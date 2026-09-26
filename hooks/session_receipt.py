#!/usr/bin/env python3
"""SessionEnd hook: print a thermal-receipt summary of the session to the terminal,
then send a metadata-only copy to a dedicated Telegram bot (best effort).

Reads the session transcript (JSONL) named in the hook input, never calls a model.
Fails open: any error exits 0 without output.

Usage:
  session_receipt.py                  hook mode, JSON on stdin, prints to the claude terminal
  session_receipt.py <transcript>     manual mode, prints to stdout
  session_receipt.py --telegram-test | --telegram-setup-chat | --telegram-preview <transcript>
  session_receipt.py --png-preview <transcript> <out.png>   (run with the venv python)
"""
import glob
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

W = 40

# USD per million tokens, from platform.claude.com/docs/en/about-claude/pricing,
# checked 2026-09-25. (input, cache write 5m, cache write 1h, cache read, output)
PRICES = {
    "claude-fable-5-1": (10.0, 12.50, 20.0, 0.25, 50.0),
    "claude-mythos-5-1": (10.0, 12.50, 20.0, 0.25, 50.0),
    "claude-fable-5": (10.0, 12.50, 20.0, 1.00, 50.0),
    "claude-mythos-5": (10.0, 12.50, 20.0, 1.00, 50.0),
    "claude-opus-5-5": (4.0, 5.00, 8.0, 0.20, 20.0),
    "claude-opus-5": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-8": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-7": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-6": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-5": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-1": (15.0, 18.75, 30.0, 1.50, 75.0),
    "claude-opus-4": (15.0, 18.75, 30.0, 1.50, 75.0),
    "claude-sonnet-5": (2.0, 2.50, 4.0, 0.20, 10.0),
    "claude-sonnet-4-6": (3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-sonnet-4-5": (3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-sonnet-4": (3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-haiku-4-5": (1.0, 1.25, 2.0, 0.10, 5.0),
}
# Fast mode: input and output only, cache multipliers stack on the fast input price.
FAST = {"claude-opus-5-5": (8.0, 40.0), "claude-opus-5": (10.0, 50.0), "claude-opus-4-8": (10.0, 50.0)}
CACHE_MULT = {"claude-fable-5-1": 0.025, "claude-mythos-5-1": 0.025, "claude-opus-5-5": 0.05}

SHORT = {
    "claude-fable-5-1": "Fable 5.1", "claude-mythos-5-1": "Mythos 5.1", "claude-fable-5": "Fable 5",
    "claude-mythos-5": "Mythos 5", "claude-opus-5-5": "Opus 5.5", "claude-opus-5": "Opus 5",
    "claude-opus-4-8": "Opus 4.8", "claude-opus-4-7": "Opus 4.7", "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-5": "Opus 4.5", "claude-opus-4-1": "Opus 4.1", "claude-opus-4": "Opus 4",
    "claude-sonnet-5": "Sonnet 5", "claude-sonnet-4-6": "Sonnet 4.6", "claude-sonnet-4-5": "Sonnet 4.5",
    "claude-sonnet-4": "Sonnet 4", "claude-haiku-4-5": "Haiku 4.5",
}


# A delegation is an invocation (worker with --mode, or the raw CLI in exec position),
# not a mention: grep, sed or cat on the worker script does not count.
CODEX_RE = re.compile(r"codex-worker\.sh[\"']?\s+--mode|(?:^|[;&|(]\s*)codex\s+exec\b")
GEMINI_RE = re.compile(r"gemini-worker\.sh[\"']?\s+--mode|(?:^|[;&|(]\s*)agy\s+-")


def price_key(model):
    """Longest known id that prefixes the model id (strips date suffixes and [1m])."""
    m = re.sub(r"\[.*\]$", "", model or "")
    for k in sorted(PRICES, key=len, reverse=True):
        if m == k or m.startswith(k + "-"):
            return k
    return None


def files_for(path):
    """Main transcript plus subagent transcripts stored under <session>/subagents/."""
    return [path] + sorted(glob.glob(os.path.join(path[:-6], "subagents", "*.jsonl")))


def load(path):
    """Assistant messages deduplicated by message id (streaming writes one line per block)."""
    msgs, first, last = {}, None, None
    tools, skills = {}, {}
    codex = gemini = jev = 0
    lines = (l for f in files_for(path) for l in open(f, encoding="utf-8", errors="replace"))
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ts = d.get("timestamp")
        if ts:
            first = first or ts
            last = ts
        if d.get("type") != "assistant":
            continue
        m = d.get("message") or {}
        mid = m.get("id") or id(d)
        if m.get("model") and m.get("model") != "<synthetic>" and m.get("usage"):
            msgs[mid] = m
        for c in m.get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            key = (mid, c.get("id"))
            if key in tools:
                continue
            tools[key] = name = c.get("name", "?")
            inp = c.get("input") or {}
            if name == "Skill":
                s = str(inp.get("skill", "?"))
                skills[s] = skills.get(s, 0) + 1
            elif name.startswith("mcp__jev__"):
                jev += 1
            elif name == "Bash":
                cmd = str(inp.get("command", ""))
                codex += len(CODEX_RE.findall(cmd))
                gemini += len(GEMINI_RE.findall(cmd))
    return list(msgs.values()), first, last, tools, skills, codex, gemini, jev


def cost(m):
    u = m.get("usage") or {}
    k = price_key(m.get("model"))
    if not k:
        return None
    inp, w5, w1, rd, out = PRICES[k]
    if u.get("speed") == "fast" and k in FAST:
        inp, out = FAST[k]
        w5, w1, rd = inp * 1.25, inp * 2, inp * CACHE_MULT.get(k, 0.1)
    cc = u.get("cache_creation") or {}
    c5 = cc.get("ephemeral_5m_input_tokens")
    c1 = cc.get("ephemeral_1h_input_tokens")
    if c5 is None and c1 is None:
        c5, c1 = u.get("cache_creation_input_tokens", 0), 0
    usd = (u.get("input_tokens", 0) * inp + (c5 or 0) * w5 + (c1 or 0) * w1
           + u.get("cache_read_input_tokens", 0) * rd + u.get("output_tokens", 0) * out) / 1e6
    if u.get("inference_geo") == "us":
        usd *= 1.1
    ws = (u.get("server_tool_use") or {}).get("web_search_requests", 0)
    return usd + ws * 0.01


def fmt_n(n):
    return f"{n:,}".replace(",", " ")


def row(label, value):
    label, value = str(label), str(value)
    room = W - len(value) - 1
    if len(label) > room:
        label = label[: room - 1] + "."
    return label.ljust(room) + " " + value


def center(t):
    return t[:W].center(W).rstrip()


def barcode(seed):
    bars = "█▌▐║│▍▎▏"
    h = int(hashlib.sha1(seed.encode()).hexdigest()[:8], 16)
    out = []
    for i in range(W - 4):
        h = (h * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(bars[h % len(bars)] if (h >> 5) % 5 else " ")
    return "  " + "".join(out)


def summarize(path):
    """Session metadata shared by the terminal ticket and the Telegram message.

    Counts and names of models, tools and skills only: no prompt, reply, path or code.
    """
    msgs, first, last, tools, skills, codex, gemini, jev = load(path)
    models = {}
    tin = tout = cr = cw = 0
    equiv, unpriced = 0.0, set()
    for m in msgs:
        u = m["usage"]
        k = price_key(m["model"]) or m["model"]
        models[k] = models.get(k, 0) + 1
        tin += u.get("input_tokens", 0)
        tout += u.get("output_tokens", 0)
        cr += u.get("cache_read_input_tokens", 0)
        cw += u.get("cache_creation_input_tokens", 0)
        c = cost(m)
        if c is None:
            unpriced.add(m["model"])
        else:
            equiv += c

    dur, secs = "", None
    try:
        t0 = datetime.fromisoformat(first.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(last.replace("Z", "+00:00"))
        s = secs = int((t1 - t0).total_seconds())
        dur = f"{s // 3600}h{s % 3600 // 60:02d}" if s >= 3600 else f"{s // 60}min{s % 60:02d}"
    except Exception:
        dur = "n/d"

    # Claude Code on a subscription login has no API key in its environment.
    metered = bool(os.environ.get("ANTHROPIC_API_KEY"))
    real = equiv if metered else 0.0

    tool_counts = {}
    for n in tools.values():
        tool_counts[n] = tool_counts.get(n, 0) + 1

    return {"models": models, "tin": tin, "tout": tout, "cr": cr, "cw": cw,
            "equiv": equiv, "unpriced": unpriced, "dur": dur, "metered": metered,
            "real": real, "secs": secs, "tool_counts": tool_counts, "skills": skills,
            "codex": codex, "gemini": gemini, "jev": jev}


def render(path, session_id="", reason="", st=None):
    st = st or summarize(path)
    models, tin, tout, cr, cw = st["models"], st["tin"], st["tout"], st["cr"], st["cw"]
    equiv, unpriced, dur, metered, real = st["equiv"], st["unpriced"], st["dur"], st["metered"], st["real"]
    tool_counts, skills = st["tool_counts"], st["skills"]
    codex, gemini, jev = st["codex"], st["gemini"], st["jev"]

    sep, dbl = "-" * W, "=" * W
    L = [dbl, center("CLAUDE ORCHESTRATOR"), center("ticket de session"),
         center(datetime.now().strftime("%d/%m/%Y   %H:%M:%S")), dbl]
    for k, n in sorted(models.items(), key=lambda x: -x[1]):
        L.append(row(SHORT.get(k, k), f"{n} req"))
    L.append(sep)
    L += [row("Input", fmt_n(tin)), row("Output", fmt_n(tout)),
          row("Cache lecture", fmt_n(cr)), row("Cache ecriture", fmt_n(cw)),
          row("TOTAL TOKENS", fmt_n(tin + tout + cr + cw)), sep]
    L.append(row("Outils", f"{sum(tool_counts.values())} appels"))
    for n, c in sorted(tool_counts.items(), key=lambda x: -x[1])[:5]:
        L.append(row("  " + re.sub(r"^mcp__([^_]+)__", r"\1:", n), f"x{c}"))
    L.append(row("Skills", sum(skills.values())))
    for n, c in sorted(skills.items(), key=lambda x: -x[1])[:3]:
        L.append(row("  " + n.split(":")[-1], f"x{c}"))
    L += [row("Codex", f"{codex} deleg." if codex else "0"),
          row("Gemini", f"{gemini} deleg." if gemini else "0"),
          row("Jev", f"{jev} appels" if jev else "0")]
    if codex or gemini:
        L.append(row("  quota Codex/Gemini", "non mesure"))
    L += [sep, row("Duree", dur), sep,
          row("EQUIVALENT API", f"{equiv:.2f} $"),
          row("COUT REEL FACTURE", f"{real:.2f} $"),
          row("  Claude", "cle API" if metered else "abonnement Max"),
          row("  Codex/Gemini", "abonnement")]
    if unpriced:
        L.append(row("  sans tarif", ",".join(sorted(unpriced))[:20]))
    extra = []
    if metered:
        extra.append("API Anthropic")
    if jev:
        extra.append(f"Jev x{jev}")
    L += [row("FACTURATION SUPPL.", "aucune" if not extra else "OUI"), ]
    for e in extra:
        L.append(row("  ", e))
    L += [dbl, center("merci de votre visite"), barcode(session_id or path),
          center((session_id or os.path.basename(path))[:36]), ""]
    return "\n".join(L)


# --- Telegram --------------------------------------------------------------------------
# Best effort copy of the ticket to a dedicated bot. Only the metadata computed above is
# sent: no prompt, reply, code, file name, path or transcript content. Token and chat id
# are read from the macOS Keychain at send time, never written anywhere else.
TG_TOKEN_SVC = "CLAUDE_TELEGRAM_BOT_TOKEN"
TG_CHAT_SVC = "CLAUDE_TELEGRAM_CHAT_ID"
TG_TIMEOUT = 5


def keychain(service):
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def keychain_store(service, value):
    """Write through `security -i` so the value never appears in a process argv."""
    user = os.environ.get("USER", "claude")
    cmd = f'add-generic-password -U -a "{user}" -s "{service}" -w "{value}"\n'
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=5)
    return r.returncode == 0 and not r.stderr.strip()


def tg_api(token, method, params=None, timeout=TG_TIMEOUT):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def tg_duration(secs):
    if secs is None:
        return "n/d"
    if secs >= 3600:
        return f"{secs // 3600} h {secs % 3600 // 60:02d}"
    if secs >= 60:
        return f"{secs // 60} min"
    return f"{secs} s"


def tg_compact(n):
    for div, unit in ((1e6, " M"), (1e3, " k")):
        if n >= div:
            v = n / div
            return (f"{v:.1f}" if v < 100 else f"{v:.0f}").replace(".", ",") + unit
    return str(n)


def telegram_text(st, session_id=""):
    """HTML message for the Telegram app: one phone screen at most, shorter when idle."""
    e = html.escape
    reqs = sum(st["models"].values())
    models = sorted(st["models"].items(), key=lambda x: -x[1])
    if len(models) == 1:
        mline = e(SHORT.get(models[0][0], models[0][0]))
    else:
        mline = " · ".join(f"{e(SHORT.get(k, k))} ×{n}" for k, n in models[:3]) or "n/d"
    tools = sum(st["tool_counts"].values())
    nskills = sum(st["skills"].values())
    workers = st["codex"] or st["gemini"] or st["jev"]
    total = st["tin"] + st["tout"] + st["cr"] + st["cw"]
    simple = reqs < 15 and not workers and not nskills

    extra = []
    if st["metered"]:
        extra.append(f"API Anthropic ${st['real']:.2f}")
    if st["jev"]:
        extra.append(f"Jev ×{st['jev']}")

    L = ["<b>🤖 Claude Orchestrator</b>",
         f"<i>Session terminée · {datetime.now().strftime('%d/%m · %H:%M')}</i>", ""]
    if simple:
        L += [f"🧠 {mline} · ⏱ {tg_duration(st['secs'])} · 🔄 {reqs} req",
              f"📊 {tg_compact(total)} tokens · 🛠 {tools} outils",
              f"💰 Équiv. API ${st['equiv']:.2f} · Facturé <b>${st['real']:.2f}</b>"]
    else:
        L += [f"🧠 {mline}", f"⏱ {tg_duration(st['secs'])} · 🔄 {reqs} requêtes", ""]
        rows = [("Input", fmt_n(st["tin"])), ("Output", fmt_n(st["tout"])),
                ("Cache", fmt_n(st["cr"] + st["cw"])), ("Total", fmt_n(total))]
        cost = [("Équiv. API", f"${st['equiv']:.2f}"), ("Facturé", f"${st['real']:.2f}")]
        w = max(len(v) for _, v in rows + cost)
        block = ["TOKENS"] + [f"{k:<10} {v:>{w}}" for k, v in rows]
        block += ["", "COÛT"] + [f"{k:<10} {v:>{w}}" for k, v in cost]
        L += ["<pre>" + e("\n".join(block)) + "</pre>", ""]
        L.append(f"🛠 {tools} appels outils")
        if nskills:
            top = sorted(st["skills"].items(), key=lambda x: -x[1])[:3]
            L.append("🧩 " + " · ".join(f"{e(n.split(':')[-1])} ×{c}" for n, c in top))
        else:
            L.append("🧩 Aucun skill")
        if workers:
            def plural(n, word):
                return f"{n} {word}{'s' if n > 1 else ''}"
            orch = ["Claude → principal",
                    f"Codex  → {plural(st['codex'], 'délégation')}",
                    f"Gemini → {plural(st['gemini'], 'délégation')}",
                    f"Jev    → {plural(st['jev'], 'appel')}"]
            L += ["", "<b>🧭 Orchestration</b>", "<pre>" + e("\n".join(orch)) + "</pre>"]
        else:
            L.append("🧭 Claude only")
    L.append("")
    if extra:
        L.append("🟠 Coût supplémentaire : " + e(" · ".join(extra)))
    else:
        L.append("🟢 Aucun coût supplémentaire")
    sid = (session_id or "")[:8]
    L += ["", f"🧾 <code>ticket {e(sid)}</code> · #ClaudeCode" if sid else "🧾 #ClaudeCode"]
    return "\n".join(L)


VENV_PY = os.path.expanduser("~/.claude/venvs/session-receipt/bin/python")  # Pillow lives here
TG_CAPTION = "Claude Orchestrator · Session complete"


def tg_photo(token, chat, png, timeout=TG_TIMEOUT * 2):
    """sendPhoto as multipart/form-data, standard library only."""
    b = "----receipt" + os.urandom(8).hex()
    parts = []
    for k, v in (("chat_id", chat), ("caption", TG_CAPTION)):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="receipt.png"\r\n'
                 f"Content-Type: image/png\r\n\r\n".encode() + png + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def tg_call(fn):
    """One retry on a network error only; a 4xx (bad token, blocked bot, bad chat) is final."""
    for attempt in (1, 2):
        try:
            r = fn()
            return "" if r.get("ok") else f"api error {r.get('error_code')}"
        except urllib.error.HTTPError as err:
            return f"http {err.code}"
        except Exception as err:
            if attempt == 2:
                return f"network {type(err).__name__}"
            time.sleep(1)
    return "unknown"


def telegram_send(text, png=None):
    """Photo ticket when a PNG is given, text message otherwise or if the photo is refused."""
    token, chat = keychain(TG_TOKEN_SVC), keychain(TG_CHAT_SVC)
    if not token or not chat:
        return "not configured"
    if png:
        err = tg_call(lambda: tg_photo(token, chat, png))
        if not err or err.startswith("network"):
            return err  # offline: a text attempt would fail the same way
        log_error(f"telegram photo: {err}, falling back to text")
    params = {"chat_id": chat, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": "true"}
    return tg_call(lambda: tg_api(token, "sendMessage", params))


def receipt_png(payload):
    """PNG bytes, or None when Pillow or the renderer is unavailable (text fallback)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import receipt_png as rp
        when = datetime.fromisoformat(payload["when"])
        return rp.render_png(payload["metrics"], payload.get("sid", ""), when)
    except Exception as err:
        log_error(f"png render failed: {type(err).__name__}")
        return None


def interactive_exit(data, path):
    """True only when a person leaves an interactive session (/exit, Ctrl+D, Ctrl+C, logout).

    Headless runs (`claude -p`, SDK, scripts) and /clear or /resume, which keep the terminal
    open on a new session, get the terminal ticket but no Telegram message.
    """
    if data.get("reason") in ("clear", "resume"):
        return False
    entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if not entry:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for _, line in zip(range(50), f):
                    m = re.search(r'"entrypoint":"([^"]*)"', line)
                    if m:
                        entry = m.group(1)
                        break
        except OSError:
            pass
    return not entry.startswith("sdk")


def telegram_payload(st, session_id=""):
    """What the detached child receives: the metrics already computed, no transcript."""
    m = dict(st, unpriced=sorted(st["unpriced"]))
    return json.dumps({"text": telegram_text(st, session_id), "metrics": m,
                       "sid": session_id or "", "when": datetime.now().isoformat()})


def telegram_spawn(payload):
    """Hand the payload to a detached child so the hook returns at once, even offline."""
    if os.environ.get("CLAUDE_TELEGRAM_RECEIPT", "1") == "0":
        return
    py = VENV_PY if os.access(VENV_PY, os.X_OK) else sys.executable
    try:
        p = subprocess.Popen([py, os.path.abspath(__file__), "--telegram-send"],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        p.stdin.write(payload.encode())
        p.stdin.close()
    except Exception as err:
        log_error(f"telegram spawn failed: {type(err).__name__}")


def telegram_setup_chat():
    """After /start in the bot: read getUpdates once and keep only the private chat id."""
    token = keychain(TG_TOKEN_SVC)
    if not token:
        return "FAIL: no token in Keychain"
    try:
        me = tg_api(token, "getMe").get("result") or {}
        ups = tg_api(token, "getUpdates", {"allowed_updates": '["message"]'}).get("result") or []
    except urllib.error.HTTPError as err:
        return f"FAIL: http {err.code}"
    except Exception as err:
        return f"FAIL: network {type(err).__name__}"
    chats = [(u.get("message") or {}).get("chat") or {} for u in ups]
    chats = [c for c in chats if c.get("type") == "private"]
    if not chats:
        return f"FAIL: no /start found, send /start to @{me.get('username', '?')} then retry"
    ok = keychain_store(TG_CHAT_SVC, str(chats[-1]["id"]))
    return f"PASS: chat id stored (bot @{me.get('username', '?')})" if ok else "FAIL: keychain write"


def terminal_device():
    """TTY of the nearest ancestor that has one.

    Claude Code starts hooks without a controlling terminal, so /dev/tty fails with
    ENXIO. The claude process itself still owns the user's terminal device.
    """
    pid = os.getppid()
    for _ in range(8):
        if pid <= 1:
            break
        out = subprocess.run(["ps", "-o", "ppid=,tty=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=2).stdout.split()
        if len(out) < 2:
            break
        if out[1] not in ("??", "?", "-"):
            dev = "/dev/" + out[1]
            if os.path.exists(dev):
                return dev
        pid = int(out[0])
    return "/dev/tty"


def log_error(msg):
    """Silent error trail for diagnosis: no prompt, no conversation content."""
    try:
        d = os.path.expanduser("~/.claude/logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "session-ticket-error.log"), "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--telegram-send":  # detached child of telegram_spawn
        payload = json.loads(sys.stdin.read())
        png = receipt_png(payload) if os.environ.get("CLAUDE_TELEGRAM_PHOTO", "1") != "0" else None
        err = telegram_send(payload["text"], png)
        if err and err != "not configured":
            log_error(f"telegram: {err}")
        return
    if arg == "--telegram-test":
        err = telegram_send("<b>🤖 Claude Orchestrator</b>\nConnexion établie ✓")
        print("TELEGRAM: PASS" if not err else f"TELEGRAM: FAIL ({err})")
        return
    if arg == "--telegram-setup-chat":
        print(telegram_setup_chat())
        return
    if arg == "--png-preview":  # <transcript> <out.png>, needs the venv python
        payload = json.loads(telegram_payload(summarize(sys.argv[2]), os.path.basename(sys.argv[2])[:8]))
        png = receipt_png(payload)
        if png:
            open(sys.argv[3], "wb").write(png)
        print("PNG: PASS" if png else "PNG: FAIL")
        return
    if arg == "--telegram-preview":
        print(telegram_text(summarize(sys.argv[2]), os.path.basename(sys.argv[2])[:8]))
        return
    if arg:
        print(render(arg))
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    path = data.get("transcript_path") or ""
    if not os.path.isfile(path):
        return
    st = summarize(path)
    text = render(path, data.get("session_id", ""), data.get("reason", ""), st)
    try:
        dev = terminal_device()
        with open(dev, "w") as tty:
            tty.write(("\n" + text + "\n").replace("\n", "\r\n"))
    except OSError as e:
        log_error(f"sid={data.get('session_id', '')} terminal write failed: {e!r}")
        sys.stderr.write(text + "\n")
    # After the ticket, never before: Telegram cannot delay or break the terminal output.
    try:
        if interactive_exit(data, path):
            telegram_spawn(telegram_payload(st, data.get("session_id", "")))
    except Exception as err:
        log_error(f"telegram build failed: {type(err).__name__}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_error(f"crash: {e!r}")
    sys.exit(0)
