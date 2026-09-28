# Workers

Status verified on the machine on 2026-09-22 with `~/.claude/bin/ai-status.sh` (local checks,
no model calls).

| Worker | Available | Auth | Billing class | Version |
|---|---|---|---|---|
| Claude | yes | subscription | INCLUDED_SUBSCRIPTION | Claude Code 2.1.278 |
| Codex | yes | ChatGPT account login | INCLUDED_SUBSCRIPTION | codex-cli 0.144.5 |
| Gemini | yes | Google account in the macOS Keychain | INCLUDED_SUBSCRIPTION | agy 1.2.8 |
| Jev | no | no key in Keychain | METERED_LOW_COST, inactive | local build present |

No account identifier, token or key appears in this repository or in the probe output.

## Claude

Primary orchestrator and the only engine with the full task context. Owns intent, planning,
repository state, conflict resolution, final verification and the answer itself.

Best placed for reasoning, architecture, debugging that depends on session history, judgment
calls, and anything touching several parts of a task at once. A worker result is always
evaluated by Claude before it changes anything.

## Codex

Invocation, prompt on stdin:

```sh
printf '<structured prompt>' | ~/.claude/bin/codex-worker.sh \
  --mode review|analyze|implement --cd <dir> [--timeout 300] [--model M] [--schema FILE] \
  [--scope PATH]...
```

Mechanism and guards implemented in the wrapper:

- `codex exec` non-interactive, always `--ephemeral` so no session files accumulate.
- `--mode review|analyze` runs sandbox `read-only`: Codex cannot modify the repository.
- `--mode implement` grants `workspace-write`, used only for a bounded implementation subtask.
- Billing guard: the wrapper reads `auth_mode` from `~/.codex/auth.json` and refuses to run
  unless it is `chatgpt`. An API key login exits 4 instead of spending money.
- Working directory pinned with `-C`, final message captured with `-o`, optional JSON Schema
  output with `--output-schema`.
- Timeout via `perl alarm` because macOS ships no coreutils `timeout`.
- Exit codes: 0 result on stdout, 1 worker error, 2 bad usage, 3 timeout, 4 unavailable or
  blocked by the billing guard, 5 change outside `--scope` ([Write scope](#write-scope)). A
  timeout exits 3 even when codex left a partial answer.
- Last stderr line on every exit: `ai-worker: codex mode=… status=… exit=… tokens=…`, with the
  token usage summed from `codex exec --json` events. Read by the session receipt, see
  [hooks.md](hooks.md#worker-telemetry).

### Write scope

Idea taken from the advisory `writeScopes` of DeepSeek Harness agent teams, reimplemented as a
check that is verified after the run instead of declared.

```sh
printf '<prompt>' | ~/.claude/bin/codex-worker.sh --mode implement --cd <repo> \
  --scope src/parser.py --scope tests/ --scope 'docs/**/*.md'
```

- A scope is relative to the repository root: an exact file, a directory (everything below
  it) or a glob. `*` and `?` stay inside one directory level, `**` crosses levels.
- Before the run, `scope-check.py` (next to the wrapper) records HEAD and, for every path git
  reports as changed or untracked, its status, a hash of the work-tree bytes and the staged
  blob id. After the run, whatever codex's outcome, it compares again.
- A path whose entry appeared, disappeared or changed outside the scope is a violation, and
  so is a moved HEAD (commit, reset, checkout). Work that was already uncommitted before the
  run is compared by content, so it is never blamed on Codex, and an edit to it is caught.
- On a violation the answer is followed by a `[scope]` block listing each path and what
  happened (modified, new file, deleted, restored or removed, staged content changed), then
  the allowed scopes. Exit 5, status line `status=scope`, even when codex also failed or timed
  out. **Nothing is reverted**: Claude reads each listed file with `git diff` and decides.
- `--scope` outside a git repository is refused (exit 2) rather than run unchecked. Without
  `--scope`, behaviour is unchanged. Review and analyze modes ignore it: they cannot write.
- Not visible, so not checked: gitignored files and paths outside the repository. The
  workspace-write sandbox already confines Codex to the working directory and temp dirs.

Good fit: isolatable implementation against a crisp spec, focused refactoring, writing tests,
bounded engineering analysis, independent review of something Claude wrote. Weak fit: anything
needing the conversation's context, or open-ended product judgment.

## Gemini

Invocation, prompt on stdin:

```sh
printf '<structured prompt>' | ~/.claude/bin/gemini-worker.sh \
  --mode analyze|review --cd <dir> [--timeout 300] [--model M] [--json]
```

Backed by the **Antigravity CLI (`agy`)**, not the retired hosted `gemini` CLI. Google stopped
serving `gemini` for personal accounts on 2026-06-18 for free, AI Pro and AI Ultra alike; the
old 0.60.0 binary is still installed and only ever returns a tier error. Nothing routes to it.

Reliability work implemented in the wrapper:

- **Structured result parsing.** Success is read from the `result` event's `status` field in
  the NDJSON stream, never by searching the model's own text. A response that merely quotes
  `"status":"ERROR"` is still a success.
- **Unique `mktemp` per invocation.** Request, response and error files use
  `mktemp -t ...XXXXXX`, so concurrent workers never share a path.
- **Cleanup trap.** `trap cleanup EXIT HUP INT TERM` removes all three files on success,
  failure and interrupt alike.
- **stdin and NDJSON instead of a giant argv.** The prompt is encoded by Python into one
  NDJSON `user` event and fed through `agy --input-format stream-json`, so prompt size is
  bounded by memory and not by `ARG_MAX`, and no prompt content is interpreted by the shell.
- **macOS and BSD safe parsing.** No GNU-only flags, no coreutils `timeout`; the outer guard
  is `perl alarm` at `timeout + 30s` in case `agy` ignores its own deadline. Parsing is done
  in Python, not with BSD `sed` or `grep -P`.
- **Subscription-auth guard.** The wrapper refuses to run if `GEMINI_API_KEY` or
  `GOOGLE_API_KEY` is set, exiting 4 rather than reaching a metered API.
- Last stderr line on every exit: `ai-worker: gemini mode=… status=… exit=… tokens=…`, with the
  token usage of agy's `result` event. agy's own `--print-timeout` now exits 3, not 1. See
  [hooks.md](hooks.md#worker-telemetry).
- **Read only by design.** `~/.gemini/antigravity-cli/settings.json` allows `read_file`,
  `list_directory` and `grep_search` and denies `write_file` and `command`.
  `--dangerously-skip-permissions` is never passed.

Good fit: large-context analysis, repository-wide reading, documentation analysis, second
opinions, independent review of work another model produced. `agy` also exposes `claude-*` and
`gpt-oss-*` models; routing never uses those, since a Claude model reached through `agy` is
neither independent from Claude nor free of Google's quota.

## Jev

Current status: **prepared, not active.**

- Built locally at `~/.claude/vendor/jev-code`, registered as the `jev` MCP server through
  `~/.claude/vendor/jev-code/bin/jev-mcp.sh`.
- The launcher reads `TYPESAFE_API_KEY` from the macOS Keychain at start, so no key is ever
  stored in `~/.claude.json`, a dotfile, or a repository. If the key is absent the server
  still starts and `jev_*` calls fail with a clear "not set" error instead of dying.
- No key exists today, so the status probe reports `jev available: false`. Signups were closed
  when the setup was built.

Intended future role, once a key exists: bounded semantic judgment only. Classification among
a small fixed set, yes/no checks with a calibrated probability, scoring on an explicit rubric,
ranking candidates, and repetitive triage over many items. Tools: `jev_classify`, `jev_check`,
`jev_score`, `jev_rank`, `jev_ask`.

Jev is **not required infrastructure**. It never gates a delegation, never becomes a mandatory
step, and its absence never changes a route. Obvious routing decisions stay deterministic and
would not consult it even when it is available.

## Known documentation drift

`~/.claude/skills/ai-router/billing-policy.md` still classifies Gemini as
"INCLUDED_SUBSCRIPTION (blocked by tier)", which described the retired `gemini` CLI.
`capabilities.md` and the live probe both report `agy` as available on the Google account.
The probe is the source of truth; the billing table entry is stale wording, not a live risk,
since both paths forbid a metered API.
