#!/bin/sh
# Bounded Gemini worker for the AI router, backed by the Antigravity CLI (agy).
# Prompt on stdin.
#   gemini-worker.sh --mode analyze|review [--cd DIR] [--timeout SEC] [--model M] [--json]
#
# Google retired the hosted gemini-cli for personal accounts on 2026-06-18; agy is the
# supported replacement and authenticates through the Google account in the macOS Keychain.
#
# Read-only by design: ~/.gemini/antigravity-cli/settings.json allows read_file,
# list_directory and grep_search, and denies write_file and command. Verified: the worker
# cannot create a file or run a shell command. --dangerously-skip-permissions is never passed.
#
# The prompt travels on stdin as NDJSON (agy --input-format stream-json), so prompt size is
# bounded by memory, not by ARG_MAX. Success is read from the structured `result` event's
# `status` field, never by searching the model's own text, so a response that merely quotes
# "status":"ERROR" is still a success.
#
# Exits 3 on timeout, 4 when unavailable or blocked by the billing guard.
#
# Last stderr line, on every exit, read by the session receipt (hooks/session_receipt.py):
#   ai-worker: gemini mode=<mode> status=ok|fail|timeout|unavailable exit=<rc> tokens=<usage>
# <usage> is in:N,cache:N,out:N,think:N from the `usage` of agy's result event, or
# `unavailable` when agy reported none.
MODE=analyze; CD="$PWD"; TIMEOUT=300; MODEL=""; JSON=""; TOKF=""
status_line() {
  case "$1" in 0) S=ok ;; 3) S=timeout ;; 4) S=unavailable ;; *) S=fail ;; esac
  T=$( [ -n "$TOKF" ] && [ -s "$TOKF" ] && cat "$TOKF" || echo unavailable)
  echo "ai-worker: gemini mode=$MODE status=$S exit=$1 tokens=$T" >&2
}
trap 'status_line $?' EXIT
need() { [ $# -ge 2 ] || { echo "$1 requires a value" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) need "$@"; MODE="$2"; shift 2 ;;
    --cd) need "$@"; CD="$2"; shift 2 ;;
    --timeout) need "$@"; TIMEOUT="$2"; shift 2 ;;
    --model) need "$@"; MODEL="$2"; shift 2 ;;
    --json) JSON=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$MODE" in analyze|review) ;; *) echo "gemini-worker: unknown mode '$MODE'" >&2; exit 2 ;; esac

command -v agy >/dev/null 2>&1 || { echo "gemini-worker: agy (Antigravity CLI) not installed; see https://antigravity.google/docs/cli/install" >&2; exit 4; }
[ -n "$GEMINI_API_KEY" ] && { echo "gemini-worker: refusing to run, GEMINI_API_KEY is set (billing guard: no metered API)" >&2; exit 4; }
[ -n "$GOOGLE_API_KEY" ] && { echo "gemini-worker: refusing to run, GOOGLE_API_KEY is set (billing guard: no metered API)" >&2; exit 4; }

# Per-invocation temp files. Concurrent workers never share a path, and the trap removes
# them on success, failure, and interrupt alike.
ERR=$(mktemp -t gemini-worker-err.XXXXXX) || exit 1
REQ=$(mktemp -t gemini-worker-req.XXXXXX) || exit 1
RES=$(mktemp -t gemini-worker-res.XXXXXX) || exit 1
TOKF=$(mktemp -t gemini-worker-tok.XXXXXX) || exit 1
cleanup() { RC_=$?; status_line $RC_; rm -f "$ERR" "$REQ" "$RES" "$TOKF"; }
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

# Encode stdin as one NDJSON user event. Python does the JSON quoting, so no prompt content
# is ever interpreted by the shell.
python3 -c '
import json,sys
p=sys.stdin.read()
if not p.strip(): sys.exit(3)
sys.stdout.write(json.dumps({"event":"user","message":{"content":p}})+"\n")
' > "$REQ" || { echo "gemini-worker: empty prompt on stdin" >&2; exit 2; }

set -- --input-format stream-json --output-format stream-json --print-timeout "${TIMEOUT}s" --disable-slash-commands
[ -n "$MODEL" ] && set -- "$@" --model "$MODEL"

cd "$CD" || { echo "gemini-worker: cannot cd to $CD" >&2; exit 2; }
# Outer guard at timeout+30s in case agy ignores its own deadline.
perl -e 'alarm shift; exec @ARGV' "$((TIMEOUT + 30))" agy "$@" < "$REQ" > "$RES" 2>"$ERR"
RC=$?
[ $RC -eq 142 ] || [ $RC -eq 14 ] && { echo "gemini-worker: timed out after ${TIMEOUT}s" >&2; exit 3; }

# Authoritative verdict: the structured result event, not the response text.
python3 - "$RES" "${JSON:-0}" "$TOKF" <<'PY'
import json,sys
path,want_json,tokf=sys.argv[1],sys.argv[2]=="1",sys.argv[3]
result=None
for line in open(path,encoding="utf-8",errors="replace"):
    line=line.strip()
    if not line: continue
    try: ev=json.loads(line)
    except ValueError: continue
    if ev.get("event")=="result": result=ev.get("result") or {}
if result is None:
    sys.stderr.write("gemini-worker: no result event from agy\n"); sys.exit(1)
u=result.get("usage")
if isinstance(u,dict):  # as agy reports it; nothing is estimated
    open(tokf,"w").write("in:%d,cache:%d,out:%d,think:%d"%tuple(int(u.get(k) or 0) for k in
        ("input_tokens","cache_read_tokens","output_tokens","thinking_tokens")))
status=result.get("status")
if status!="SUCCESS":
    sys.stderr.write("gemini-worker: turn status %s\n"%status)
    if result.get("error"): sys.stderr.write(str(result["error"])+"\n")
    sys.exit(1)
if want_json:
    sys.stdout.write(json.dumps(result)+"\n")
else:
    text=result.get("response") or ""
    if not text.strip():
        sys.stderr.write("gemini-worker: SUCCESS with empty response\n"); sys.exit(1)
    sys.stdout.write(text if text.endswith("\n") else text+"\n")
PY
PRC=$?
[ $PRC -ne 0 ] && [ -s "$ERR" ] && tail -3 "$ERR" >&2
# agy's own --print-timeout returns a partial turn: report it as a timeout, not a failure.
[ $PRC -ne 0 ] && grep -q "print timeout" "$ERR" 2>/dev/null && { echo "gemini-worker: timed out after ${TIMEOUT}s" >&2; exit 3; }
exit $PRC
