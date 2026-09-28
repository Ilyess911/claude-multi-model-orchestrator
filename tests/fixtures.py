"""Synthetic, anonymized session transcripts for the receipt tests.

Transcripts are built in code and written to a temporary directory at test time, so no
.jsonl file is ever committed (the repository ignores *.jsonl on purpose). Every prompt,
path and id below is invented. No real transcript, prompt, path or credential is used.
"""
import json
import os
from datetime import datetime, timedelta, timezone

MODEL = "claude-opus-5-5"
T0 = "2026-01-01T10:00:00.000Z"


def ts(sec):
    t = datetime(2026, 1, 1, 10, tzinfo=timezone.utc) + timedelta(seconds=sec)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def prompt(text, sec):
    return {"type": "user", "timestamp": ts(sec), "message": {"role": "user", "content": text}}


def tool_result(sec, tool_id="t0"):
    return {"type": "user", "timestamp": ts(sec), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}


def usage(inp=100, out=50, cr=0, c5=0, c1=0, **extra):
    u = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
         "cache_creation_input_tokens": c5 + c1,
         "cache_creation": {"ephemeral_5m_input_tokens": c5, "ephemeral_1h_input_tokens": c1}}
    u.update(extra)
    return u


def assistant(mid, sec, tools=(), model=MODEL, **u):
    content = [{"type": "text", "text": "synthetic reply"}]
    for i, (name, inp) in enumerate(tools):
        content.append({"type": "tool_use", "id": f"{mid}-tool{i}", "name": name, "input": inp})
    return {"type": "assistant", "timestamp": ts(sec),
            "message": {"id": mid, "model": model, "role": "assistant", "content": content,
                        "usage": usage(**u)}}


def bash(cmd):
    return ("Bash", {"command": cmd, "description": "synthetic"})


CODEX = "printf 'x' | ~/.claude/bin/codex-worker.sh --mode {} --cd /work/demo"
GEMINI = "printf 'x' | ~/.claude/bin/gemini-worker.sh --mode {} --cd /work/demo"

# name -> (main transcript lines, {subagent file name: lines})
FIXTURES = {
    # One prompt, Claude only. The first message is streamed twice with the same id:
    # usage must be counted once.
    "direct": ([
        {"type": "system", "timestamp": T0, "entrypoint": "cli"},
        prompt("Rename the demo variable", 0),
        assistant("msg_a1", 5, tools=[("Read", {"file_path": "/work/demo/app.py"})],
                  inp=1000, out=200, cr=5000, c5=300),
        assistant("msg_a1", 5, tools=[("Read", {"file_path": "/work/demo/app.py"})],
                  inp=1000, out=200, cr=5000, c5=300),
        tool_result(6, "msg_a1-tool0"),
        assistant("msg_a2", 65, tools=[("Skill", {"skill": "demo-skill"})], inp=10, out=40, cr=6000),
    ], {}),
    # A Codex implementation, plus a grep on the worker script that must not count.
    "codex": ([
        prompt("Implement the demo feature", 0),
        assistant("msg_c1", 10, tools=[bash("grep -n mode ~/.claude/bin/codex-worker.sh")]),
        assistant("msg_c2", 20, tools=[bash(CODEX.format("implement"))]),
    ], {}),
    "gemini": ([
        prompt("Summarize the demo documents", 0),
        assistant("msg_g1", 30, tools=[bash(GEMINI.format("analyze"))]),
    ], {}),
    # Any worker in review mode makes the turn a cross review.
    "cross_review": ([
        prompt("Review the demo auth change", 0),
        assistant("msg_r1", 30, tools=[bash(CODEX.format("review"))]),
    ], {}),
    "parallel": ([
        prompt("Audit the demo repository", 0),
        assistant("msg_p1", 30, tools=[bash(CODEX.format("analyze")), bash(GEMINI.format("analyze"))]),
    ], {}),
    # Three prompts (DIRECT, CODEX, REVIEW) among lines that are not prompts, and a subagent
    # transcript whose usage and Jev call count in totals but not in routing.
    "mixed": ([
        {"type": "user", "isMeta": True, "timestamp": ts(0), "message": {"content": "meta line"}},
        prompt("<command-name>/demo</command-name>", 1),
        prompt("First demo task", 2),
        assistant("msg_m1", 10),
        prompt("Second demo task", 20),
        assistant("msg_m2", 30, tools=[bash(CODEX.format("implement"))]),
        tool_result(31, "msg_m2-tool0"),
        {"type": "user", "isCompactSummary": True, "timestamp": ts(40),
         "message": {"content": "compacted summary"}},
        prompt("Third demo task", 50),
        assistant("msg_m3", 3600, tools=[bash(CODEX.format("review"))]),
    ], {"agent-demo.jsonl": [
        {"type": "user", "isSidechain": True, "timestamp": ts(11), "message": {"content": "sub task"}},
        assistant("msg_s1", 12, tools=[("mcp__jev__jev_check", {"q": "demo"})],
                  model="claude-haiku-4-5-20251001", inp=500, out=100),
    ]}),
    # Pricing edge cases: fast mode, US inference, web search, 1h cache, an unknown model,
    # and a synthetic message that must be ignored.
    "pricing": ([
        prompt("Price demo", 0),
        assistant("msg_x1", 5, inp=1_000_000, out=100_000, speed="fast"),
        assistant("msg_x2", 6, inp=1_000_000, c1=1_000_000, inference_geo="us",
                  server_tool_use={"web_search_requests": 3}),
        assistant("msg_x3", 7, model="demo-model-unknown", inp=10, out=10),
        assistant("msg_x4", 8, model="<synthetic>", inp=999, out=999),
    ], {}),
}


def write_all(root):
    """Write every fixture under root, return {name: transcript path}."""
    paths = {}
    for name, (main, subs) in FIXTURES.items():
        path = os.path.join(root, f"{name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(l) + "\n" for l in main)
        if subs:
            sd = os.path.join(root, name, "subagents")
            os.makedirs(sd)
            for fn, lines in subs.items():
                with open(os.path.join(sd, fn), "w", encoding="utf-8") as f:
                    f.writelines(json.dumps(l) + "\n" for l in lines)
        paths[name] = path
    return paths
