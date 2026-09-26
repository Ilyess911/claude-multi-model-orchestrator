#!/usr/bin/env python3
"""Retro thermal receipt PNG for the session receipt: printed line by line on a 42-column
grid, then turned into a physical slip of paper (grain, folds, bow, tilt, torn edge, shadow).

Input is the metrics dict computed once by session_receipt.summarize(): counts, model and
skill names, costs. Nothing else reaches the image: no prompt, code, path or repository.
Local and deterministic: every imperfection is drawn from a generator seeded with the
session id, so a session always gives the same ticket and two sessions never quite match.
Used by the detached Telegram child only (Pillow and numpy live in its venv).

Manual run: receipt_png.py <metrics.json> <out.png> [session_id]
"""
import io
import json
import math
import sys
import zlib
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PAPER_RGB = np.array([247, 244, 236], np.float32)   # thermal paper, faintly warm
BACK_RGB = np.array([234, 231, 223], np.float32)    # reverse side, seen on a folded corner
INK_RGB = np.array([36, 35, 33], np.float32)        # softened black
BACKDROP = (198, 196, 191)                         # neutral desk grey; Telegram flattens alpha to white

SIZE = 26              # body size in px, Andale Mono advance 16 px
PAPER_W = 780
PAD_X = 50
PAD_TOP = 62
PAD_BOTTOM = 56
MARGIN = 56            # canvas around the paper, room for tilt and shadow

FONTS = [  # monospace faces shipped with macOS, first found wins
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

SHORT = {
    "claude-fable-5-1": "FABLE 5.1", "claude-mythos-5-1": "MYTHOS 5.1", "claude-fable-5": "FABLE 5",
    "claude-mythos-5": "MYTHOS 5", "claude-opus-5-5": "OPUS 5.5", "claude-opus-5": "OPUS 5",
    "claude-opus-4-8": "OPUS 4.8", "claude-opus-4-7": "OPUS 4.7", "claude-opus-4-6": "OPUS 4.6",
    "claude-opus-4-5": "OPUS 4.5", "claude-opus-4-1": "OPUS 4.1", "claude-opus-4": "OPUS 4",
    "claude-sonnet-5": "SONNET 5", "claude-sonnet-4-6": "SONNET 4.6", "claude-sonnet-4-5": "SONNET 4.5",
    "claude-sonnet-4": "SONNET 4", "claude-haiku-4-5": "HAIKU 4.5",
}


def load_font(size):
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def num(v):
    return f"{v:,}"


def duration(secs):
    if secs is None:
        return "N/A"
    if secs >= 3600:
        return f"{secs // 3600}H{secs % 3600 // 60:02d}"
    return f"{secs // 60}M{secs % 60:02d}S"


def code128(text):
    """Module string, '1' = bar. Real Code128 via python-barcode, else a seeded pattern."""
    try:
        from barcode.codex import Code128
        return Code128(text).build()[0]
    except Exception:
        r = np.random.default_rng(zlib.crc32(text.encode()))
        return "11010010000" + "".join(r.choice(["1", "0", "11", "00"], 60)) + "1100011101011"


# --- 1. Print: lines on a character grid --------------------------------------------------

class Tape:
    """Lines as a thermal printer emits them: text, gap or barcode."""

    def __init__(self):
        self.font = {1: load_font(SIZE), 2: load_font(SIZE * 2), 0.8: load_font(int(SIZE * 0.8))}
        self.adv = {k: f.getlength("M") for k, f in self.font.items()}
        self.cols = {k: int((PAPER_W - 2 * PAD_X) // a) for k, a in self.adv.items()}
        self.lines = []

    def text(self, s, scale=1, bold=False, tone=1.0, align="left", key=False):
        c = self.cols[scale]
        s = s[:c]
        s = s.center(c).rstrip() if align == "center" else s
        self.lines.append(("text", s, scale, bold, tone, key))

    def lead(self, label, value, bold=False, tone=1.0, indent=0, key=True):
        """LABEL ........ VALUE, the dotted leader of old register slips."""
        c = self.cols[1]
        label = " " * indent + label
        dots = c - len(label) - len(value) - 2
        if dots < 2:
            label = label[: c - len(value) - 4]
            dots = c - len(label) - len(value) - 2
        self.text(f"{label} {'.' * dots} {value}", 1, bold, tone, key=key)

    def rule(self, ch="-"):
        c = self.cols[1]
        s = (". " * c)[:c] if ch == "." else ch * c
        self.lines.append(("text", s, 1, False, 0.8 if ch in ".-" else 1.0, False))

    def gap(self, px):
        self.lines.append(("gap", px))

    def barcode(self, sid):
        self.lines.append(("barcode", code128(sid)))

    def height(self, line):
        if line[0] == "gap":
            return line[1]
        if line[0] == "barcode":
            return 104
        return int(self.font[line[2]].size * 1.42)

    def print_ink(self, rng):
        """Ink coverage (0..1), per-row head pressure, and rows holding key figures."""
        h = PAD_TOP + sum(self.height(l) for l in self.lines) + PAD_BOTTOM
        ink = Image.new("L", (PAPER_W, h), 0)
        d = ImageDraw.Draw(ink)
        dens = np.ones(h, np.float32)
        keep = np.zeros(h, bool)
        y = PAD_TOP
        for line in self.lines:
            lh = self.height(line)
            if line[0] == "text":
                _, s, scale, bold, tone, key = line
                lo = 0.93 if key or bold else 0.84      # key figures stay near full density
                dens[y:y + lh] = tone * rng.uniform(lo, 1.0)
                stroke = (2 if scale == 2 else 1) if bold else 0
                d.text((PAD_X, y), s, font=self.font[scale], fill=255, stroke_width=stroke, stroke_fill=255)
                if key or bold:
                    keep[y:y + lh] = True
            elif line[0] == "barcode":
                mods = line[1]
                mw = max(3, int((PAPER_W - 2 * PAD_X - 20) / len(mods)))
                x0 = (PAPER_W - mw * len(mods)) // 2
                for i, m in enumerate(mods):
                    if m == "1":
                        v = int(255 * rng.uniform(0.86, 1.0))
                        d.rectangle((x0 + i * mw, y, x0 + (i + 1) * mw - 1, y + 84), fill=v)
            y += lh
        return np.asarray(ink, np.float32) / 255.0, dens, keep


# --- 2. Paper, ink and thermal defects ------------------------------------------------------

def smooth_noise(rng, h, w, cell, amp):
    """Low-frequency noise in [-amp, amp]: random grid upscaled bicubic."""
    gh, gw = max(2, h // cell + 2), max(2, w // cell + 2)
    g = Image.fromarray((rng.random((gh, gw)) * 255).astype(np.uint8))
    a = np.asarray(g.resize((w, h), Image.BICUBIC), np.float32) / 255.0
    return (a - 0.5) * 2 * amp


def thermal_ink(ink, dens, keep, rng):
    h, w = ink.shape
    a = ink * dens[:, None]
    ramp = np.linspace(0, 1, w, dtype=np.float32)
    a *= 1.0 - (0.05 + 0.04 * rng.random()) * (ramp if rng.random() < 0.5 else ramp[::-1])[None, :]
    patch = 1.0 + smooth_noise(rng, h, w, 22, 0.10)           # a few lighter glyphs
    patch = np.where(keep[:, None], np.maximum(patch, 0.95), patch)
    a *= np.clip(patch, 0.8, 1.05)
    a *= 0.88 + 0.12 * rng.random((h, w), np.float32)         # dot-level unevenness
    for _ in range(rng.integers(1, 3)):                       # a weak heater row
        r = int(rng.integers(PAD_TOP, h - PAD_BOTTOM))
        a[r:r + 1] *= 0.93 if keep[r] else 0.62
    img = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.55))
    return np.clip(np.asarray(img, np.float32) / 255.0 * 1.06, 0, 1)


def paper_tone(h, w, rng):
    base = np.ones((h, w), np.float32)
    fib = Image.fromarray((rng.random((h, w // 7 + 1)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    base += (np.asarray(fib, np.float32) / 255.0 - 0.5) * 0.022        # fibres
    base += (rng.random((h, w), np.float32) - 0.5) * 0.018              # grain
    base += smooth_noise(rng, h, w, 160, 0.012)                         # whiteness drift
    rgb = PAPER_RGB[None, None, :] * base[..., None]
    for _ in range(rng.integers(4, 9)):                                 # micro-marks
        cy, cx = int(rng.integers(4, h - 4)), int(rng.integers(4, w - 4))
        r = rng.uniform(0.6, 1.4)
        yy, xx = np.ogrid[cy - 3:cy + 4, cx - 3:cx + 4]
        m = np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r * r)))[..., None]
        rgb[cy - 3:cy + 4, cx - 3:cx + 4] *= 1 - 0.18 * m
    return rgb


# --- 3. Shape: cut top, torn bottom, uneven sides, maybe a folded corner --------------------

def outline(h, w, rng):
    s = 2  # supersampled polygon, then downscaled for clean antialiased edges
    tilt = rng.uniform(-3, 3)
    side = smooth_noise(rng, h, 2, 90, 1.6)
    pts = [(x, 3.5 + tilt * x / w + rng.uniform(-0.4, 0.4)) for x in np.linspace(0, w, 40)]
    pts += [(w - abs(side[int(y), 1]), y) for y in np.linspace(0, h - 1, 60)[1:-1]]
    x = w
    while x > 0:                                    # thermal tear bar: fine uneven teeth
        step = rng.uniform(7, 12)
        pts += [(x, h - rng.uniform(0, 2)), (x - step / 2, h - rng.uniform(5, 10))]
        x -= step
    pts.append((0, h - 3))
    pts += [(abs(side[int(y), 0]), y) for y in np.linspace(h - 1, 0, 60)[1:-1]]
    m = Image.new("L", (w * s, h * s), 0)
    ImageDraw.Draw(m).polygon([(px * s, py * s) for px, py in pts], fill=255)
    return np.asarray(m.resize((w, h), Image.LANCZOS), np.float32) / 255.0


def dog_ear(rgb, mask, rng):
    """A top corner folded over: corner cut away, reverse-side flap inside, soft shadow."""
    h, w = mask.shape
    s = int(rng.uniform(38, 58))
    r = 2 * s
    right = rng.random() < 0.5
    b, x = np.mgrid[0:r, 0:r].astype(np.float32)
    a = (r - 1 - x) if right else x                 # distance from the side edge
    cols = slice(w - r, w) if right else slice(0, r)
    cut = a + b < s
    flap = (a < s) & (b < s) & ~cut
    dist = np.maximum(np.maximum(a - s, b - s), 0)
    shadow = np.where(flap | cut, 0, np.exp(-dist / 6.0))
    sub = rgb[0:r, cols]
    sub *= (1 - 0.2 * shadow)[..., None]
    shade = 0.9 + 0.08 * (a + b - s) / s
    shade = np.where(np.abs(a + b - s) < 1.2, shade * 0.9, shade)
    sub[flap] = BACK_RGB * shade[flap][:, None]
    mask[0:r, cols][cut] = 0.0


# --- 4. Light: creases, waves, raking light --------------------------------------------------

def fold_profile(d, width, amp):
    """A crease under raking light: bright flank, dark flank, a thin sharp valley."""
    z = d / width
    return (amp * 1.6 * z * np.exp(-z * z) + amp * 0.3 * np.tanh(d / (width * 3))
            - 0.075 * np.exp(-(d / 1.4) ** 2) + 0.035 * np.exp(-((d - 2.6) / 1.7) ** 2))


def light_field(h, w, rng):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    f = np.ones((h, w), np.float32)
    f += smooth_noise(rng, h, w, 120, 0.026)                                  # not quite flat
    f += smooth_noise(rng, h, w, 45, 0.010)                                   # micro-waves
    f += 0.012 * np.sin(yy / h * math.pi * rng.uniform(1.5, 3) + rng.uniform(0, 6))
    f += (xx / w - 0.5) * rng.uniform(-0.03, 0.03)                            # light from a side
    folds = []
    for _ in range(rng.integers(1, 3)):                                       # horizontal creases
        yf = rng.uniform(0.22, 0.8) * h
        slope = rng.uniform(-0.03, 0.03)
        wob = smooth_noise(rng, 1, w, 70, 2.0)[0]
        d = yy - (yf + slope * (xx - w / 2) + wob[None, :])
        f += fold_profile(d, rng.uniform(16, 30), rng.uniform(0.06, 0.09))
        folds.append((yf, rng.uniform(0.6, 1.2)))
    if rng.random() < 0.6:                                                    # short diagonal crease
        ang = rng.uniform(0.35, 0.9) * (1 if rng.random() < 0.5 else -1)
        cx, cy = rng.uniform(0.2, 0.8) * w, rng.uniform(0.15, 0.85) * h
        n = (xx - cx) * math.sin(ang) - (yy - cy) * math.cos(ang)
        t = (xx - cx) * math.cos(ang) + (yy - cy) * math.sin(ang)
        f += fold_profile(n, 12, 0.06) * np.exp(-(t / rng.uniform(110, 190)) ** 2)
    return np.clip(f, 0.86, 1.04), folds


# --- 5. Geometry: tilt, bow, keystone, crease pinch -------------------------------------------

def sample(img, xs, ys):
    """Bilinear sampling of an (H, W, C) array at float coords; outside is zero."""
    h, w, c = img.shape
    pad = np.zeros((h + 2, w + 2, c), np.float32)
    pad[1:-1, 1:-1] = img
    xs, ys = xs + 1, ys + 1
    x0 = np.clip(np.floor(xs).astype(np.int32), 0, w)
    y0 = np.clip(np.floor(ys).astype(np.int32), 0, h)
    fx, fy = np.clip(xs - x0, 0, 1)[..., None], np.clip(ys - y0, 0, 1)[..., None]
    return (pad[y0, x0] * (1 - fx) * (1 - fy) + pad[y0, x0 + 1] * fx * (1 - fy)
            + pad[y0 + 1, x0] * (1 - fx) * fy + pad[y0 + 1, x0 + 1] * fx * fy)


def warp(rgba, folds, rng):
    h, w, _ = rgba.shape
    W, H = w + 2 * MARGIN, h + 2 * MARGIN
    theta = math.radians(rng.uniform(0.25, 0.6) * (1 if rng.random() < 0.5 else -1))
    bow = rng.uniform(1.5, 3.5) * (1 if rng.random() < 0.5 else -1)
    keystone = rng.uniform(-0.004, 0.004)
    v, u = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = u - W / 2, v - H / 2
    x = u * math.cos(theta) + v * math.sin(theta)
    y = -u * math.sin(theta) + v * math.cos(theta)
    yn = np.clip((y + h / 2) / h, 0, 1)
    x = x * (1 + keystone * (yn - 0.5)) - bow * np.sin(math.pi * yn)
    for yf, b in folds:
        y = y - b * np.tanh((y + h / 2 - yf) / 6.0)
    return sample(rgba, x + w / 2, y + h / 2)


# --- 6. Content ----------------------------------------------------------------------------

def layout(st, sid, when):
    t = Tape()
    c = t.cols[1]
    t.rule("=")
    t.gap(10)
    t.text("CLAUDE SYSTEMS", 0.8, tone=0.8, align="center")
    t.gap(4)
    t.text("CLAUDE", 2, bold=True, align="center")
    t.text("ORCHESTRATOR", 2, bold=True, align="center")
    t.text("TERMINAL 01 * SESSION RECEIPT", 0.8, tone=0.85, align="center")
    t.gap(10)
    t.rule("=")
    t.lead("SYS", "ONLINE", key=False)
    t.lead("ROUTER", "ACTIVE", key=False)
    date, clock = when.strftime("%d %b %Y").upper(), when.strftime("%H:%M")
    t.text(date + " " * (c - len(date) - len(clock)) + clock)
    t.lead("SESSION", sid)
    t.rule("-")

    t.text("MODEL" + " " * (c - 8) + "REQ", tone=0.75)
    for k, n in sorted(st["models"].items(), key=lambda x: -x[1])[:3] or [("NONE", 0)]:
        t.lead(SHORT.get(k, k.upper())[:24], str(n), bold=True)
    t.rule("-")

    tin, tout, cr, cw = st["tin"], st["tout"], st["cr"], st["cw"]
    t.text("TOKENS", bold=True)
    t.lead("INPUT", num(tin))
    t.lead("OUTPUT", num(tout))
    t.lead("CACHE READ", num(cr))
    t.lead("CACHE WRITE", num(cw))
    t.rule(".")
    t.lead("TOTAL TOKENS", num(tin + tout + cr + cw), bold=True)
    t.rule("-")

    tools, skills = st["tool_counts"], st["skills"]
    t.text("ACTIVITY", bold=True)
    t.lead("TOOLS", num(sum(tools.values())))
    for name, n in sorted(tools.items(), key=lambda x: -x[1])[:3]:
        label = name.split("__")[-1] if name.startswith("mcp__") else name
        t.lead(label.upper()[:22], f"x{n}", tone=0.72, indent=2, key=False)
    t.lead("SKILLS", num(sum(skills.values())))
    for name, n in sorted(skills.items(), key=lambda x: -x[1])[:3]:
        t.lead(name.split(":")[-1].upper()[:22], f"x{n}", tone=0.72, indent=2, key=False)
    t.rule("-")

    workers = st["codex"] or st["gemini"] or st["jev"]
    t.text("ROUTING", bold=True)
    t.lead("CLAUDE", "MAIN")
    for label, key in (("CODEX", "codex"), ("GEMINI", "gemini"), ("JEV", "jev")):
        n = st[key]
        t.lead(label, str(n), bold=bool(n), tone=1.0 if n else 0.72)
    if not workers:
        t.text("CLAUDE ONLY SESSION", 0.8, tone=0.75, align="center")
    t.rule("-")
    t.lead("DURATION", duration(st.get("secs")))
    t.rule("=")

    extra = (["ANTHROPIC API"] if st["metered"] else []) + ([f"JEV x{st['jev']}"] if st["jev"] else [])
    t.text("BILLING", bold=True)
    t.lead("SUBTOTAL / API EQUIV", f"${st['equiv']:.2f}", tone=0.72)
    t.text("  * REFERENCE VALUE ONLY, NEVER CHARGED", 0.8, tone=0.7)
    t.lead("ACTUALLY CHARGED", f"${st['real']:.2f}")
    t.rule(".")
    total, label, big = f"${st['real']:.2f}", "TOTAL CHARGED", t.cols[2]
    if len(label) + len(total) + 1 <= big:
        t.gap(4)
        t.text(label + " " * (big - len(label) - len(total)) + total, 2, bold=True)
    else:
        t.lead(label, total, bold=True)
    t.rule("=")
    t.lead("PLAN", "API KEY" if st["metered"] else "CLAUDE MAX")
    t.lead("STATUS", "OK")
    t.gap(6)
    t.text("** EXTRA: " + ", ".join(extra) + " **" if extra else "** NO ADDITIONAL CHARGE **",
           bold=True, align="center")
    t.gap(6)
    t.rule("-")
    t.gap(10)
    t.text("SESSION CLOSED SUCCESSFULLY", tone=0.9, align="center")
    t.gap(14)
    t.text("THANK YOU FOR", bold=True, align="center")
    t.text("COMPUTING", bold=True, align="center")
    t.gap(4)
    t.text("CLAUDE SYSTEMS  *  EST. 2026", 0.8, tone=0.8, align="center")
    t.gap(22)
    t.barcode(sid)
    t.text(" ".join(sid), tone=0.95, align="center")
    t.gap(12)
    t.text("HUMAN OPERATED  ·  MACHINE ASSISTED", 0.8, tone=0.6, align="center")
    return t


# --- 7. Assembly --------------------------------------------------------------------------

def build(st, session_id="", when=None):
    sid = (session_id or "00000000")[:8].upper()
    rng = np.random.default_rng(zlib.crc32(sid.encode()))
    when = when or datetime.now()

    ink, dens, keep = layout(st, sid, when).print_ink(rng)
    h, w = ink.shape
    ink = thermal_ink(ink, dens, keep, rng)[..., None]
    rgb = paper_tone(h, w, rng) * (1 - ink) + INK_RGB * ink
    mask = outline(h, w, rng)
    if rng.random() < 0.45:
        dog_ear(rgb, mask, rng)
    light, folds = light_field(h, w, rng)
    rgb *= light[..., None]
    edge = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)),
                      np.float32) / 255.0
    rgb *= (0.955 + 0.045 * np.clip((edge - 0.5) * 2, 0, 1))[..., None]        # worn edges

    out = warp(np.dstack([rgb * mask[..., None], mask]), folds, rng)          # premultiplied
    a = np.clip(out[..., 3], 0, 1)
    col = out[..., :3] / np.maximum(a, 1e-4)[..., None]

    am = Image.fromarray((a * 255).astype(np.uint8))
    soft = np.roll(np.asarray(am.filter(ImageFilter.GaussianBlur(16)), np.float32) / 255.0, (10, 4), (0, 1))
    near = np.roll(np.asarray(am.filter(ImageFilter.GaussianBlur(2.5)), np.float32) / 255.0, (2, 1), (0, 1))
    sh = np.clip(0.30 * soft + 0.22 * near, 0, 0.6)                           # a few mm above the desk

    if BACKDROP is None:
        alpha = a + sh * (1 - a)
        rgb_out = (col * a[..., None] + np.float32(12) * (sh * (1 - a))[..., None]) \
            / np.maximum(alpha, 1e-4)[..., None]
        arr = np.dstack([np.clip(rgb_out, 0, 255), alpha * 255]).astype(np.uint8)
        return Image.fromarray(arr, "RGBA")
    bg = np.array(BACKDROP, np.float32)[None, None, :] * (1 - sh[..., None])
    arr = col * a[..., None] + bg * (1 - a[..., None])
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def render_png(st, session_id="", when=None):
    buf = io.BytesIO()
    build(st, session_id, when).save(buf, "PNG", compress_level=6)
    return buf.getvalue()


if __name__ == "__main__":
    st = json.load(open(sys.argv[1]))
    with open(sys.argv[2], "wb") as f:
        f.write(render_png(st, sys.argv[3] if len(sys.argv) > 3 else ""))
