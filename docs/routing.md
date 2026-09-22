# Routing

Source of truth on the machine: `~/.claude/skills/ai-router/routing-policy.md`.

## Philosophy

The objective is not to use every available model. It is to use **the smallest appropriate
combination of tools and models that produces a reliable result**. A three line task stays a
three line task.

## Hierarchy

Stop at the first level that fits.

1. **Deterministic tool.** Filesystem, git, ripgrep, compiler, linter, tests, package manager,
   Graphify. If one of these answers, nothing else runs.
2. **Claude directly.** Default for anything obvious, small, or needing real reasoning.
3. **A worker**, only when delegation has a concrete benefit.
4. **Multi-model review**, only when the change justifies it.
5. **Jev**, when available, for cheap bounded classification inside the levels above.

A delegation must plausibly buy better quality, real parallelism, genuine independence, or
saved Claude context. If framing the subtask costs more than doing it, Claude does it.

## Modes

| Mode | Shape |
|---|---|
| DIRECT | Claude does it. The common case. |
| DELEGATE | One bounded subtask, one worker, contract specified by Claude. |
| IMPLEMENT + REVIEW | One model implements, a different one reviews, Claude integrates. |
| PARALLEL ANALYSIS | Two models analyze the same question independently, Claude synthesizes. Reserved for ambiguous or consequential problems. |

In IMPLEMENT + REVIEW the reviewer gets the original requirements, the actual diff and the
constraints. It is never told what the implementer concluded and never asked to confirm a
verdict.

## Prompt contract sent to a worker

```
OBJECTIVE        what outcome is wanted
SCOPE            what is in and out of bounds
RELEVANT FILES   paths, plus the excerpts that matter
CONSTRAINTS      style, stack, invariants, what must not change
ALLOWED ACTIONS  read only, or which files may be edited
EXPECTED OUTPUT  the exact shape wanted back
```

Minimum context. Never the conversation, never a whole repository. Locate the subsystem first
with Graphify, ripgrep or git, then send only what matters.

## Signals weighed

Task type, complexity, context size, implementation intensity, reasoning depth, value of
independence, UI requirements, debugging requirements, parallelizability, latency tolerance,
which workers are available right now, rate limit pressure seen in this session, failures
already seen on this task. No fixed weights.

## Fallback

| Situation | Route |
|---|---|
| Codex unavailable, timed out, rate limited | Claude, or Gemini if it fits |
| Gemini unavailable, timed out, rate limited | Claude, or Codex if it fits |
| Jev unavailable | Claude routes. Normal state today, costs nothing |
| Malformed worker output | Claude evaluates; one retry only with a new strategy |
| Everything unavailable | Claude does the whole task |

The router failing never blocks work.

## Loop protection

- At most **two** delegated attempts on the same unresolved subtask. The second requires a
  genuinely different strategy, not the same prompt again. Then Claude finishes it.
- Delegation **depth is one**. A worker's output returns to Claude; a worker never triggers
  another delegation.
- No ping-pong: not Claude to Codex to Claude to Codex, not Codex to Gemini to Codex.

## Kill switches

`~/.claude/ai-router.json`:

```json
{ "enabled": true, "workers": { "codex": true, "gemini": true, "jev": true } }
```

`enabled: false` disables routing entirely. A worker set to `false` stays installed but out of
routing. A worker set to `true` is still only used when detected available and beneficial.

## Visibility

When a delegation actually happens, a compact note is shown, and nothing for routine work:

```
AI ROUTING
Codex  -> implementation
Claude -> verification
```

## Commands

| Command | Effect |
|---|---|
| `/ai-status` | Availability, auth mode and billing class per worker. Local checks, zero model calls. |
| `/ai-route` | Explains how a task would be routed. Runs no worker. |
| `/ai-review` | Independent review of recent work by a model that did not write it. |
