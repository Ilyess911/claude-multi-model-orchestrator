# Security and privacy

## Repository rules

- No secret in this repository: no API key, token, cookie, Keychain value, MCP header or
  environment block.
- No employer-owned, confidential or proprietary material, and no content from work
  repositories.
- No personal email content, no account identifiers.
- No generated cache, no session log, no transcript. Those contain prompts.
- Absolute personal paths are written with `~` instead of the full home directory.
- A secret scan runs before the first commit, and a value that would have been sensitive is
  removed from the working tree, never merely gitignored after entering history.

## Credential handling on the machine

| Credential | Where it lives | Never |
|---|---|---|
| TypeSafe key for Jev | macOS Keychain, read at MCP launch | not in `~/.claude.json`, not in a dotfile, not in a repository |
| Google account for `agy` | macOS Keychain, shared with the Antigravity desktop app | no API key anywhere |
| ChatGPT login for Codex | `~/.codex/auth.json`, managed by the CLI | never copied, never printed |
| GitHub token | `gh` keyring | never echoed into a file |

Status output prints auth **mode**, never key material.

## Data leaving the machine

| Component | Network |
|---|---|
| `graphify update`, `query`, `explain`, `path`, git hooks, `hook-guard` | local only, no model call |
| Codex worker | ChatGPT subscription endpoint, only the scoped prompt |
| Gemini worker | Google endpoint through `agy`, only the scoped prompt |
| Jev | TypeSafe API, inactive today |
| Graphify LLM-backed subcommands (`extract`, `label`, `cluster-only` without `--no-label`, `add <url>`, `prs --triage`, the semantic pass of the `/graphify` skill) | can send file content off-machine, so each one needs explicit approval per repository |

Hard rule: no employer-owned or confidential material is ever fed into an LLM-backed path.
When a folder looks work-related, the local Graphify commands are the only ones that run
without asking, and anything external is flagged first.

## Worker scoping

- Workers receive the minimum evidence that makes the judgment possible: the objective, the
  scope, the relevant excerpts, the constraints, the expected output. Never the conversation,
  never a whole repository.
- Raw evidence goes to a worker, not Claude's own conclusion about it, which would bias the
  answer.
- `codex-worker.sh --mode review|analyze` and both Gemini modes are read-only sandboxes. Write
  access is granted only by an explicit `--mode implement`.

## Repository safety

- Claude is the only coordinator of repository state. `git status` is checked before any
  delegated change and uncommitted work is preserved.
- Never automatic: `git reset --hard`, `git clean -fd`, force checkout, force push, or
  anything else that discards work.
- Parallel workers analyze, review or propose. If two must write, they get disjoint file
  scopes. Concurrent writes to the same file are never allowed.
- Delegation depth is one, and at most two attempts per unresolved subtask, so no runaway
  chain of model calls can rewrite a tree unsupervised.

## Harness note

The global permission mode is `bypassPermissions`, with a 236-entry allowlist in
`~/.claude/settings.local.json`. That trades prompt friction for speed, and it is the reason
the destructive-operation rules above are policy rather than a dialog box. Anyone reproducing
this setup should decide that trade-off deliberately rather than copying it.
