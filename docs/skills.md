# Skills

Inventory read from the machine on 2026-09-22. A skill is an instruction bundle Claude **may
choose** to load. It never executes by itself. Compare with [hooks.md](hooks.md), which does.

Activation column:

- **automatic**: policy in `~/.claude/CLAUDE.md` tells Claude to load it without being asked
- **conditional**: loaded when the task matches the skill description
- **manual**: invoked by name, usually as a slash command

## Personal skills (`~/.claude/skills/`)

| Name | Purpose | Activation | Typical trigger |
|---|---|---|---|
| `ai-router` | Choose the engine: deterministic tool, Claude, Codex, Gemini, cross-model review | automatic before any delegation | "which model should handle this", "second opinion", any delegation decision |
| `graphify` | Turn a codebase or documents into a persistent knowledge graph, then query it | automatic on substantial repositories | "how does this work", "what will this break", "where is this used", refactoring |
| `ui-design-system` | Route component choices through 21st.dev, Magic UI, Unlumen UI, SmoothUI, Neobrutalism before inventing patterns | automatic for frontend work | landing page, dashboard, React/Next/Astro component, animation, UI redesign |
| `jev` | Call mechanics and question design for the TypeSafe System One classifier | conditional, blocked while no key exists | batch labelling, calibrated yes/no, scoring, ranking |

`ai-router` carries three reference files: `routing-policy.md`, `capabilities.md`,
`billing-policy.md`. Updating `capabilities.md` alone changes routing preferences.

## Slash commands (`~/.claude/commands/`)

| Command | Purpose | Activation |
|---|---|---|
| `/ai-status` | Worker availability, auth mode, billing class. Local checks, zero model calls | manual |
| `/ai-route` | Explain the route a task would take, without running a worker | manual |
| `/ai-review` | Independent review of recent work by a model that did not write it | manual |
| `/construire-site-premium` | Install the premium website stack (UI/UX, motion, 21st.dev components) | manual |

## Account-synced skills (`~/.claude/skills/synced/`)

Ten skills synced from the Claude account, all present and usable.

| Name | Purpose | Activation |
|---|---|---|
| `docs` | Living shared documents, only when the user asks for one | conditional |
| `docx` | Create, read and edit Word documents and templates | conditional |
| `pptx` | Create, read and edit PowerPoint decks and templates | conditional |
| `xlsx` | Spreadsheets as primary input or output, including CSV and TSV cleanup | conditional |
| `pdf` | Read, extract, merge, split, fill, OCR and produce PDFs | conditional |
| `humanizer` | Rewrite AI-sounding prose without changing meaning | conditional |
| `import-memory` | Import a memory export from another assistant, additively | manual |
| `morning` | Render or schedule the morning brief | manual (`/morning`) |
| `seo-audit` | Technical and on-page SEO audit | conditional |
| `skill-creator` | Create, edit, evaluate and optimize skills | manual |

## Plugin skills

Enabled plugins are declared in `~/.claude/settings.json`.

| Plugin | Source | Skills | Activation |
|---|---|---|---|
| `caveman` | `JuliusBrussee/caveman` | `caveman`, `caveman-commit`, `caveman-review`, `caveman-stats`, `caveman-compress`, `caveman-help`, `cavecrew` | wording mode is automatic through a hook; the rest are manual |
| `i-have-adhd` | `ayghri/i-have-adhd` | `i-have-adhd` | automatic through a hook plus the always-on flag |
| `figma` | `anthropics/claude-plugins-official` | 14 skills: `figma-use`, `figma-design-to-code`, `figma-generate-design`, `figma-generate-library`, `figma-generate-diagram`, `figma-create-new-file`, `figma-code-connect`, `figma-implement-motion`, `figma-shaders`, `figma-swiftui`, `figma-generative-plugins`, `figma-use-figjam`, `figma-use-motion`, `figma-use-slides` | conditional, several are mandatory prerequisites before a Figma MCP call |
| `cowork-plugin-management` | account-synced plugin bucket | `cowork-plugin-customizer`, `create-cowork-plugin` | manual |

`caveman` also ships subagents (`cavecrew-investigator`, `cavecrew-builder`,
`cavecrew-reviewer`) whose output is compressed before it re-enters the main context.

## Harness built-in skills

Claude Code itself exposes skills that are not part of this setup but are available in every
session, among them `code-review`, `simplify`, `security-review`, `init`, `run`, `dataviz`,
`update-config`, `loop`, `schedule`, `workflow-authoring` and the artifact skills. They are
listed here only so a future reader does not mistake them for something installed by hand.

## What is deliberately absent

- No `~/.claude/agents/` directory: no custom subagents defined by hand.
- No project-level `CLAUDE.md` committed into work repositories, by policy.
- No skill holds a credential. The only key-bearing component reads the macOS Keychain at
  launch.
