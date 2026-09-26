# Hooks

## Skill against hook

| | Skill | Hook |
|---|---|---|
| Who decides | Claude, by judgment | Claude Code, by event |
| When it runs | If the task matches | Always, on the configured event |
| Can be skipped | Yes | No, unless disabled in configuration |
| Failure effect | Claude works without it | Depends on the hook; the ones here fail open |

A skill is an offer. A hook is a fact.

## SessionStart hooks

Four hooks fire when a session starts. None of them calls a model.

### 1. Repository sync check (`~/.claude/settings.json`)

Fetches one specific working repository and, if the local branch is behind its upstream,
injects a line asking Claude to propose a pull before working. Timeout 20 s. Silent when the
branch is up to date.

### 2. Graphify freshness (`~/.claude/hooks/graphify_session_start.py`)

93 lines of Python, timeout 60 s, **fails open**: any error prints nothing and exits 0.

Behaviour:

1. Resolve the git top level. Exit if not a repository, or if it is the home directory.
2. If `graphify-out/graph.json` is missing: count tracked source files across 25 extensions.
   At 15 or more, inject a reminder to run `graphify update .`, install the git hooks, and
   gitignore `graphify-out/`, including the warning to ask first if the repository could hold
   employer or confidential material.
3. If the graph exists: compare `built_at_commit` with `HEAD` and check for a dirty working
   tree, ignoring `graphify-out/` itself. If stale, run `graphify update .` (local AST, no
   model call) with a 50 s budget.
4. Report node count, refresh status, and whether the repository's own git hooks are installed.

### 3. Caveman activation (`caveman` plugin)

`node src/hooks/caveman-activate.js`, timeout 5 s. Injects the active wording mode, read from
`~/.claude/.caveman-active` (currently `full`). A `UserPromptSubmit` hook,
`caveman-mode-tracker.js`, re-asserts the mode on every prompt so it does not drift over a
long session.

### 4. i-have-adhd always-on (`i-have-adhd` plugin)

`hooks/always-on.mjs`, matcher `startup|resume|clear|compact`, timeout 30 s. Injects the full
response-structure ruleset because the flag file `~/.claude/.i-have-adhd-always` exists.
Deleting that file turns always-on off permanently.

## Status line

Not a hook, but wired the same way: `statusLine` in `~/.claude/settings.json` runs
`~/.claude/bin/caveman-statusline.sh` on each refresh, which prints the active wording mode as
a badge. The plugin's own script lives under a version-hashed cache path, so the wrapper globs
`~/.claude/plugins/cache/caveman/caveman/*/src/hooks/caveman-statusline.sh`, takes the most
recent match and execs it. If the plugin is removed or an update fails, the wrapper prints
nothing and exits 0 rather than breaking the status line.

## PreToolUse hooks

Both are Graphify guards declared in `~/.claude/settings.json` and both call the local CLI
only. No model call, no network.

| Matcher | Command | Role |
|---|---|---|
| `Bash` or `Grep` | `graphify hook-guard search` | Nudges toward a graph query before a broad blind search |
| `Read` or `Glob` | `graphify hook-guard read` | Nudges toward a graph query before opening many unrelated files |

They guard, they do not block work: exit status is 0 in the normal case.

## SessionEnd hook: session receipt

`~/.claude/hooks/session_receipt.py`, wired as a `SessionEnd` hook (timeout 10 s). When a
session closes, it reads the session transcript named in the hook input, plus the subagent
transcripts under `<session>/subagents/`, and prints a 40-column thermal-receipt summary
to the terminal device owned by the `claude` process (hooks run without a controlling
terminal, so `/dev/tty` fails with ENXIO). Fires on `/exit`, Ctrl+D and Ctrl+C, not when the
terminal window is killed. No model call, fails open (exit 0 on any error). The only network
call is the optional Telegram copy described below, made after the ticket is printed.

What the receipt shows:

| Line | Source |
|---|---|
| Models used | `message.model` of each assistant message, deduplicated by message id |
| Input, output, cache read, cache write, total | `message.usage` as returned by the API |
| Tools and skills | `tool_use` blocks; skills are `Skill` calls, counted by skill name |
| Codex, Gemini | Bash calls that invoke `codex-worker.sh --mode` / `gemini-worker.sh --mode` (or `codex exec`, `agy -`); a `grep` or `cat` on the scripts does not count |
| Jev | `mcp__jev__*` tool calls |
| Duration | first to last transcript timestamp |
| API equivalent | per-model rates, see below |
| Real billed cost | 0 $ on the subscription login; equal to the API equivalent only if `ANTHROPIC_API_KEY` is set in Claude Code's environment |
| Extra billing | `OUI` when a metered path was detected: an Anthropic API key, or Jev calls (TypeSafe, metered) |

Two costs, kept apart on purpose:

- **COUT REEL FACTURE**: what this session actually adds to a bill. Claude, Codex and Gemini
  all run on included subscriptions, so it is 0 $ unless a metered path was detected.
- **EQUIVALENT API**: what the same Claude token usage would have cost on the Claude API.
  Rates per model (input, 5 min cache write, 1 h cache write, cache read, output) are
  hard-coded from the official pricing page, checked on 2026-09-25. Fast mode, `inference_geo:
  "us"` (x1.1) and web search ($10 per 1,000) are applied when the usage block reports them.
  An unknown model id is listed as "sans tarif" instead of being guessed.

Codex and Gemini get no API equivalent. `codex-worker.sh` runs `codex exec --ephemeral` with
stdout discarded and `agy` reports no token usage, so no exact data exists to convert. The
receipt shows the delegation count and "quota non mesure".

Manual run on any transcript: `python3 ~/.claude/hooks/session_receipt.py <transcript.jsonl>`.
When prices change, update the `PRICES` table at the top of the script.

### Telegram copy of the receipt

One pipeline, one hook: the same script renders the ticket, then hands a metadata-only copy
to a dedicated Telegram bot.

```
SessionEnd
  -> session_receipt.py
       summarize(transcript)      counts only, computed once
       render()                   terminal ticket, always, first
       telegram_payload()         metrics + fallback text, JSON
       telegram_spawn()           detached child, the hook returns at once
         -> receipt_png.render_png()   thermal receipt PNG, Pillow
         -> Telegram Bot API sendPhoto, caption "Claude Orchestrator · Session complete"
         -> sendMessage (HTML text) if the PNG cannot be rendered or the photo is refused
```

Source files are versioned in [`hooks/`](../hooks/). The transcript is parsed once; the
terminal ticket, the PNG and the text message all read the same metrics dict.

#### Thermal receipt PNG

`receipt_png.py` prints a retro register slip, then turns it into a physical piece of paper.

- Print: Andale Mono (macOS, fallbacks Courier New, Menlo, Pillow default) on a 42-column
  grid, double-size lines for the title and the total, dotted leaders (`INPUT ..... 76`),
  `=`, `-` and `. . .` rules. Sections: header (CLAUDE ORCHESTRATOR, SYS, ROUTER, date,
  short session id), models, tokens, activity (top 3 tools and skills), routing (Codex,
  Gemini, Jev in bold only when used), duration, billing, footer, Code128 barcode of the
  8-character short id (python-barcode, seeded decorative fallback).
- Billing cannot be misread: "SUBTOTAL / API EQUIV" is grey and marked "reference value only,
  never charged"; "ACTUALLY CHARGED" and the double-size "TOTAL CHARGED" carry the real cost.
- Thermal ink: per-line head pressure, a head fade across the width, a few lighter glyphs, one
  or two weak heater rows, slight bleed. Rows holding key figures keep at least 93 % density.
- Paper: warm off-white, fibres, grain, whiteness drift, a few micro-marks, worn edges, cut
  top, torn bottom, uneven sides, sometimes a folded top corner showing the reverse side.
- Physical: one or two horizontal creases and sometimes a short diagonal one (raking light:
  bright flank, dark flank, thin valley), micro-waves, a bow, a tilt of a few tenths of a
  degree, a slight keystone, a soft two-layer shadow on a neutral grey backdrop (Telegram
  flattens transparency to white, so the backdrop is opaque).
- Deterministic: every variation comes from a generator seeded with the short session id.
  Same session, same bytes; another session, other creases, tilt, tear and grain.

About 900 x 2500 px, 0.5 to 0.8 s including PNG encoding, local, no network. The PNG is kept
in memory and never written to disk by the hook.

Pillow and numpy live in a dedicated venv used only by the detached child, so the terminal
ticket keeps zero dependencies:

```
uv venv ~/.claude/venvs/session-receipt --python /opt/homebrew/bin/python3
uv pip install --python ~/.claude/venvs/session-receipt/bin/python pillow numpy python-barcode
```

Without the venv the child runs on the hook's Python, the import fails and the text message is
sent instead. `CLAUDE_TELEGRAM_PHOTO=0` forces the text message.

What is sent: date and time, duration, models, request count, tokens (input, output, cache,
total), tool call count, skill names and counts, Codex and Gemini delegation counts, Jev
calls, API equivalent, real billed cost, first 8 characters of the session id. What is never
sent: prompts, replies, code, file names, paths, repository content, transcripts, secrets.

The message adapts to the session. A short session (fewer than 15 requests, no skill, no
worker) gets four lines. A longer one gets a token and cost block, the top three skills, and
an orchestration block only when Codex, Gemini or Jev actually ran (otherwise one line,
"Claude only"). HTML parse mode, `<pre>` blocks for aligned numbers, one phone screen at most.

Failure rules, Telegram can never break the ticket:

- PNG fails: text message. Telegram fails: the ticket is already printed. Everything fails:
  Claude Code still exits normally.

- The ticket is written before anything Telegram related runs.
- Sending happens in a detached child (`start_new_session`), so Claude Code closes at once,
  online or not.
- 5 s timeout, one retry on a network error only, no retry on an HTTP error (bad token,
  blocked bot, bad chat id).
- Errors go to `~/.claude/logs/session-ticket-error.log` as a class or HTTP code only, never
  the token, the chat id or the message.
- Token or chat id missing: nothing is sent, nothing is logged.
- Kill switch: `CLAUDE_TELEGRAM_RECEIPT=0` in the environment.

Credentials live in the macOS Keychain only and are read at send time:

| Keychain service | Content |
|---|---|
| `CLAUDE_TELEGRAM_BOT_TOKEN` | bot token from @BotFather |
| `CLAUDE_TELEGRAM_CHAT_ID` | private chat id of the owner |

Setup:

1. Create the bot with @BotFather (`/newbot`).
2. Store the token from a separate terminal, masked prompt, nothing in shell history:
   `security add-generic-password -U -a "$USER" -s CLAUDE_TELEGRAM_BOT_TOKEN -w`
3. Send `/start` to the bot, then run
   `python3 ~/.claude/hooks/session_receipt.py --telegram-setup-chat`. It calls `getUpdates`
   once, keeps only the private chat id and writes it to the Keychain through `security -i`
   (the value never appears in a process argument list).
4. Check: `python3 ~/.claude/hooks/session_receipt.py --telegram-test` prints
   `TELEGRAM: PASS`.
5. Preview without sending: `--telegram-preview <transcript.jsonl>` prints the text message;
   `~/.claude/venvs/session-receipt/bin/python ~/.claude/hooks/session_receipt.py
   --png-preview <transcript.jsonl> <out.png>` writes the PNG.

## Per-repository git hooks

Separate from Claude Code. `graphify hook install` writes `post-commit` and `post-checkout`
hooks in a work repository so the graph rebuilds in the background after a commit or a branch
switch. AST only, no model call. Log at `~/.cache/graphify-rebuild.log`.

## What no hook does

- No hook calls Codex, Gemini, Jev or any model API. The one outbound call is the
  best-effort Telegram copy of the session receipt, metadata only.
- No hook writes to a work repository beyond the Graphify output directory.
- No hook blocks a tool call outright.
