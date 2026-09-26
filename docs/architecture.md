# Architecture

Read from the live machine on 2026-09-22. Paths are written with `~` for the home directory.

## Layers

| Layer | Role | Where it lives |
|---|---|---|
| Interface | The only thing launched by hand: `claude` | Claude Code 2.1.278 |
| Policy | Global rules loaded into every session | `~/.claude/CLAUDE.md` |
| Harness config | Permissions, hooks, enabled plugins | `~/.claude/settings.json`, `~/.claude/settings.local.json` |
| Knowledge | Local AST knowledge graph of a repository | `graphify` 0.9.61 (`uv tool`, package `graphifyy`) |
| Skills | Task-specific instruction bundles | `~/.claude/skills/`, plugin skills, synced skills |
| Router | Decides engine per task | `~/.claude/skills/ai-router/`, `~/.claude/ai-router.json` |
| Workers | Bounded subprocess calls to other models | `~/.claude/bin/codex-worker.sh`, `~/.claude/bin/gemini-worker.sh` |
| Classifier | Bounded semantic judgment, inactive | `~/.claude/vendor/jev-code`, `jev` MCP server |
| Presentation | Response shape and wording | `i-have-adhd` plugin, `caveman` plugin, `statusLine` in `settings.json` |

## Real file map

```
~/.claude/
├── CLAUDE.md                     global policy: graphify, UI, jev, ai-router, style
├── settings.json                 hooks, enabled plugins, permissions mode
├── settings.local.json           local permission allowlist only (no hooks)
├── ai-router.json                router kill switches
├── bin/
│   ├── ai-status.sh              local availability probe, zero model calls
│   ├── caveman-statusline.sh     stable wrapper, resolves the plugin script by glob
│   ├── codex-worker.sh           bounded Codex call, sandboxed
│   └── gemini-worker.sh          bounded Gemini call through agy, read only
├── hooks/
│   ├── graphify_session_start.py SessionStart graph freshness
│   └── session_receipt.py        SessionEnd receipt: terminal ticket + Telegram copy
├── skills/
│   ├── ai-router/                SKILL.md + routing-policy + capabilities + billing-policy
│   ├── graphify/                 SKILL.md + references/
│   ├── jev/                      SKILL.md + references/
│   ├── ui-design-system/         SKILL.md + references/
│   └── synced/                   account-synced skills (docs, docx, pptx, xlsx, pdf, ...)
├── commands/
│   ├── ai-status.md  ai-route.md  ai-review.md
│   └── construire-site-premium/
├── vendor/
│   └── jev-code/                 local Jev build, MCP entry at bin/jev-mcp.sh
└── plugins/                      caveman, i-have-adhd, figma (official marketplace)
```

MCP servers are declared in `~/.claude.json`: `21st` (HTTP, 21st.dev component catalog) and
`jev` (stdio, local launcher). Header and environment values are not reproduced anywhere in
this repository.

## Control flow of one session

1. Claude Code starts and loads `~/.claude/CLAUDE.md`.
2. SessionStart hooks run: repository sync check, Graphify freshness, caveman activation,
   i-have-adhd always-on flag. None of them calls a model.
3. The user types a task.
4. Claude picks a route per `docs/routing.md`. Most tasks stop at deterministic tools or Claude.
5. If a worker is selected, Claude runs `~/.claude/bin/ai-status.sh` once, then pipes a scoped
   prompt into the matching worker script.
6. Worker output returns to Claude as a proposal. Claude verifies against source and tests.
7. Claude writes the answer, shaped by i-have-adhd and worded by Caveman.

## What never happens automatically

- No model call at startup.
- No metered API call, ever, without the user configuring it themselves.
- No worker writing to a file that another worker is writing to.
- No destructive git operation (`reset --hard`, `clean -fd`, force checkout) as a side effect.
- No delegation chain deeper than one hop.
