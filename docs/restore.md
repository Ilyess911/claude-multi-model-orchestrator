# Reproducing the architecture on another Mac

Documentation, not an installer. No credential is stored in this repository, so every
authentication step is manual and marked **AUTH**.

Estimated time: about 45 minutes, most of it waiting on logins and downloads.

## 1. Base tools

```sh
# Claude Code (the only interface)
# install per Anthropic instructions, then:
claude --version            # expected: 2.x

# Codex CLI
codex --version             # expected: codex-cli 0.14x

# Antigravity CLI (Gemini path)
# https://antigravity.google/docs/cli/install
agy --version               # expected: 1.2.x

# Graphify, global uv tool, never vendored into a project
uv tool install graphifyy
graphify --version          # expected: 0.9.6x
```

**AUTH** required: Claude subscription login, `codex login` with the ChatGPT account, `agy`
login with the Google account. All three must end on a subscription login, not an API key.

## 2. Global policy and configuration

Recreate by hand, they are personal and not mirrored here:

| File | Content to recreate |
|---|---|
| `~/.claude/CLAUDE.md` | Graphify policy, UI policy, Jev policy, AI router policy, response style policy |
| `~/.claude/settings.json` | SessionStart and PreToolUse hooks, enabled plugins, extra marketplaces, permission mode |
| `~/.claude/ai-router.json` | `{"enabled": true, "workers": {"codex": true, "gemini": true, "jev": true}}` |
| `~/.claude/settings.local.json` | permission allowlist, machine-specific, start empty |

## 3. Router layer

Recreate three scripts in `~/.claude/bin/`, all `chmod +x`:

| Script | Must implement |
|---|---|
| `ai-status.sh` | local-only availability probe, `--live` and `--json` flags, prints auth mode never keys |
| `caveman-statusline.sh` | resolve the current plugin statusline script by glob, exit 0 when the plugin is absent |
| `codex-worker.sh` | `codex exec`, `--ephemeral`, sandbox per mode, `auth_mode = chatgpt` guard, `perl alarm` timeout, exit codes 0/1/2/3/4 |
| `gemini-worker.sh` | `agy` NDJSON stdin, structured `result` parsing, per-invocation `mktemp`, cleanup trap, API-key guard, outer `perl alarm` at timeout+30 |

And four skill files in `~/.claude/skills/ai-router/`: `SKILL.md`, `routing-policy.md`,
`capabilities.md`, `billing-policy.md`. Plus three commands in `~/.claude/commands/`:
`ai-status.md`, `ai-route.md`, `ai-review.md`.

See [workers.md](workers.md) and [routing.md](routing.md) for the exact behaviour each file
must reproduce.

## 4. Hooks

- Write `~/.claude/hooks/graphify_session_start.py` (freshness check, fails open, exits 0 on
  any error) and wire it as a SessionStart hook with a 60 s timeout.
- Wire the two Graphify `PreToolUse` guards: `hook-guard search` on `Bash|Grep`,
  `hook-guard read` on `Read|Glob`.
- Write `~/.claude/hooks/session_receipt.py` (reads the transcript, prints the session
  receipt to the terminal device of the `claude` process, fails open) and wire it as a `SessionEnd` hook with a 10 s timeout.
  Back up `settings.json` with a timestamp before editing it.
- Per work repository: `graphify hook install`, and add `graphify-out/` to `.gitignore`.

## 5. Plugins

```
/plugin marketplace add JuliusBrussee/caveman
/plugin marketplace add ayghri/i-have-adhd
/plugin install caveman@caveman
/plugin install i-have-adhd@i-have-adhd
/plugin install figma@claude-plugins-official
```

Then `touch ~/.claude/.i-have-adhd-always` for permanent structure mode, and set the caveman
level once with `/caveman full`.

For the status line badge, write `~/.claude/bin/caveman-statusline.sh` (glob the plugin cache,
take the newest match, exec it, exit 0 when absent) and point `statusLine` in `settings.json`
at that wrapper, never at the version-hashed plugin path directly.

## 6. Skills

Account-synced skills (`docs`, `docx`, `pptx`, `xlsx`, `pdf`, `humanizer`, `import-memory`,
`morning`, `seo-audit`, `skill-creator`) arrive on their own once the Claude account is
signed in. **AUTH** required.

Personal skills to recreate by hand: `graphify`, `ui-design-system`, `jev`, plus `ai-router`
from step 3.

## 7. MCP servers

| Server | Transport | Note |
|---|---|---|
| `21st` | HTTP to the 21st.dev component API | **AUTH** required, header value never stored in a repository |
| `jev` | stdio, `~/.claude/vendor/jev-code/bin/jev-mcp.sh` | build locally, launcher reads the key from the Keychain |

## 8. Jev, optional

```sh
# only if a TypeSafe key exists; the setup works fully without it
security add-generic-password -a "$USER" -s TYPESAFE_API_KEY -w '<key>'
```

**AUTH** required. Without it the MCP server still starts and `jev_*` calls fail cleanly.

## 9. Verification

```sh
~/.claude/bin/ai-status.sh
```

Expected: `claude true`, `codex true` on a ChatGPT login, `gemini true` on a Google account,
`jev false` unless a key was added, and the closing line
`metered API auto-use: DISABLED`.

Then open a repository and start a session: the Graphify hook should report graph status, the
caveman hook should report a wording level, and the i-have-adhd hook should report always-on.

## What is deliberately not automated

No install script, because every meaningful step is either a login or a personal policy file.
An installer would encourage copying credentials and blind-copying a permission mode that
should be chosen deliberately.
