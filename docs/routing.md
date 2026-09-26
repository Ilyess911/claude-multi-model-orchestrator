# Routing

Source of truth on the machine: `~/.claude/skills/ai-router/routing-policy.md`.

## Philosophy

**Auto-routing is always on; auto-delegation happens when justified.** Every substantial task
gets a routing decision before execution. The decision may be DIRECT, and usually is, but it
is never skipped just because Claude looks able to do the work. The deciding question is:
would a second model materially improve quality, independence, context handling or
verification, at reasonable latency?

The always-active part lives in the global `~/.claude/CLAUDE.md` (loaded every session), so
routing no longer depends on Claude choosing to load the `ai-router` skill. The skill holds
the invocation details and is loaded once a route other than DIRECT is picked.

## Fast path

Trivial work skips routing entirely, with no worker and no routing note: existence checks,
git status, a typo, a rename, changing a value, a one-line fix, a short factual answer.

## Hierarchy

Deterministic tools answer first (filesystem, git, ripgrep, compiler, linter, tests,
Graphify). Then, for a substantial task, weigh complexity, scope, risk, context size,
implementation intensity, independent-review value, parallelizability and worker
availability, and pick one route: DIRECT, DELEGATE_CODEX, DELEGATE_GEMINI,
IMPLEMENT_REVIEW, PARALLEL_ANALYSIS.

- **Codex triggers:** non-trivial multi-file implementation, structural refactor, hard
  debugging, substantial tests, regression-risk changes, significant pre-commit changes,
  independent code review, alternative implementation.
- **Gemini triggers:** very large context, many documents to actually read, repository-wide
  analysis, many sources to compare, independent second opinion, parallelizable analysis.
- **Risk-based review:** auth, permissions, security, billing, payments, migrations,
  destructive operations, deployment, infrastructure, concurrency, data-loss risk, large
  refactors, public releases. Even a small diff there gets a reviewer that did not write it;
  a request to review such a change sends it to Codex in `--mode review` alongside Claude.
- Repository-wide or cross-package questions default to PARALLEL_ANALYSIS: Gemini reads the
  whole scope while Claude narrows with Graphify or grep; targeted grep is not a repo-wide read.
- Workers run in the foreground so their result is awaited; a backgrounded worker dies with a
  headless session.
- No line-count or file-count thresholds: nature and risk decide.
- No automatic per-commit worker hook: the router decides.

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

When a delegation actually happens, a compact note is shown; DIRECT and the fast path print
nothing:

```
AI ROUTING
Codex  -> implementation
Claude -> verification
```

## Telemetry

The session receipt classifies each typed prompt of the main transcript from its worker
calls, with no extra instrumentation: DIRECT (none), CODEX, GEMINI, CROSS REVIEW (any worker
in `--mode review`), PARALLEL (Codex and Gemini analyzing in the same turn). It shows in the
terminal ticket, the PNG and the Telegram message. Calibration: 20 substantial dev tasks with
zero workers means routing is too conservative; three models on most tasks means too
aggressive. No quota.

## Commands

| Command | Effect |
|---|---|
| `/ai-status` | Availability, auth mode and billing class per worker. Local checks, zero model calls. |
| `/ai-route` | Explains how a task would be routed. Runs no worker. |
| `/ai-review` | Independent review of recent work by a model that did not write it. |
