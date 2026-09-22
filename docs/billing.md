# Billing

Principle: three subscriptions already paid for, and no fourth bill arriving by surprise.

Priority order: **quality first, then no additional API bill, then efficient use of included
quota, then latency.**

## Classification

| Integration | Class | Verified state on 2026-09-22 |
|---|---|---|
| ripgrep, git, filesystem, compilers, linters, test runners, Graphify | LOCAL_FREE | always preferred |
| Claude Code on the Claude subscription | INCLUDED_SUBSCRIPTION | active |
| Codex CLI with `auth_mode: chatgpt` | INCLUDED_SUBSCRIPTION | active, verified locally |
| Gemini through `agy` on the Google account in the Keychain | INCLUDED_SUBSCRIPTION | active |
| Jev on TypeSafe | METERED_LOW_COST | inactive, no key |
| Anthropic API, OpenAI API, Gemini API, Vertex AI | METERED_API | disabled |

## No surprise API fallback

Automatic METERED_API usage is disabled. When a subscription-authenticated worker is
unavailable, the fallback is Claude or deterministic tooling, never a metered API.

The policy is not advisory, it is enforced in the wrappers:

| Guard | Where | Behaviour |
|---|---|---|
| Codex must be on a ChatGPT login | `codex-worker.sh` | reads `auth_mode` from `~/.codex/auth.json`, exits 4 if it is not `chatgpt` |
| Gemini must not have an API key in the environment | `gemini-worker.sh` | exits 4 if `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set |
| Claude must not be on an API key | `ai-status.sh` | reports `METERED_API` if `ANTHROPIC_API_KEY` is set, instead of pretending it is included |

Exit 4 means "refused, no spend". It is not an error to work around.

Never configured on the user's behalf: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, Vertex settings. An API key is never
suggested as a quick fix for an auth problem. If metered access is ever wanted, the user sets
it up themselves and says so.

## Availability is free

`~/.claude/bin/ai-status.sh` answers from local signals only: which binaries exist, what
`~/.codex/auth.json` says, whether an API key is in the environment, whether the Keychain
holds a TypeSafe key, what `~/.claude/ai-router.json` allows. Zero model calls.

`--live` is opt-in and makes exactly one tiny call per available worker, used only when an
auth or quota problem is suspected. A failed Gemini live probe drops a marker file
(`~/.claude/.ai-router-gemini-ineligible`) so later sessions do not retry blindly; a
successful probe removes it.

## Quota

No official CLI exposes remaining subscription quota. The rule is to invent nothing: no
estimated budget, no scraped account page. Only real signals count, meaning success, failure,
rate limit errors and observed latency in the current session. If two workers fit equally and
one is visibly rate limited, prefer the other. Otherwise quality decides, and usage is not
balanced artificially.

## One exception, explicitly authorized

One project has a standing authorization for a small automated daily model call under a hard
daily cost ceiling, with alerting and revocation conditions attached. It lives in that
project's own rules, not in this global layer, and no other automated spend exists.
