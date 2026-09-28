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
RED_RGB = np.array([176, 38, 42], np.float32)       # second head of a two-colour kitchen printer
STAMP_RGB = np.array([150, 30, 70], np.float32)     # rubber stamp, violet-red pad ink
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

    def text(self, s, scale=1, bold=False, tone=1.0, align="left", key=False, red=False, tag=None):
        c = self.cols[scale]
        s = s[:c]
        s = s.center(c).rstrip() if align == "center" else s
        self.lines.append(("text", s, scale, bold, tone, key, red, tag))

    def lead(self, label, value, bold=False, tone=1.0, indent=0, key=True, red=False):
        """LABEL ........ VALUE, the dotted leader of old register slips."""
        c = self.cols[1]
        label = " " * indent + label
        dots = c - len(label) - len(value) - 2
        if dots < 2:
            label = label[: c - len(value) - 4]
            dots = c - len(label) - len(value) - 2
        self.text(f"{label} {'.' * dots} {value}", 1, bold, tone, key=key, red=red)

    def split(self, left, right, scale=1, **kw):
        c = self.cols[scale]
        self.text(left + " " * max(1, c - len(left) - len(right)) + right, scale, **kw)

    def item(self, qty, name, price, tone=1.0, bold=False):
        """QTY  DESIGNATION            PRICE, the column layout of a restaurant bill."""
        c = self.cols[1]
        room = c - 7 - len(price) - 1
        self.text(f"{qty:>5}  {name[:room]:<{room}} {price}", tone=tone, bold=bold, key=True)

    def logo(self):
        self.lines.append(("logo",))

    def rule(self, ch="-"):
        c = self.cols[1]
        s = (". " * c)[:c] if ch == "." else ch * c
        self.lines.append(("text", s, 1, False, 0.8 if ch in ".-" else 1.0, False, False, None))

    def gap(self, px):
        self.lines.append(("gap", px))

    def barcode(self, sid):
        self.lines.append(("barcode", code128(sid)))

    def height(self, line):
        if line[0] == "gap":
            return line[1]
        if line[0] == "barcode":
            return 104
        if line[0] == "logo":
            return 92
        return int(self.font[line[2]].size * 1.42)

    def print_ink(self, rng):
        """Black and red ink coverage (0..1), per-row head pressure, key rows, tagged rows."""
        h = PAD_TOP + sum(self.height(l) for l in self.lines) + PAD_BOTTOM
        ink = Image.new("L", (PAPER_W, h), 0)
        red = Image.new("L", (PAPER_W, h), 0)
        dk, dr = ImageDraw.Draw(ink), ImageDraw.Draw(red)
        dens = np.ones(h, np.float32)
        keep = np.zeros(h, bool)
        tags = {}
        y = PAD_TOP
        for line in self.lines:
            lh = self.height(line)
            if line[0] == "text":
                _, s, scale, bold, tone, key, is_red, tag = line
                lo = 0.93 if key or bold else 0.84      # key figures stay near full density
                dens[y:y + lh] = tone * rng.uniform(lo, 1.0)
                stroke = (2 if scale == 2 else 1) if bold else 0
                (dr if is_red else dk).text((PAD_X, y), s, font=self.font[scale], fill=255,
                                            stroke_width=stroke, stroke_fill=255)
                if key or bold:
                    keep[y:y + lh] = True
                if tag:
                    tags[tag] = y + lh // 2
            elif line[0] == "logo":                     # serving cloche, printed in red
                cx, base = PAPER_W // 2, y + 70
                dr.pieslice((cx - 52, base - 52, cx + 52, base + 52), 180, 360, outline=255, width=5)
                dr.ellipse((cx - 8, base - 66, cx + 8, base - 50), fill=255)
                dr.rounded_rectangle((cx - 70, base + 2, cx + 70, base + 9), 3, fill=255)
                dr.arc((cx - 36, base - 38, cx + 20, base + 20), 200, 250, fill=255, width=4)
                keep[y:y + lh] = True
            elif line[0] == "barcode":
                mods = line[1]
                mw = max(3, int((PAPER_W - 2 * PAD_X - 20) / len(mods)))
                x0 = (PAPER_W - mw * len(mods)) // 2
                for i, m in enumerate(mods):
                    if m == "1":
                        v = int(255 * rng.uniform(0.86, 1.0))
                        dk.rectangle((x0 + i * mw, y, x0 + (i + 1) * mw - 1, y + 84), fill=v)
            y += lh
        f = lambda im: np.asarray(im, np.float32) / 255.0
        return f(ink), f(red), dens, keep, tags


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
    f += crumple(h, w, rng)
    return np.clip(f, 0.80, 1.06), folds


def crumple(h, w, rng):
    """Balled up then flattened: Voronoi facets, each a slightly tilted plane under raking
    light, so every facet border reads as a soft crease, plus a few sharp short creases."""
    n = int(rng.integers(26, 40))
    pts = np.stack([rng.uniform(-0.05, 1.05, n) * w, rng.uniform(-0.02, 1.02, n) * h], 1)
    s = 4                                                   # facets solved on a coarse grid
    yy, xx = np.mgrid[0:h:s, 0:w:s].astype(np.float32)
    d = (xx[..., None] - pts[:, 0]) ** 2 + (yy[..., None] - pts[:, 1]) ** 2
    idx = np.argmin(d, -1)
    gx, gy = rng.normal(0, 0.00026, n), rng.normal(0, 0.00016, n)
    plane = gx[idx] * (xx - pts[idx, 0]) + gy[idx] * (yy - pts[idx, 1]) + rng.normal(0, 0.022, n)[idx]
    img = Image.fromarray(((np.clip(plane, -0.1, 0.1) + 0.1) * 1275).astype(np.uint8))
    img = img.resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(2.2))
    f = np.asarray(img, np.float32) / 1275 - 0.1
    Y, X = np.mgrid[0:h, 0:w].astype(np.float32)
    for _ in range(rng.integers(9, 15)):                    # short sharp creases
        ang = rng.uniform(0, math.pi)
        cx, cy = rng.uniform(0.05, 0.95) * w, rng.uniform(0.03, 0.97) * h
        nrm = (X - cx) * math.sin(ang) - (Y - cy) * math.cos(ang)
        tan = (X - cx) * math.cos(ang) + (Y - cy) * math.sin(ang)
        f += fold_profile(nrm, rng.uniform(5, 12), rng.uniform(0.045, 0.085)) \
            * np.exp(-(tan / rng.uniform(60, 220)) ** 2)
    return f


def rubber_stamp(h, w, cy, rng, top="PAYÉ", bottom="ABONNEMENT CLAUDE MAX"):
    """Rubber stamp coverage, inked unevenly and pressed at a slant near row cy."""
    big, small = load_font(88), load_font(22)
    sw, sh = 440, 190
    im = Image.new("L", (sw, sh), 0)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6, 6, sw - 7, sh - 7), 16, outline=255, width=7)
    d.rounded_rectangle((20, 20, sw - 21, sh - 21), 10, outline=255, width=3)
    d.text((sw / 2, 88), top, font=big, fill=255, anchor="mm", stroke_width=2, stroke_fill=255)
    d.text((sw / 2, 150), bottom, font=small, fill=255, anchor="mm")
    im = im.rotate(rng.uniform(8, 15) * (1 if rng.random() < 0.5 else -1), Image.BICUBIC, expand=True)
    a = np.asarray(im, np.float32) / 255.0
    ih, iw = a.shape
    press = 0.62 + smooth_noise(rng, ih, iw, 60, 0.3) + smooth_noise(rng, ih, iw, 8, 0.2)
    a = a * np.clip(press, 0, 1) * (rng.random((ih, iw)) > 0.06)       # dry patches, pinholes
    a = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8)),
                   np.float32) / 255.0
    out = np.zeros((h, w), np.float32)
    x0 = int(w * rng.uniform(0.60, 0.70) - iw / 2)
    y0 = int(cy + rng.uniform(60, 110) - ih / 2)
    ys, xs = slice(max(0, y0), min(h, y0 + ih)), slice(max(0, x0), min(w, x0 + iw))
    out[ys, xs] = a[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
    return out * 0.82


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
    x = x + smooth_noise(rng, H, W, 140, 1.6)                                # crumple relief
    y = y + smooth_noise(rng, H, W, 140, 1.2)
    return sample(rgba, x + w / 2, y + h / 2)


# --- 6. Content ----------------------------------------------------------------------------

def compact(n):
    for div, unit in ((1e9, "G"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            return f"{v:.1f}{unit}" if v < 100 else f"{v:.0f}{unit}"
    return str(n)


def usd(v):
    return f"{v:.2f}"


def short_tool(name):
    if name.startswith("mcp__"):
        parts = name.split("__")
        return (parts[1].replace("claude_ai_", "") + ":" + parts[-1]) if len(parts) > 2 else parts[-1]
    return name


def section(t, title):
    t.gap(6)
    t.text(title, bold=True, red=True)


def layout(st, sid, when):
    """A restaurant bill: every model, token line, tool, skill and worker gets its own row."""
    t = Tape()
    t.logo()
    t.text("CHEZ CLAUDE", 2, bold=True, align="center")
    t.text("BISTROT COMPUTATIONNEL  *  DEPUIS 2026", 0.8, tone=0.85, align="center")
    t.text("1 RUE DU TERMINAL  -  75000 LOCALHOST", 0.8, tone=0.7, align="center")
    t.gap(8)
    t.rule("=")
    reqs = sum(st["models"].values())
    t.split("TABLE 01", f"COUVERTS {len(st['models']) or 1}")
    t.split("SERVEUR  CLAUDE", f"CAISSE {'ROUTER' if st.get('routes') else 'MAIN'}", tone=0.85)
    t.split(when.strftime("%d/%m/%Y  %H:%M"), f"N° {sid}", tone=0.85)
    t.rule("=")
    t.split("QTE  DÉSIGNATION", "PRIX $", tone=0.75)
    t.rule("-")

    bill = st.get("bill") or {}
    for model in sorted(bill, key=lambda k: -sum(v[1] for v in bill[k].values())):
        b = bill[model]
        section(t, f"MENU {SHORT.get(model, model.upper())[:18]}  x{st['models'].get(model, 0)} REQ")
        for key, label in (("in", "Entrée, prompt brut"), ("cr", "Contexte réchauffé"),
                           ("cw", "Contexte mis en cache"), ("out", "Plat, tokens générés"),
                           ("web", "Recherche web")):
            n, v = b.get(key, (0, 0.0))
            if n or key in ("in", "out"):
                t.item(compact(n), label, usd(v), bold=key == "out")
    if not bill:
        section(t, "MENU")
        t.item("0", "Aucune requête", "0.00", tone=0.7)

    tools = sorted(st["tool_counts"].items(), key=lambda x: -x[1])
    section(t, f"LA CUISINE  *  OUTILS ({num(sum(n for _, n in tools))})")
    for name, n in tools[:9]:
        t.item(str(n), short_tool(name), "incl.", tone=0.85)
    if len(tools) > 9:
        t.item(str(sum(n for _, n in tools[9:])), f"Autres outils ({len(tools) - 9})", "incl.", tone=0.72)
    if not tools:
        t.item("0", "Rien en cuisine", "-", tone=0.7)

    skills = sorted(st["skills"].items(), key=lambda x: -x[1])
    section(t, f"SPÉCIALITÉS DU CHEF  *  SKILLS ({num(sum(n for _, n in skills))})")
    for name, n in skills[:8]:
        t.item(str(n), name.split(":")[-1], "offert", tone=0.85)
    if len(skills) > 8:
        t.item(str(sum(n for _, n in skills[8:])), f"Autres skills ({len(skills) - 8})", "offert", tone=0.72)
    if not skills:
        t.item("0", "Pas de spécialité ce soir", "-", tone=0.7)

    section(t, "LA BRIGADE  *  WORKERS")
    t.item("1", "Claude, chef de cuisine", "abo", tone=0.85)
    for label, key, tail in (("Codex, second", "codex", "abo"), ("Gemini, sous-chef", "gemini", "abo"),
                             ("Jev, commis", "jev", "API")):
        n = st[key]
        t.item(str(n), label, tail if n else "-", tone=1.0 if n else 0.7, bold=bool(n))
    problems = st.get("worker_problems") or []
    if problems:
        t.text("  INCIDENT  " + " / ".join(problems).upper(), 0.8, red=True)
    routes = st.get("routes") or {}
    if any(routes.values()):
        r = [f"{lab} {routes.get(k, 0)}" for k, lab in (("DIRECT", "DIR"), ("CODEX", "CDX"),
             ("GEMINI", "GEM"), ("REVIEW", "REV"), ("PARALLEL", "PAR"))]
        t.text("  ROUTES  " + " ".join(r), 0.8, tone=0.8)

    t.gap(6)
    t.rule("-")
    t.lead("SOUS-TOTAL (TARIF API)", f"${st['equiv']:.2f}")
    remise = st["equiv"] - st["real"]
    if remise > 0.004:
        t.lead("REMISE ABONNEMENT MAX", f"-${remise:.2f}", red=True)
    t.lead("SERVICE", "COMPRIS", tone=0.8, key=False)
    t.lead("DURÉE DU SERVICE", duration(st.get("secs")), tone=0.8, key=False)
    t.rule("=")
    t.gap(4)
    total, label, big = f"${st['real']:.2f}", "TOTAL", t.cols[2]
    t.text(label + " " * (big - len(label) - len(total)) + total, 2, bold=True, tag="total")
    t.rule("=")
    t.split("RÈGLEMENT", "API KEY" if st["metered"] else "ABONNEMENT MAX", tone=0.9)
    extra = (["API ANTHROPIC"] if st["metered"] else []) + ([f"JEV x{st['jev']}"] if st["jev"] else [])
    t.gap(4)
    t.text("SUPPLÉMENT : " + ", ".join(extra) if extra else "AUCUN SUPPLÉMENT FACTURÉ",
           bold=True, align="center", red=bool(extra))
    t.gap(14)
    t.text("MERCI DE VOTRE VISITE", bold=True, align="center")
    t.text("À BIENTÔT DANS LE TERMINAL", 0.8, tone=0.8, align="center")
    t.gap(14)
    t.barcode(sid)
    t.text(" ".join(sid), tone=0.95, align="center")
    return t


# --- 7. Assembly --------------------------------------------------------------------------

def build(st, session_id="", when=None):
    sid = (session_id or "00000000")[:8].upper()
    rng = np.random.default_rng(zlib.crc32(sid.encode()))
    when = when or datetime.now()

    ink, red, dens, keep, tags = layout(st, sid, when).print_ink(rng)
    h, w = ink.shape
    ink = thermal_ink(ink, dens, keep, rng)[..., None]
    red = thermal_ink(red, dens, keep, rng)[..., None] * 0.92
    rgb = paper_tone(h, w, rng)
    rgb = rgb * (1 - red) + RED_RGB * red
    rgb = rgb * (1 - ink) + INK_RGB * ink
    if "total" in tags:
        stamp = rubber_stamp(h, w, tags["total"], rng, bottom="CLAUDE MAX" if not st["metered"] else "API")[..., None]
        rgb = rgb * (1 - stamp) + STAMP_RGB * stamp
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
