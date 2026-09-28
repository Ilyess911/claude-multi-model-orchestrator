#!/usr/bin/env python3
"""PreToolUse / UserPromptSubmit hook: catch Claude calling the same tool with the same
arguments over and over.

Consecutive identical calls (same tool, same normalized arguments, same agent) form a
chain. At 3 the model gets a soft reminder, at 5 a strong warning, from 8 on the call is
denied until the arguments change or the person types a new prompt. Silent otherwise.
When the person's last prompt asks for polling or waiting, the guard still reminds and
warns but never denies.

Local, deterministic, no model, no network. Fails open: any error allows the call.
State: one small JSON file per session under ~/.claude/state/repeat-guard/, reset on each
prompt of that session, pruned after 24 h, capped in count and size.

Disable: CLAUDE_REPEAT_GUARD=0. Exempt one Bash call on purpose: end it with `# repeat-ok`.
"""
import fcntl
import hashlib
import json
import os
import re
import sys
import time

REMIND, WARN, BLOCK = 3, 5, 8
MAX_AGE = 24 * 3600      # stale session files are deleted
MAX_FILES = 200          # oldest session files beyond this are deleted
MAX_AGENTS = 32          # chains kept per session file (main + subagents)
CHAIN_GAP = 30 * 60      # a pause this long breaks a chain

# Tools whose repetition is their purpose (waiting, polling, asking) or is harmless.
EXEMPT = {"ScheduleWakeup", "Monitor", "TaskOutput", "TaskStop", "TaskList", "TaskGet",
          "CronList", "ListAgents", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
          "TodoWrite"}
# Display-only input fields: they never change what the call does.
IGNORED_KEYS = {"description", "statusMessage"}
# A prompt asking for repetition on purpose (FR and EN). Matching only lifts the deny.
POLL_RE = re.compile(
    r"\b(poll\w*|surveill\w*|monitor\w*|watch\w*|attends?|wait\w*|until|jusqu'?\s?(à|a)\b|"
    r"toutes les|every \d|chaque \d|boucle|loop|r[ée]p[èe]te\w*|repeat\w*|retry|r[ée]essa\w*)\b",
    re.IGNORECASE)

MSG_REMIND = ("Repeat guard: this is the 3rd identical {tool} call in a row. "
              "Tu répètes la même action. Vérifie si le résultat change réellement.")
MSG_WARN = ("Repeat guard: 5 identical {tool} calls in a row. "
            "Cette approche semble bloquée. Change de stratégie avant de rappeler cet outil.")
MSG_BLOCK = ("Repeat guard: blocked, {n} identical {tool} calls in a row. "
             "Change the arguments or the approach. If the person asked for polling, use "
             "Monitor, ScheduleWakeup or /loop instead of repeating the call.")


def state_dir():
    return os.environ.get("REPEAT_GUARD_STATE_DIR") or os.path.expanduser(
        "~/.claude/state/repeat-guard")


def norm(v):
    """Canonical value: display-only keys dropped, keys sorted, strings kept exact."""
    if isinstance(v, dict):
        return {k: norm(x) for k, x in sorted(v.items()) if k not in IGNORED_KEYS}
    if isinstance(v, list):
        return [norm(x) for x in v]
    return v


def call_key(tool, tool_input):
    """Short hash of tool + normalized arguments.

    Only a Bash command is cleaned further (outer whitespace, runs of spaces, trailing `;`),
    since the shell ignores those; every other string is compared exactly.
    """
    inp = norm(tool_input if isinstance(tool_input, dict) else {"_": tool_input})
    if tool == "Bash" and isinstance(inp.get("command"), str):
        inp["command"] = re.sub(r"[ \t]+", " ", inp["command"]).strip().rstrip("; ")
    raw = json.dumps([tool, inp], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def exempt(tool, tool_input):
    if tool in EXEMPT:
        return True
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    return tool == "Bash" and isinstance(cmd, str) and cmd.rstrip().endswith("# repeat-ok")


def safe_name(session_id):
    return re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "nosession")[:80]


def prune(d, now):
    """Bound the state directory: drop stale files, then the oldest beyond MAX_FILES."""
    try:
        files = [(os.path.getmtime(p), p) for p in
                 (os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))]
    except OSError:
        return
    files.sort()
    for i, (mt, p) in enumerate(files):
        if now - mt > MAX_AGE or i < len(files) - MAX_FILES:
            try:
                os.remove(p)
            except OSError:
                pass


def decide(count, tool, polling=False):
    """Hook output for a chain of `count` identical calls, or None to stay silent."""
    if count >= BLOCK and not polling:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": MSG_BLOCK.format(n=count, tool=tool)}}
    msg = {REMIND: MSG_REMIND, WARN: MSG_WARN}.get(count)
    if msg:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "additionalContext": msg.format(tool=tool)}}
    return None


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("chains"), dict):
            return st
    except (OSError, ValueError):
        pass
    return None


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, path)


def handle(data, now=None):
    """Process one hook input, return the JSON-able output or None."""
    now = time.time() if now is None else now
    d = state_dir()
    path = os.path.join(d, safe_name(data.get("session_id")) + ".json")
    event = data.get("hook_event_name")
    if event not in ("PreToolUse", "UserPromptSubmit"):
        return None
    os.makedirs(d, mode=0o700, exist_ok=True)

    # One lock for the directory, so no lock file per session is left behind.
    with open(os.path.join(d, ".lock"), "w") as lock:  # parallel calls fire hooks concurrently
        fcntl.flock(lock, fcntl.LOCK_EX)
        st = load_state(path)
        if st is None:
            prune(d, now)  # once per new session file, not on every call
            st = {"poll": False, "chains": {}}

        if event == "UserPromptSubmit":  # a new request resets every chain
            polling = bool(POLL_RE.search(str(data.get("prompt") or "")))
            save_state(path, {"poll": polling, "chains": {}})
            return None

        tool = data.get("tool_name") or "?"
        tool_input = data.get("tool_input") or {}
        agent = data.get("agent_id") or "main"  # set by Claude Code inside a subagent
        chains = st["chains"]
        if exempt(tool, tool_input):
            chains.pop(agent, None)  # an exempt call still breaks a chain
            out = None
        else:
            key = call_key(tool, tool_input)
            prev = chains.pop(agent, None) or {}
            same = prev.get("k") == key and now - prev.get("t", 0) <= CHAIN_GAP
            count = prev.get("n", 0) + 1 if same else 1
            chains[agent] = {"k": key, "n": count, "t": now}
            out = decide(count, tool, bool(st.get("poll")))

        while len(chains) > MAX_AGENTS:  # dicts keep insertion order: drop the least recent
            chains.pop(next(iter(chains)))
        save_state(path, st)
    return out


def main():
    if os.environ.get("CLAUDE_REPEAT_GUARD", "1") == "0":
        return
    try:
        data = json.load(sys.stdin)
        out = handle(data)
    except Exception:
        return  # fail open
    if out:
        sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
    sys.exit(0)
