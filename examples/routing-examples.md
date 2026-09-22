# Routing examples

Each row is the actual route, not an idealized one. "No model call" means exactly that.

## Trivial and deterministic

| Task | Route | Why |
|---|---|---|
| "Does `package.json` exist?" | filesystem | A tool answers it. Zero delegated call, zero tokens spent on a model. |
| "What changed on this branch?" | `git diff`, `git log` | Same. Git is authoritative. |
| "Where is `parseConfig` defined?" | ripgrep, or Graphify `explain` on a large repository | Search beats asking a model to guess. |
| "Do the tests pass?" | test runner | The only source of truth about behaviour. |

## Small changes

| Task | Route |
|---|---|
| "Change the button padding" | Claude directly, plus `ui-design-system` if the component is being designed rather than tweaked |
| "Fix this typo in the README" | Claude directly. No Graphify, no worker |
| "Rename this local variable" | Claude directly |

A three line task stays a three line task.

## Frontend work

| Task | Route |
|---|---|
| "Build the pricing section" | `ui-design-system` skill, which searches 21st.dev, Magic UI, Unlumen UI, SmoothUI before inventing a pattern, then Claude adapts it to the project tokens |
| "Add a hover micro-interaction" | same skill, SmoothUI first |
| "Redesign the dashboard" | skill, then Claude implements, then an independent review if the change is large |

## Cross-module work

| Task | Route |
|---|---|
| "Refactor the authentication module" | 1. Graphify: dependencies, callers, imports, impact. 2. Verify in source, because the graph misses dynamic dispatch and injected collaborators. 3. Claude implements, or Codex if the spec is crisp and isolatable. 4. Tests. 5. Independent review by a model that did not write it. 6. `graphify update .` if the structure changed. |
| "What breaks if I delete this module?" | Graphify `explain`, then source verification. No worker. |
| "Analyze this large unfamiliar repository" | 1. Graphify for the map. 2. A targeted Gemini analysis on the parts worth reading in bulk. 3. Claude synthesizes and decides. |

## Review

| Task | Route |
|---|---|
| "Review this implementation independently" | A model that did not write it. Claude wrote it, so Codex reviews in read-only mode, given the requirements and the diff, and never told what Claude concluded. |
| "Two plausible designs, which one?" | Parallel analysis: Claude plus one worker, independently, then evidence (tests, source) resolves the disagreement. |
| "Is this diff safe to merge?" | Tests first, then review. Never review instead of tests. |

## Classification, once Jev is active

| Task | Route today | Route with a key |
|---|---|---|
| "Triage these 60 issues into 4 buckets" | Claude | `jev_classify`, one batched call, Claude acts on `decision=auto` and escalates `review` |
| "Which of these files answers this question?" | ripgrep, then Claude | `jev_rank` as a cheap pre-filter, Claude still decides |
| "Does this diff touch auth?" | ripgrep | ripgrep, still. A deterministic answer never goes to Jev. |

## Anti-patterns, explicitly avoided

- Calling three models on a task Claude answers in one paragraph.
- Delegating before locating the relevant code, which sends a whole repository instead of an excerpt.
- Asking a reviewer to confirm a conclusion instead of evaluating the change on its own terms.
- Retrying the same failed prompt on a second worker.
- Reaching for a metered API because a subscription worker is down.
