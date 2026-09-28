#!/bin/sh
# Bounded Codex worker for the AI router. Prompt on stdin.
#   codex-worker.sh --mode review|analyze|implement [--cd DIR] [--timeout SEC] [--model M] [--schema FILE]
#
# review/analyze -> sandbox read-only (Codex cannot modify the repository)
# implement      -> sandbox workspace-write, only when the caller asks for it
# Always --ephemeral: no session files accumulate.
# Exits 3 on timeout, 4 when codex is unavailable or not on a subscription login.
#
# Last stderr line, on every exit, read by the session receipt (hooks/session_receipt.py):
#   ai-worker: codex mode=<mode> status=ok|fail|timeout|unavailable exit=<rc> tokens=<usage>
# <usage> is in:N,cache:N,out:N,think:N summed from the turn.completed events of
# `codex exec --json`, or `unavailable` when codex reported none.
MODE=review; CD="$PWD"; TIMEOUT=300; MODEL=""; SCHEMA=""; TOK=unavailable; EV=""
status_line() {
  case "$1" in 0) S=ok ;; 3) S=timeout ;; 4) S=unavailable ;; *) S=fail ;; esac
  echo "ai-worker: codex mode=$MODE status=$S exit=$1 tokens=$TOK" >&2
}
trap 'RC_=$?; [ -n "$EV" ] && rm -f "$EV"; status_line $RC_' EXIT
need() { [ $# -ge 2 ] || { echo "$1 requires a value" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) need "$@"; MODE="$2"; shift 2 ;;
    --cd) need "$@"; CD="$2"; shift 2 ;;
    --timeout) need "$@"; TIMEOUT="$2"; shift 2 ;;
    --model) need "$@"; MODEL="$2"; shift 2 ;;
    --schema) need "$@"; SCHEMA="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v codex >/dev/null 2>&1 || { echo "codex-worker: codex not installed" >&2; exit 4; }
AUTH=$(python3 -c "import json,os;p=os.path.expanduser('~/.codex/auth.json');d=json.load(open(p)) if os.path.exists(p) else {};print(d.get('auth_mode') or 'none')" 2>/dev/null)
[ "$AUTH" = "chatgpt" ] || { echo "codex-worker: refusing to run, auth_mode is '$AUTH', not 'chatgpt' (billing guard: no metered API)" >&2; exit 4; }

case "$MODE" in
  review|analyze) SANDBOX=read-only ;;
  implement)      SANDBOX=workspace-write ;;
  *) echo "codex-worker: unknown mode '$MODE'" >&2; exit 2 ;;
esac

OUT=$(mktemp -t codex-worker)
EV=$(mktemp -t codex-worker-ev)
set -- exec -s "$SANDBOX" --ephemeral --skip-git-repo-check --json -C "$CD" -o "$OUT"
[ -n "$MODEL" ] && set -- "$@" -m "$MODEL"
[ -n "$SCHEMA" ] && set -- "$@" --output-schema "$SCHEMA"

# macOS has no coreutils timeout; perl's alarm is always present.
perl -e 'alarm shift; exec @ARGV' "$TIMEOUT" codex "$@" - >"$EV" 2>/tmp/.codex-worker-err
RC=$?
# Token usage exactly as codex reports it; nothing is estimated.
TOK=$(python3 - "$EV" <<'PY' 2>/dev/null || echo unavailable
import json,sys
t=None
for line in open(sys.argv[1],encoding="utf-8",errors="replace"):
    try: e=json.loads(line)
    except ValueError: continue
    u=e.get("usage") if e.get("type")=="turn.completed" else None
    if isinstance(u,dict):
        t=t or [0,0,0,0]
        for i,k in enumerate(("input_tokens","cached_input_tokens","output_tokens","reasoning_output_tokens")):
            t[i]+=int(u.get(k) or 0)
print("in:%d,cache:%d,out:%d,think:%d"%tuple(t) if t else "unavailable")
PY
)
# A timeout is reported as one even when codex left a partial answer behind.
if [ $RC -eq 142 ] || [ $RC -eq 14 ]; then
  echo "codex-worker: timed out after ${TIMEOUT}s" >&2; rm -f "$OUT"; exit 3
fi
if [ $RC -ne 0 ] && [ ! -s "$OUT" ]; then
  echo "codex-worker: codex exited $RC" >&2; tail -3 /tmp/.codex-worker-err >&2; rm -f "$OUT"; exit 1
fi
cat "$OUT"
# The status line must start on its own line even when stderr is merged into stdout.
[ -s "$OUT" ] && [ -n "$(tail -c 1 "$OUT")" ] && echo
rm -f "$OUT"
