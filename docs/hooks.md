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

## Per-repository git hooks

Separate from Claude Code. `graphify hook install` writes `post-commit` and `post-checkout`
hooks in a work repository so the graph rebuilds in the background after a commit or a branch
switch. AST only, no model call. Log at `~/.cache/graphify-rebuild.log`.

## What no hook does

- No hook calls Codex, Gemini, Jev or any API.
- No hook writes to a work repository beyond the Graphify output directory.
- No hook blocks a tool call outright.
