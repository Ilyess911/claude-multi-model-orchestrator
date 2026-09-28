# Testing

One command, from the repository root, standard library only:

```sh
python3 -m unittest
```

No network, no model call, no dependency to install. Runs in under a second.

## What is covered

| File | Covers |
|---|---|
| `tests/test_session_receipt.py` | Transcript parsing, route detection, token and cache totals, API equivalent, billing classification, worker outcomes, Telegram payload privacy, snapshots |
| `tests/test_write_scope.py` | `scope-check.py` on temporary git repositories (in scope, outside, new, deleted, globs, pre-existing work, index-only change, moved HEAD) and `codex-worker.sh --scope` end to end against a fake `codex` binary, no model call |
| `tests/test_repeat_tool_guard.py` | Thresholds 3, 5, 8, no false alarm on changing arguments, normalization, exemptions, polling prompts, resets, bounded state, fail open, kill switch |

Receipt checks, one fixture each where it applies:

- **Parsing:** a message streamed twice is counted once; meta lines, slash-command echoes,
  tool results and compaction summaries are not prompts; subagent transcripts add to totals
  and to the duration.
- **Routing:** DIRECT, CODEX, GEMINI, CROSS REVIEW, PARALLEL, and a multi-turn mix. A `grep`
  on a worker script is not a delegation.
- **Accounting:** input, output, cache read and cache write totals; per-model rates, fast
  mode, US inference x1.1, 1 h cache write, web search; an unknown model is listed, never priced.
- **Billing:** 0 $ on the subscription; `ANTHROPIC_API_KEY` makes the session metered; a Jev
  call is extra billing.
- **Telegram:** the payload carries no prompt, path, reply or command; headless sessions and
  `/clear` send nothing.

- **Workers:** Codex success, failure, retry then success; Gemini success, failure, timeout;
  billing guard refusal then Claude fallback; a mixed session with a Gemini timeout taken
  over by Codex; UNKNOWN when the status line is missing; a status line quoted in a model's
  answer, a raw CLI run, two runs with one line, command order, and a write-scope violation. Normal sessions show no
  incident; a failure is the first line of the Telegram text.

## Fixtures

`tests/fixtures.py` builds seventeen synthetic transcripts in code and writes them to a
temporary directory at test time. Every prompt, path and id is invented. No `.jsonl` file is
committed (the repository ignores `*.jsonl`), and no real transcript is ever used.

## Snapshots

`tests/snapshots/` holds, per fixture, the summary as JSON, the terminal ticket and the
Telegram text. The wall-clock time is masked as `<now>`. A missing snapshot is written on the
first run. After an intended change to the receipt, review the diff, then refresh:

```sh
UPDATE_SNAPSHOTS=1 python3 -m unittest
git diff tests/snapshots/
```

## Keeping the live copy in sync

The live hooks run from `~/.claude/hooks/`. After changing a hook here, run the tests, then
copy the file there. `diff -q hooks/<file> ~/.claude/hooks/<file>` must print nothing.
