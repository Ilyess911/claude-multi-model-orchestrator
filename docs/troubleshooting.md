# Troubleshooting

First command in every case:

```sh
~/.claude/bin/ai-status.sh          # local checks, zero model calls
~/.claude/bin/ai-status.sh --live   # one tiny call per worker, only when auth is suspect
```

## Worker failures

| Symptom | Cause | What happens | Fix |
|---|---|---|---|
| `codex-worker: refusing to run, auth_mode is 'apikey'` | Codex logged in with an API key | exit 4, no spend, route falls back to Claude or Gemini | `codex login` with the ChatGPT account |
| `codex-worker: codex not installed` | binary missing from PATH | exit 4, routing continues without Codex | reinstall the Codex CLI |
| `codex-worker: timed out after Ns` | model slower than the budget | exit 3, Claude takes the subtask back | raise `--timeout`, or shrink the scope |
| `gemini-worker: refusing to run, GEMINI_API_KEY is set` | API key in the environment | exit 4, billing guard | unset the variable; do not add a key to make it work |
| `gemini-worker: agy not installed` | Antigravity CLI missing | exit 4 | install `agy`, then re-run the status probe |
| `gemini-worker: no result event from agy` | stream ended without a structured result | exit 1 | re-run once; if it repeats, route to Codex or Claude |
| `IneligibleTierError` | the retired hosted `gemini` CLI was called | nothing routes there by design | use `agy`; the old binary stays unused |
| `jev available: false` | no TypeSafe key in the Keychain | normal state, costs nothing, no route changes | add a key only if Jev is actually wanted |
| `TYPESAFE_API_KEY is not set` on a `jev_*` call | MCP server started without the key | tool call fails cleanly, server stays up | same as above |

Rule in all cases: **a worker failing never blocks the task.** Claude finishes it.

## Loop and runaway protection

- Two delegated attempts maximum on the same unresolved subtask, and the second must use a
  different strategy.
- Delegation depth one: a worker never triggers another delegation.
- No bouncing a subtask between workers.
- If the same failure appears three turns in a row, the rule is to stop iterating on code and
  name the assumption that might be wrong instead.

## Graphify failures

| Symptom | Fix |
|---|---|
| `graphify` not found | check `~/.local/bin/graphify` and `uv tool list` (package `graphifyy`) |
| Graph stale or refuses to refresh | `graphify update . --force` |
| Corrupted output | delete `graphify-out/` and rebuild |
| Background rebuild silent | read `~/.cache/graphify-rebuild.log`, check `graphify hook status` |
| Hook prints nothing at session start | it fails open by design; run `graphify update .` by hand |
| A subsystem is invisible in the graph | expected for notebooks, config semantics, registry dispatch, injected collaborators, pub/sub, file handoffs, pipeline order; fall back to source and say so |

## Router failures

| Symptom | Fix |
|---|---|
| Delegation happening when it should not | set `enabled: false` or a worker to `false` in `~/.claude/ai-router.json` |
| A worker stays "available" but always fails | run `--live`; a failed Gemini probe writes `~/.claude/.ai-router-gemini-ineligible` |
| Routing feels wrong for a class of task | edit `~/.claude/skills/ai-router/capabilities.md`; the policy follows without being rewritten |

## Presentation failures

| Symptom | Fix |
|---|---|
| Wording mode drifts mid-session | the `UserPromptSubmit` tracker re-asserts it; `/caveman lite\|full\|ultra` sets the level |
| Response structure lost | say "stop adhd mode" to disable for a session, or delete `~/.claude/.i-have-adhd-always` to disable always-on permanently |
| Statusline badge missing | check `statusLine` in `settings.json` points at `~/.claude/bin/caveman-statusline.sh`, then run it by hand: it must print a badge and exit 0. An empty result means no plugin copy matched the glob, so reinstall the plugin |
