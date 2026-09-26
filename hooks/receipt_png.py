#!/usr/bin/env python3
"""Thermal receipt PNG for the session receipt, drawn with Pillow.

Input is the metrics dict computed once by session_receipt.summarize(): counts, model and
skill names, costs. Nothing else reaches the image: no prompt, code, path or repository.
Local, deterministic, no network. Used by the detached Telegram child only, so the terminal
ticket never depends on Pillow.

Manual run: receipt_png.py <metrics.json> <out.png> [session_id]
"""
import json
import os
import random
import sys
import zlib
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PAPER = (250, 248, 242)
INK = (24, 24, 24)
FADED = (72, 72, 72)
BACKDROP = (32, 32, 34)

PAPER_W = 760
PAD = 58               # inner margin of the paper
EDGE = 34              # backdrop around the paper
TOOTH = 12             # torn edge depth

SHORT = {
    "claude-fable-5-1": "FABLE 5.1", "claude-mythos-5-1": "MYTHOS 5.1", "claude-fable-5": "FABLE 5",
    "claude-mythos-5": "MYTHOS 5", "claude-opus-5-5": "OPUS 5.5", "claude-opus-5": "OPUS 5",
    "claude-opus-4-8": "OPUS 4.8", "claude-opus-4-7": "OPUS 4.7", "claude-opus-4-6": "OPUS 4.6",
    "claude-opus-4-5": "OPUS 4.5", "claude-opus-4-1": "OPUS 4.1", "claude-opus-4": "OPUS 4",
    "claude-sonnet-5": "SONNET 5", "claude-sonnet-4-6": "SONNET 4.6", "claude-sonnet-4-5": "SONNET 4.5",
    "claude-sonnet-4": "SONNET 4", "claude-haiku-4-5": "HAIKU 4.5",
}

# Monospace faces shipped with macOS, first found wins; (path, regular index, bold index).
FONTS = [
    ("/System/Library/Fonts/Menlo.ttc", 0, 1),
    ("/System/Library/Fonts/SFNSMono.ttf", 0, 0),
    ("/System/Library/Fonts/Supplemental/Courier New.ttf", 0, 0),
    ("/System/Library/Fonts/Courier.ttc", 0, 1),
]


def font(size, bold=False):
    for path, reg, bld in FONTS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=bld if bold else reg)
            except OSError:
                continue
    return ImageFont.load_default(size)


def n(v):
    return f"{v:,}".replace(",", " ")


def duration(secs):
    if secs is None:
        return "N/A"
    if secs >= 3600:
        return f"{secs // 3600}H {secs % 3600 // 60:02d}"
    if secs >= 60:
        return f"{secs // 60} MIN"
    return f"{secs} S"


def code128(text):
    """Module string ('1' = bar); real Code128 when python-barcode is present."""
    try:
        from barcode.codex import Code128
        return Code128(text).build()[0]
    except Exception:
        rnd = random.Random(zlib.crc32(text.encode()))
        return "11010010000" + "".join(rnd.choice(("1", "0", "11", "00", "110")) for _ in range(70)) + "1100011101011"


class Receipt:
    """Two passes over the same layout: measure the height, then draw."""

    def __init__(self):
        self.f = {"body": font(25), "bold": font(25, True), "small": font(20),
                  "head": font(22, True), "title": font(50, True), "big": font(30, True)}
        self.ops, self.y = [], 0

    def gap(self, h):
        self.y += h

    def text_c(self, s, key="body", fill=INK, spacing=0):
        self.ops.append(("center", self.y, s, key, fill, spacing))
        self.y += self.f[key].size + 12

    def row(self, left, right, key="body", fill=INK, indent=0):
        self.ops.append(("row", self.y, left, right, key, fill, indent))
        self.y += self.f[key].size + 13

    def head(self, s):
        self.ops.append(("head", self.y, s))
        self.y += self.f["head"].size + 18

    def rule(self, style="dash"):
        self.gap(8)
        self.ops.append(("rule", self.y, style))
        self.gap(26)

    def barcode(self, modules, label):
        self.ops.append(("barcode", self.y, modules))
        self.y += 96
        self.text_c(label, "small", spacing=6)

    def draw(self, sid):
        h = self.y + 2 * PAD
        paper = Image.new("RGB", (PAPER_W, h), PAPER)
        # Paper grain: seeded low-res noise, upscaled soft, a few levels deep only.
        rnd = random.Random(zlib.crc32(sid.encode()))
        gw, gh = PAPER_W // 4, h // 4 + 1
        grain = Image.frombytes("L", (gw, gh), bytes(rnd.randrange(247, 256) for _ in range(gw * gh)))
        paper = Image.composite(paper, Image.new("RGB", paper.size, (238, 235, 227)),
                                grain.resize(paper.size, Image.BILINEAR).point(lambda v: 255 - (255 - v) * 9))
        ink = Image.new("L", paper.size, 0)
        d = ImageDraw.Draw(ink)
        left, right = PAD, PAPER_W - PAD
        for op in self.ops:
            kind, y = op[0], op[1] + PAD
            if kind == "center":
                _, _, s, key, fill, sp = op
                f = self.f[key]
                w = f.getlength(s) + sp * (len(s) - 1)
                x = (PAPER_W - w) / 2
                for ch in s:
                    d.text((x, y), ch, font=f, fill=255 - fill[0])
                    x += f.getlength(ch) + sp
            elif kind == "row":
                _, _, l, r, key, fill, ind = op
                f = self.f[key]
                d.text((left + ind, y), l, font=f, fill=255 - fill[0])
                d.text((right - f.getlength(r), y), r, font=f, fill=255 - fill[0])
            elif kind == "head":
                s = op[2]
                f = self.f["head"]
                x = left
                for ch in s:
                    d.text((x, y), ch, font=f, fill=255 - INK[0])
                    x += f.getlength(ch) + 5
            elif kind == "rule":
                style = op[2]
                if style == "dash":
                    for x in range(left, right, 14):
                        d.line((x, y, min(x + 7, right), y), fill=190, width=2)
                elif style == "double":
                    d.line((left, y - 3, right, y - 3), fill=230, width=2)
                    d.line((left, y + 3, right, y + 3), fill=230, width=2)
                else:
                    d.line((left, y, right, y), fill=230, width=2)
            elif kind == "barcode":
                mods = op[2]
                mw = max(2, int((right - left - 80) / len(mods)))
                x = (PAPER_W - mw * len(mods)) // 2
                for i, m in enumerate(mods):
                    if m == "1":
                        d.rectangle((x + i * mw, y, x + (i + 1) * mw - 1, y + 80), fill=235)
        # Thermal ink: a hair of bleed so the glyphs do not look like a screen capture.
        ink = ink.filter(ImageFilter.GaussianBlur(0.45))
        paper.paste(Image.new("RGB", paper.size, INK), (0, 0), ink)
        return frame(paper, sid)


def torn_mask(w, h, sid):
    """Paper silhouette with a zigzag tear at the top and bottom, deterministic per session."""
    rnd = random.Random(zlib.crc32(sid.encode()) ^ 0x5EED)
    pts, step = [], 16
    for x in range(0, w + step, step):
        pts.append((min(x, w), rnd.randint(0, TOOTH) if (x // step) % 2 else TOOTH // 3))
    bottom = [(min(x, w), h - (rnd.randint(0, TOOTH) if (x // step) % 2 else TOOTH // 3))
              for x in reversed(range(0, w + step, step))]
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon(pts + bottom, fill=255)
    return m


def frame(paper, sid):
    w, h = paper.size
    W, H = w + 2 * EDGE, h + 2 * EDGE
    out = Image.new("RGB", (W, H), BACKDROP)
    mask = torn_mask(w, h, sid)
    shadow = Image.new("L", (W, H), 0)
    shadow.paste(mask, (EDGE + 4, EDGE + 10))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14)).point(lambda v: v * 0.55)
    out.paste(Image.new("RGB", (W, H), (8, 8, 9)), (0, 0), shadow)
    out.paste(paper, (EDGE, EDGE), mask)
    return out


def build(st, session_id="", when=None):
    sid = (session_id or "00000000")[:8].upper()
    when = when or datetime.now()
    r = Receipt()
    r.gap(14)
    r.text_c("CLAUDE", "title", spacing=10)
    r.text_c("ORCHESTRATOR", "title", spacing=4)
    r.gap(4)
    r.text_c("SESSION RECEIPT", "small", FADED, spacing=7)
    r.gap(18)
    r.row(when.strftime("%d/%m/%Y"), when.strftime("%H:%M"))
    r.row("SESSION", f"#{sid}")
    r.rule()

    models = sorted(st["models"].items(), key=lambda x: -x[1])
    for k, c in models[:3]:
        r.row(SHORT.get(k, k.upper())[:22], f"{c} REQ", "bold")
    if not models:
        r.row("NO MODEL CALL", "0 REQ", "bold")
    r.rule()

    tin, tout, cr, cw = st["tin"], st["tout"], st["cr"], st["cw"]
    r.head("TOKENS")
    r.row("INPUT", n(tin))
    r.row("OUTPUT", n(tout))
    r.row("CACHE READ", n(cr))
    r.row("CACHE WRITE", n(cw))
    r.rule("line")
    r.row("TOTAL", n(tin + tout + cr + cw), "bold")
    r.rule()

    tools = st["tool_counts"]
    skills = st["skills"]
    r.head("ACTIVITY")
    r.row("TOOLS", n(sum(tools.values())))
    for name, c in sorted(tools.items(), key=lambda x: -x[1])[:3]:
        label = name.split("__")[-1] if name.startswith("mcp__") else name
        r.row(label[:24], f"x{c}", "small", FADED, indent=24)
    r.row("SKILLS", n(sum(skills.values())))
    for name, c in sorted(skills.items(), key=lambda x: -x[1])[:3]:
        r.row(name.split(":")[-1][:24], f"x{c}", "small", FADED, indent=24)
    workers = st["codex"] or st["gemini"] or st["jev"]
    if workers:
        r.rule()
        r.head("ORCHESTRATION")
        r.row("CLAUDE", "MAIN", "bold")
        for label, key in (("CODEX", "codex"), ("GEMINI", "gemini"), ("JEV", "jev")):
            r.row(label, str(st[key]), "bold" if st[key] else "body", INK if st[key] else FADED)
    else:
        r.row("CODEX", "0")
        r.row("GEMINI", "0")
        r.row("JEV", "0")
    r.rule()

    r.row("SESSION TIME", duration(st.get("secs")))
    r.rule()

    r.row("API EQUIVALENT", f"${st['equiv']:.2f}", "body", FADED)
    r.row("reference only, not billed", "", "small", FADED)
    r.rule("double")
    r.row("ACTUALLY BILLED", f"${st['real']:.2f}", "big")
    r.rule("double")
    extra = st["metered"] or st["jev"]
    r.gap(4)
    r.text_c("METERED USAGE" if extra else "MAX PLAN  ✓", "bold", spacing=4)
    r.rule()

    r.gap(6)
    r.text_c("THANK YOU", "big", spacing=8)
    r.text_c("CLAUDE ORCHESTRATOR", "small", FADED, spacing=4)
    r.gap(20)
    r.barcode(code128(sid), sid)
    return r.draw(sid)


def render_png(st, session_id="", when=None):
    import io
    buf = io.BytesIO()
    build(st, session_id, when).save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


if __name__ == "__main__":
    st = json.load(open(sys.argv[1]))
    with open(sys.argv[2], "wb") as f:
        f.write(render_png(st, sys.argv[3] if len(sys.argv) > 3 else ""))
