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

Three guards declared in `~/.claude/settings.json`. All run locally. No model call, no network.

| Matcher | Command | Role |
|---|---|---|
| `Bash` or `Grep` | `graphify hook-guard search` | Nudges toward a graph query before a broad blind search |
| `Read` or `Glob` | `graphify hook-guard read` | Nudges toward a graph query before opening many unrelated files |
| `*` | `~/.claude/hooks/repeat_tool_guard.py` | Catches the same tool called with the same arguments over and over |

The two Graphify guards only nudge: exit status is 0 in the normal case. The repeat guard
is the one hook that can refuse a call, and only after eight identical calls in a row.

### Repeat tool guard (`~/.claude/hooks/repeat_tool_guard.py`)

Idea taken from the `repeat-tool-reminder` guard of DeepSeek Harness, reimplemented from
scratch as a Claude Code hook. Source versioned in [`hooks/`](../hooks/repeat_tool_guard.py).
Wired twice, timeout 5 s each: `PreToolUse` with matcher `*`, and `UserPromptSubmit`.

A chain is a run of consecutive calls with the same tool and the same normalized arguments,
counted per session and per agent (`agent_id` is set by Claude Code inside a subagent, so
subagents never add to the main chain). Any different call breaks the chain.

| Identical calls in a row | Effect |
|---|---|
| 1, 2 | Nothing |
| 3 | `additionalContext` to Claude: « Tu répètes la même action. Vérifie si le résultat change réellement. » |
| 5 | `additionalContext`: « Cette approche semble bloquée. Change de stratégie avant de rappeler cet outil. » |
| 8 and more | `permissionDecision: deny` with the reason; Claude sees it as a refused call |

Normalization: display-only fields (`description`, `statusMessage`) are dropped and keys are
sorted. A Bash command also loses runs of spaces and a trailing `;`, which the shell ignores.
Every other string is compared exactly, so a different file, offset, pattern, test input or
timeout is a different call.

Not counted, never blocked:

- Pagination, other files, other test inputs, any argument that really changes.
- Tools whose repetition is their purpose: `Monitor`, `ScheduleWakeup`, `TaskOutput`,
  `TaskStop`, `TaskList`, `TaskGet`, `CronList`, `ListAgents`, `AskUserQuestion`, plan mode
  tools, `TodoWrite`. They also break a chain.
- A Bash command ending with `# repeat-ok`, a deliberate per-call exemption.
- Polling the person asked for: when the last prompt contains a polling word (poll, surveille,
  attends, wait, until, jusqu'à, toutes les, every N, boucle, loop, répète, retry, réessaie),
  the guard still reminds and warns but never denies.

Resets: every new prompt resets all chains of the session; a 30 minute pause breaks a chain.

State: `~/.claude/state/repeat-guard/<session>.json`, a few hundred bytes (hash, count, time
per agent). Never in a repository. Bounded three ways: at most 32 chains per file, files older
than 24 h deleted, at most 200 files kept. One lock file for the directory serializes parallel
tool calls. Fails open: invalid input, a full disk or any exception allows the call silently.
Kill switch: `CLAUDE_REPEAT_GUARD=0` in the environment.

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
| Worker outcomes | The `ai-worker:` status line each wrapper prints last on stderr, read from the Bash tool result, see [Worker telemetry](#worker-telemetry) |
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

Codex and Gemini get no API equivalent: both run on subscriptions with no public per-token
price. Their token counts are shown as the workers report them, never converted to dollars.

### Worker telemetry

Idea taken from DeepSeek Harness's separate operational event channel, reimplemented with no
log file and no database: the data travels in the transcript Claude Code already writes.

Each wrapper (`bin/codex-worker.sh`, `bin/gemini-worker.sh`, live copies in `~/.claude/bin/`)
prints one last line on stderr on every exit path, including argument errors and the billing
guard:

```
ai-worker: codex mode=review status=ok exit=0 tokens=in:11924,cache:11648,out:5,think:0
ai-worker: gemini mode=analyze status=timeout exit=3 tokens=unavailable
```

`status` follows the exit code: 0 `ok`, 3 `timeout`, 4 `unavailable` (not installed or billing
guard), 5 `scope` (Codex wrote outside its `--scope`, shown as SCOPE), anything else `fail`. Exit codes are unchanged. `tokens` is copied from what the CLI
reports, never estimated: the sum of `turn.completed.usage` events of `codex exec --json`
(`input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`), and the
`usage` of agy's `result` event (`input_tokens`, `cache_read_tokens`, `output_tokens`,
`thinking_tokens`). When the CLI reports nothing, the value is `unavailable`. agy reports zeros
for a turn cut by its own timeout; the receipt shows those zeros as reported.

The receipt reads these lines from the Bash tool result of the call:

- A run whose line is absent is **UNKNOWN**, never guessed: stderr sent to a file or
  `/dev/null`, a raw `codex exec` or `agy` call, a session cut before the result, or any
  transcript older than this telemetry.
- Only whole lines count, matched per worker in command order. One run with several candidate
  lines takes the last one (stderr follows the model's answer, which may quote such a line).
  When the number of lines and runs of a worker in one command disagree, those runs stay UNKNOWN.
- **Retry:** a run that follows a failed or timed-out run of the same worker in the same prompt turn.
- **Fallback to Claude:** a prompt turn whose last worker run failed. Nothing ran after it,
  so Claude finished the task. Another worker taking over is not counted as a Claude fallback.
- Subagent transcripts count in the outcomes but not in retries and fallbacks, which are per
  prompt turn of the main transcript.

What it looks like. A clean session:

```
WORKERS
  Codex                             2 OK
  Gemini                            1 OK
  Jev                                  0
  tok Codex             1,2 k in/350 out
```

A session with a problem adds `RETRY` and `FALLBACK` lines only when non-zero, puts a red
`🔴 Workers : Codex 1 FAIL · fallback Claude` line at the top of the Telegram message, and a
red `INCIDENT` line in the brigade section of the PNG. `(partiel)` after a token count means
some runs of that worker reported no usage.

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
total), tool call count, skill names and counts, Codex and Gemini delegation counts and
outcomes (OK, FAIL, TIMEOUT, UNKNOWN, retries, fallbacks, reported token counts), Jev calls, API equivalent, real billed cost, first 8 characters of the session id. What is never
sent: prompts, replies, code, file names, paths, repository content, transcripts, secrets.

The message adapts to the session. A short session (fewer than 15 requests, no skill, no
worker) gets four lines. A longer one gets a token and cost block, the top three skills, and
an orchestration block only when Codex, Gemini or Jev actually ran (otherwise one line,
"Claude only"). HTML parse mode, `<pre>` blocks for aligned numbers, one phone screen at most.

When it is sent: only when a person leaves an interactive session (`/exit`, Ctrl+D, Ctrl+C,
logout). Headless sessions (`claude -p`, SDK, scripts: `CLAUDE_CODE_ENTRYPOINT` or the
transcript `entrypoint` starts with `sdk`) and `/clear` or `/resume` (the terminal stays open on
a new session) still print the terminal ticket but send nothing. SessionEnd never fires after a
single reply.

Failure rules, Telegram can never break the ticket:

- PNG fails: text message. Telegram fails: the ticket is already printed. Everything fails:
  Claude Code still exits normally.

- The ticket is written before anything Telegram related runs.
- Sending happens in a detached child (`start_new_session`), so Claude Code closes at once,
  online or not.
- 5 s timeout, one retry on a network error only, no retry on an HTTP error (bad token,
  blocked bot, bad chat id).
- Still offline after that (a proxy blocking `api.telegram.org`, no network): the payload,
  metrics only, is kept in `~/.claude/state/receipt-queue/`. The next interactive session end
  sends the queue oldest first, then its own ticket. 20 tickets at most, a week at most. Each
  file is claimed by an atomic rename, so two sessions ending together never send one twice.
- The queue also empties without a session end: a launchd agent
  ([`examples/com.claude.receipt-flush.plist`](../examples/com.claude.receipt-flush.plist))
  runs `session_receipt.py --flush` on every network change and every 10 min. An empty queue
  exits at once; a network that still blocks Telegram (Zscaler) is retried silently. A claim
  older than 15 min (sender killed by sleep or shutdown) goes back to the queue.
  Remove: `launchctl bootout gui/$(id -u)/com.claude.receipt-flush`.
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
- No hook blocks a tool call outright, except the repeat guard after eight identical calls
  in a row, and only when the last prompt did not ask for polling.
