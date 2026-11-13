"""Colour art renderer: bitmap -> styled raster (ASCII / halftone / mosaic).

Pure function of (source image, params, theme). Used by art_engine for
`image` arts that set `mode`, and by the art studio for live previews.

Design notes
- Every theme gets a *positive* image: on dark backgrounds ink follows
  brightness, on light backgrounds ink follows darkness (like print).
- Colours come from the source, graded, then fitted to the theme background
  so they stay readable (lifted on dark, deepened on light).
- Output is RGBA with a transparent background; the profile SVG supplies
  the page colour. Assets are encoded as WebP (alpha) to stay small.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import subject_mask

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"  # independent of the working directory
FONTS = {"regular": "DejaVuSansMono.ttf", "bold": "DejaVuSansMono-Bold.ttf"}
RENDER_VERSION = 2          # bump on any change to the output: it is part of the asset hash
GLYPH_ASPECT = 2.0          # cell height / width for monospace text cells
MODES = ("ascii", "halftone", "mosaic")
CUTOUTS = ("none", "subject", "key")
EDGE_SHAPES = ("oval", "torn", "brush", "dissolve", "subject")
FITS = ("auto", "cover", "contain")
POLARITIES = ("auto", "bright", "dark")
LIGHT_STYLES = ("auto", "print", "match", "card")
ENUMS = {"cutout": CUTOUTS, "edge_shape": EDGE_SHAPES, "fit": FITS, "polarity": POLARITIES, "light_style": LIGHT_STYLES,
         "light_mode": ("halftone", "same")}
KEY_DEFAULT_TOL = 0.18  # flood keying tolerance when the slider is at 0
THEME_BG = {"dark": (22, 27, 34), "light": (246, 248, 250)}  # profile card fills (#161b22 / #f6f8fa)
CARD_BG = (13, 17, 23)  # light-theme "card": GitHub dark canvas

DEFAULTS = {
    "mode": "ascii",
    "cols": 56,             # grid columns (detail)
    "chars": " .,:;i1tfLCG08@",  # sparse -> dense fill ramp (ascii mode); no - / = so fills don't read as scanlines
    "edges": 0.35,          # 0 = off; edge strength threshold factor for / | \\ -
    "bold": True,
    "saturation": 1.25,
    "contrast": 1.1,
    "brightness": 0.0,
    "gamma": 1.0,
    "glow": 0.6,            # 0..1 bloom strength (dark theme only)
    "bg_remove": 0.0,       # key tolerance: background = colour near the border's, *connected* to the border
    "cutout": "none",       # "subject" (Vision mask file, see subject_mask.py) | "key" (flood from the border) | "none"
    "backdrop": 0.0,        # 0..1: how much of the cut-away background is still drawn
    "body": 0.3,            # 0..1: minimum ink across the subject, so its dark parts still read as part of it
    "crop": None,           # [x, y, w, h] in 0..1 of the source
    "ink_floor": 0.1,       # ink below this is empty (cleans noise in dark areas)
    "vignette": 0.35,       # 0 = off; width of the edge treatment
    "edge_shape": "oval",   # oval | torn (ragged paper) | brush (dry streaks) | dissolve (scatter) | subject (follows the cut-out)
    "seed": 0,              # variant of the torn / brush / dissolve / subject edge
    "gap": 0.18,            # mosaic tile gap (fraction of cell)
    "invert": False,
    "levels": True,         # auto-levels: stretch the 1st..99th luminance percentile to full range
    "underlay": 0.2,        # ascii: tint each cell with its colour under the glyph (0 = off)
    "polarity": "auto",     # what becomes ink: "auto" (whatever stands out from the image's own background) | "bright" | "dark"
    "light_style": "auto",  # light theme: "auto" (positive print, flips like `polarity`) | "print" (darkness = ink) | "match" (dark theme's silhouette) | "card" (dark panel)
    "light_mode": "halftone",  # light theme draws a halftone (glyph strokes carry no tone on white) | "same" keeps `mode`
    "fit": "auto",          # "auto": your crop as drawn, else fill the zone | "cover": fill (centre crop) | "contain": whole image
}

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(str(FONT_DIR / FONTS[weight]), size)
    return _FONT_CACHE[key]


def merged(params: dict | None) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (params or {}).items() if v is not None and k in DEFAULTS})
    return out


# ---------------------------------------------------------------------------
# Source preparation
# ---------------------------------------------------------------------------

def _crop_box(size: tuple[int, int], crop) -> tuple[int, int, int, int] | None:
    if not crop:
        return None
    x, y, w, h = (float(v) for v in crop)
    W, H = size
    box = (int(max(0, x) * W), int(max(0, y) * H), int(min(1, x + w) * W), int(min(1, y + h) * H))
    return box if box[2] - box[0] > 4 and box[3] - box[1] > 4 else None


def load_source(path_or_bytes, crop=None, mask=None):
    """-> (rgb float32 HxWx3 0..1, alpha HxW, matte HxW or None).

    `mask` is a greyscale image path (white = subject) the size of the source;
    it is cropped and scaled exactly like the image."""
    src = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes)
    src.load()
    img = src.convert("RGBA")
    full = img.size
    box = _crop_box(full, crop)
    if box:
        img = img.crop(box)
    # Work at a bounded size: plenty for grids up to ~160 cols, fast to filter.
    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    matte = None
    if mask is not None:
        m = Image.open(mask).convert("L")
        if m.size != full:
            m = m.resize(full, Image.Resampling.BILINEAR)
        if box:
            m = m.crop(box)
        m = m.resize(img.size, Image.Resampling.BILINEAR)
        matte = np.asarray(m, dtype=np.float32) / 255.0
    return arr[..., :3].copy(), arr[..., 3].copy(), matte


def key_background(rgb: np.ndarray, tolerance: float) -> np.ndarray:
    """Subject matte (1 = keep) from a flat-ish background.

    Background = pixels close to the border's dominant colour AND connected to
    the border. The connectivity is what keeps a subject that shares the
    background's colour (a white shirt on a white wall) from being eaten."""
    h, w, _ = rgb.shape
    s = min(1.0, 256 / max(h, w))
    sw, sh = max(8, int(w * s)), max(8, int(h * s))
    small = np.stack([_resize(rgb[..., c], (sw, sh)) for c in range(3)], axis=-1)
    small = np.stack([_smooth(small[..., c]) for c in range(3)], axis=-1)  # beat jpeg noise / fine texture
    b = max(1, min(sh, sw) // 40)
    border = np.zeros((sh, sw), dtype=bool)
    border[:b] = border[-b:] = True
    border[:, :b] = border[:, -b:] = True
    key = np.median(small[border], axis=0)
    dist = np.sqrt(((small - key) ** 2).sum(axis=2)) / math.sqrt(3)
    tol = max(0.01, float(tolerance))
    cand = dist < tol
    bg = cand & border
    while True:  # grow the border seeds through similar pixels (4-connected)
        g = bg.copy()
        g[1:] |= bg[:-1]
        g[:-1] |= bg[1:]
        g[:, 1:] |= bg[:, :-1]
        g[:, :-1] |= bg[:, 1:]
        g &= cand
        if (g == bg).all():
            break
        bg = g
    # soft edge: background pixels near the tolerance fade instead of cutting hard
    soft = np.clip((dist - tol * 0.5) / (tol * 0.5 + 1e-6), 0.0, 1.0)
    keep = np.where(bg, soft, 1.0).astype(np.float32)
    keep = np.clip(_resize(keep, (w, h)), 0.0, 1.0)
    im = Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0))
    return np.asarray(im, dtype=np.float32) / 255.0


def grade(rgb: np.ndarray, p: dict, weight: np.ndarray | None = None) -> np.ndarray:
    """`weight` (HxW 0..1): pixels that count for auto-levels (the subject)."""
    out = rgb
    if p["levels"]:
        y = luminance(out)
        sel = y[weight > 0.5] if weight is not None and (weight > 0.5).sum() > 200 else y
        lo, hi = np.percentile(sel, 1), np.percentile(sel, 99)
        if hi - lo > 0.05:
            out = np.clip((out - lo) / (hi - lo), 0.0, 1.0)
    if p["invert"]:
        out = 1.0 - out
    if p["brightness"]:
        out = out + float(p["brightness"])
    if p["contrast"] != 1.0:
        out = (out - 0.5) * float(p["contrast"]) + 0.5
    out = np.clip(out, 0.0, 1.0)
    if p["gamma"] != 1.0:
        out = out ** float(p["gamma"])
    if p["saturation"] != 1.0:
        y = luminance(out)[..., None]
        out = np.clip(y + (out - y) * float(p["saturation"]), 0.0, 1.0)
    return out


def luminance(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def fit_to_theme(rgb: np.ndarray, theme: str, flipped: bool = False) -> np.ndarray:
    """Keep hue/chroma, move lightness into a band readable on the theme bg.

    `flipped`: the ink is the theme's unnatural side (dark source tones drawn on the
    dark page, or bright ones on the light page). Then lightness is mirrored, so
    black lines become chalk-white on dark and white highlights deep ink on light;
    saturated colours keep most of their own lightness (red stays red)."""
    y = luminance(rgb)[..., None]
    chroma = rgb.max(axis=-1, keepdims=True) - rgb.min(axis=-1, keepdims=True)
    if theme == "dark" and flipped:
        target = np.maximum(np.clip(y, 0.38, 1.0), 0.38 + 0.42 * (1 - y) * (1 - chroma))
    elif theme == "dark":
        target = np.clip(y, 0.38, 1.0)            # lift murky colours
    elif flipped:
        target = np.minimum(0.08 + 0.42 * y, 0.08 + 0.42 * (1 - y) + 0.3 * chroma)
    else:
        target = 0.08 + 0.42 * y                  # deepen so ink reads on white, keep tonal order
    scale = target / np.maximum(y, 1e-4)
    out = rgb * scale
    # a channel pushed past 1 would clip and shift hue: rescale, then make up
    # the missing lightness by mixing towards white
    peak = np.maximum(out.max(axis=-1, keepdims=True), 1.0)
    out = out / peak
    missing = np.clip(target - luminance(out)[..., None], 0.0, 1.0)
    out = out + (1.0 - out) * missing
    grey = np.repeat(target, 3, axis=-1)
    out = np.where(y < 1e-4, grey, out)
    if theme != "dark":
        # darkening shrinks chroma in absolute terms (colours go muddy): win it back
        yo = luminance(out)[..., None]
        out = yo + (out - yo) * 1.3
    return np.clip(out, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Grid sampling
# ---------------------------------------------------------------------------

def _resize(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Area-average resize of a float array (HxW or HxWxC) to (cols, rows)."""
    if arr.ndim == 2:
        im = Image.fromarray(arr.astype(np.float32), mode="F")
        return np.asarray(im.resize(size, Image.Resampling.BOX), dtype=np.float32)
    return np.stack([_resize(arr[..., c], size) for c in range(arr.shape[2])], axis=-1)


def sample_grid(rgb, alpha, cols: int, rows: int):
    """Premultiplied colour + coverage per cell."""
    pre = rgb * alpha[..., None]
    a = _resize(alpha, (cols, rows))
    c = _resize(pre, (cols, rows)) / np.maximum(a[..., None], 1e-4)
    return np.clip(c, 0, 1), np.clip(a, 0, 1)


def _smooth(a: np.ndarray) -> np.ndarray:
    """Separable [1,2,1]/4 blur (PIL cannot blur float images)."""
    p = np.pad(a, 1, mode="edge")
    a = (p[:, :-2] + 2 * p[:, 1:-1] + p[:, 2:]) / 4
    return (a[:-2] + 2 * a[1:-1] + a[2:]) / 4


def edge_field(field: np.ndarray, cols: int, rows: int):
    """Per-cell edge magnitude (0..1) and orientation glyph index 0..3 of a
    pixel field (the ink that will be drawn, so cut-out silhouettes get outlined)."""
    sub = 4
    L = _resize(field, (cols * sub, rows * sub))
    L = _smooth(L)
    gx = np.zeros_like(L)
    gy = np.zeros_like(L)
    gx[:, 1:-1] = (L[:, 2:] - L[:, :-2]) * 0.5
    gy[1:-1, :] = (L[2:, :] - L[:-2, :]) * 0.5
    # account for tall cells: a cell step is GLYPH_ASPECT times taller in y
    mag = np.hypot(gx, gy)
    ang = np.arctan2(gy * GLYPH_ASPECT, gx)
    # pool per cell: take the strongest sub-sample's angle
    mag_c = mag.reshape(rows, sub, cols, sub).transpose(0, 2, 1, 3).reshape(rows, cols, sub * sub)
    ang_c = ang.reshape(rows, sub, cols, sub).transpose(0, 2, 1, 3).reshape(rows, cols, sub * sub)
    idx = mag_c.argmax(axis=2)
    m = np.take_along_axis(mag_c, idx[..., None], axis=2)[..., 0]
    a = np.take_along_axis(ang_c, idx[..., None], axis=2)[..., 0]
    m = m / max(float(np.percentile(m, 99)), 1e-4)
    # gradient angle -> edge direction (perpendicular), quantised to 4 glyphs
    edge_dir = (a + math.pi / 2) % math.pi
    q = np.round(edge_dir / (math.pi / 4)).astype(int) % 4   # 0:-, 1:\ , 2:|, 3:/
    return np.clip(m, 0, 1), q


EDGE_GLYPHS = ("-", "\\", "|", "/")


def _smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _noise(rows: int, cols: int, seed: int, stretch: float = 1.0, octaves=(3, 7, 15)) -> np.ndarray:
    """Deterministic smooth value noise in about -1..1. `stretch` > 1 makes
    horizontal streaks (brush)."""
    rng = np.random.default_rng(1009 + int(seed))
    out = np.zeros((rows, cols), dtype=np.float32)
    amp, total = 1.0, 0.0
    for n in octaves:
        gh = max(2, n)
        gw = max(2, int(round(n * cols / max(rows, 1) / stretch)))
        g = rng.random((gh, gw), dtype=np.float32) * 2 - 1
        im = Image.fromarray(g, mode="F").resize((cols, rows), Image.Resampling.BICUBIC)
        out += amp * np.asarray(im, dtype=np.float32)
        total += amp
        amp *= 0.55
    return out / total


def _frame_distance(rows: int, cols: int, power: float) -> np.ndarray:
    """0 in the middle -> 1 at the frame edge (superellipse; higher power = squarer)."""
    y = (np.arange(rows, dtype=np.float32) + 0.5) / rows * 2 - 1
    x = (np.arange(cols, dtype=np.float32) + 0.5) / cols * 2 - 1
    return (np.abs(x)[None, :] ** power + np.abs(y)[:, None] ** power) ** (1 / power)


def _grow_distance(inside: np.ndarray, steps: int, y_every: int) -> np.ndarray:
    """Chamfer-ish distance (in cell widths) from `inside` cells, capped at `steps`.
    Rows step every `y_every` iterations: text cells are taller than wide."""
    d = np.where(inside, 0.0, float(steps)).astype(np.float32)
    reach = inside.copy()
    for i in range(1, steps):
        g = reach.copy()
        g[:, 1:] |= reach[:, :-1]
        g[:, :-1] |= reach[:, 1:]
        if i % y_every == 0:
            g[1:] |= reach[:-1]
            g[:-1] |= reach[1:]
        d[g & ~reach] = i
        reach = g
    return d


def edge_mask(shape: str, rows: int, cols: int, strength: float, seed: int = 0,
              subject: np.ndarray | None = None, cell_aspect: float = 1.0) -> np.ndarray:
    """Per-cell 0..1 multiplier that dissolves the art into the page.

    oval      soft superellipse vignette
    torn      ragged, mostly hard edge like torn paper
    brush     horizontal dry-brush streaks breaking up the sides
    dissolve  cells scatter out towards the edge
    subject   only the cut-out stays solid; the backdrop survives in a ragged halo
              around it (the mask is meant for the backdrop: see render())
    """
    s = float(strength)
    if s <= 0:
        return np.ones((rows, cols), dtype=np.float32)
    s = min(0.95, s)
    if shape == "subject" and subject is not None:
        width = max(2, int(round(2 + s * 14)))       # halo width in cell widths
        y_every = max(1, int(round(cell_aspect)))
        d = _grow_distance(subject > 0.35, width + 1, y_every) / width
        n = _noise(rows, cols, seed, octaves=(4, 9, 19))
        return (1.0 - _smoothstep((d + 0.35 * n - 0.25) / 0.6)).astype(np.float32)
    if shape == "torn":
        d = _frame_distance(rows, cols, 5.0)
        n = _noise(rows, cols, seed, octaves=(5, 11, 23, 41))
        edge = 1.0 - s * 0.45                        # where the paper tears
        dn = d + n * (0.08 + 0.14 * s)
        return (1.0 - _smoothstep((dn - edge) / 0.03 + 0.5)).astype(np.float32)
    if shape == "brush":
        d = _frame_distance(rows, cols, 6.0)
        n = _noise(rows, cols, seed, stretch=6.0, octaves=(6, 13, 29))
        start = 1.0 - s
        t = (d - start) / max(1e-3, 1.0 - start)
        return (1.0 - _smoothstep(t * 1.6 + n * 0.9 - 0.2)).astype(np.float32)
    if shape == "dissolve":
        d = _frame_distance(rows, cols, 4.0)
        start = 1.0 - s
        t = _smoothstep((d - start) / max(1e-3, 1.0 - start) + 0.15)
        rng = np.random.default_rng(2027 + int(seed))
        keep = rng.random((rows, cols)) >= t
        return np.where(keep, 1.0 - 0.5 * t, 0.0).astype(np.float32)
    # oval (also the fallback for "subject" without a cut-out)
    d = _frame_distance(rows, cols, 4.0)
    start = 1.0 - s
    return (1.0 - _smoothstep((d - start) / (1.0 - start))).astype(np.float32)


def vignette_mask(rows: int, cols: int, strength: float) -> np.ndarray:
    return edge_mask("oval", rows, cols, strength)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def resolve_polarity(polarity: str, lum, alpha, subj, back, cut: bool, theme: str) -> str:
    """What becomes ink. "auto" keeps the theme's natural reading (light ink on the
    dark page, dark ink on the light one: photos stay positive) and flips only when
    the image's background would otherwise become the ink:
    - with a cut-out, when the subject contrasts with what was removed the other way
      (a black gun lifted off a pale floor, on the dark theme);
    - without one, when the border is flat (a drawing on white paper, a sign on
      black) and on the ink's side of the content."""
    if polarity in ("bright", "dark"):
        return polarity
    natural = "bright" if theme == "dark" else "dark"
    if cut and back.sum() > 0.05 * alpha.sum() and subj.sum() > 0:
        bg = float((lum * back).sum() / back.sum())
        fg = float((lum * subj).sum() / subj.sum())
    else:
        h, w = lum.shape
        b = max(2, min(h, w) // 40)
        border = np.concatenate([lum[:b].ravel(), lum[-b:].ravel(), lum[:, :b].ravel(), lum[:, -b:].ravel()])
        bg = float(np.median(border))
        if float(np.percentile(np.abs(border - bg), 90)) > 0.08:
            return natural                   # busy border: a photo, keep it positive
        content = np.abs(lum - bg) > 0.15
        if content.sum() < 0.01 * lum.size:
            return natural
        fg = float(lum[content].mean())
    if theme == "dark" and bg - fg >= 0.2:
        return "dark"
    if theme == "light" and fg - bg >= 0.2:
        return "bright"
    return natural


def cover_crop(aspect: float, *arrays):
    """Centre-crop arrays (same HxW; None passes through) to the zone's aspect (w/h)."""
    h, w = arrays[0].shape[:2]
    if w / h > aspect:            # too wide: trim the sides
        nw = max(1, int(round(h * aspect)))
        x0 = (w - nw) // 2
        return tuple(None if a is None else a[:, x0:x0 + nw] for a in arrays)
    nh = max(1, int(round(w / aspect)))  # too tall: trim top/bottom
    y0 = (h - nh) // 2
    return tuple(None if a is None else a[y0:y0 + nh] for a in arrays)


def canvas_size(src_w: int, src_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    s = min(box_w / src_w, box_h / src_h)
    return max(8, int(round(src_w * s))), max(8, int(round(src_h * s)))


def render(source, params: dict | None, theme: str, box: tuple[int, int] = (740, 1000)) -> Image.Image:
    """Render to an RGBA image that fits `box` (px), transparent background."""
    p = merged(params)
    if theme == "light" and p["light_style"] == "card":
        return dark_card(render(source, params, "dark", box))
    cutout = p["cutout"] if p["cutout"] in CUTOUTS else "none"
    if cutout == "none" and float(p["bg_remove"]) > 0:
        cutout = "key"                       # legacy: the slider alone means "key the background"
    mask = subject_mask.find_mask(source) if cutout == "subject" else None
    rgb, alpha, matte = load_source(source, p["crop"], mask)
    fit = p["fit"] if p["fit"] in FITS else "auto"
    if fit == "cover" or (fit == "auto" and not p["crop"]):
        rgb, alpha, matte = cover_crop(box[0] / box[1], rgb, alpha, matte)
    if cutout == "subject" and matte is None:
        cutout = "none"                      # no mask (none found / not generated): draw the whole image
    if cutout == "key":
        tol = float(p["bg_remove"]) or KEY_DEFAULT_TOL
        matte = key_background(rgb, tol)
    if cutout == "none" or matte is None:
        matte = np.ones_like(alpha)
    subj = alpha * matte                     # the subject
    back = alpha * (1.0 - matte)             # what the cut-out removed
    rgb = grade(rgb, p, subj if cutout != "none" else None)
    lum = luminance(rgb)
    # Ink = what stands out from the image's *own* background, the same on both
    # themes (a black gun on a pale floor is drawn as the gun, neon on black as
    # the neon); the theme only changes the colours. "print" forces darkness = ink
    # on the light theme.
    pol = resolve_polarity(p["polarity"], lum, alpha, subj, back, cutout != "none", theme)
    if theme == "light" and p["light_style"] == "print":
        pol = "dark"
    elif theme == "light" and p["light_style"] == "match":   # the dark theme's silhouette
        pol = resolve_polarity(p["polarity"], lum, alpha, subj, back, cutout != "none", "dark")
    tone = 1.0 - lum if pol == "dark" else lum
    if cutout != "none":
        # the whole silhouette carries at least `body` ink: a black gun on the dark
        # theme still draws as a (sparse) gun instead of a hole
        body = float(p["body"])
        subj_ink = subj * (body + (1.0 - body) * tone)
    else:
        subj_ink = subj * tone
    shape = p["edge_shape"] if p["edge_shape"] in EDGE_SHAPES else "oval"
    backdrop = float(p["backdrop"])
    if shape == "subject" and cutout != "none":
        backdrop = max(backdrop, 0.5)        # the halo *is* the backdrop: make it visible by default
    back_ink = back * tone * backdrop

    W, H = canvas_size(rgb.shape[1], rgb.shape[0], *box)
    mode = p["mode"] if p["mode"] in MODES else "ascii"
    cols = int(max(8, min(220, p["cols"])))
    if theme == "light" and p["light_mode"] == "halftone" and mode != "halftone":
        # text cells are 2x taller than wide, dots are square: at the same column
        # count a halftone would have twice the cells (heavy asset, finer than the
        # dark version). ~0.7x columns keeps the cell count and the look's density.
        if mode == "ascii":
            cols = max(8, int(round(cols * 0.7)))
        mode = "halftone"
    cell_w = W / cols
    aspect = GLYPH_ASPECT if mode == "ascii" else 1.0
    cell_h = cell_w * aspect
    rows = max(1, int(round(H / cell_h)))
    H = int(round(rows * cell_h))

    # colour of what is actually drawn (the subject, plus the backdrop if kept)
    color, _ = sample_grid(rgb, alpha * (matte + (1.0 - matte) * max(backdrop, 0.05)), cols, rows)
    ink_s = np.clip(_resize(subj_ink, (cols, rows)), 0, 1)
    ink_b = np.clip(_resize(back_ink, (cols, rows)), 0, 1)
    strength, seed = float(p["vignette"]), int(p["seed"])
    if shape == "subject" and cutout != "none":
        halo = edge_mask("subject", rows, cols, strength or 0.35, seed,
                         subject=_resize(subj, (cols, rows)), cell_aspect=aspect)
        frame = edge_mask("oval", rows, cols, min(strength, 0.25))   # keep the block edge soft
        ink = (ink_s + ink_b * halo) * frame
    else:
        ink = (ink_s + ink_b) * edge_mask("oval" if shape == "subject" else shape, rows, cols, strength, seed)
    floor = float(p["ink_floor"])
    ink = np.where(ink < floor, 0.0, (ink - floor) / (1 - floor))
    natural = "bright" if theme == "dark" else "dark"
    color = fit_to_theme(color, theme, flipped=(pol != natural and not (theme == "light" and p["light_style"] in ("print", "match"))))
    edge_src = subj_ink + back_ink

    ss = 2  # supersample for clean anti-aliasing
    layer = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cw, ch = cell_w * ss, cell_h * ss

    if mode == "ascii":
        chars = p["chars"] if len(p["chars"]) >= 2 else DEFAULTS["chars"]
        font = _font("bold" if p["bold"] else "regular", max(4, int(round(ch * 0.86))))
        n = len(chars)
        under = float(p["underlay"])
        if under > 0:
            for r in range(rows):
                for c in range(cols):
                    k = ink[r, c]
                    if k <= 0:
                        continue
                    col = tuple(int(v * 255) for v in color[r, c])
                    draw.rectangle((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch), fill=(*col, int(255 * under * k)))
        edges, orient = (edge_field(edge_src, cols, rows) if p["edges"] else (None, None))
        thr = 1.0 - float(p["edges"]) if p["edges"] else 2.0
        for r in range(rows):
            y = r * ch
            for c in range(cols):
                k = ink[r, c]
                if k <= 0:
                    continue
                if edges is not None and edges[r, c] > thr and k > 0.08:
                    g = EDGE_GLYPHS[orient[r, c]]
                else:
                    g = chars[min(n - 1, max(1, int(k * (n - 1) + 0.5)))]
                if g == " ":
                    continue
                col = tuple(int(v * 255) for v in color[r, c])
                a = int(255 * min(1.0, 0.55 + 0.45 * k))
                draw.text((c * cw + cw / 2, y + ch / 2), g, font=font, fill=(*col, a), anchor="mm")
    elif mode == "halftone":
        for r in range(rows):
            for c in range(cols):
                k = ink[r, c]
                if k <= 0:
                    continue
                rad = math.sqrt(k) * cw * 0.62
                cx, cy = c * cw + cw / 2, r * ch + ch / 2
                col = tuple(int(v * 255) for v in color[r, c])
                draw.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(*col, 255))
    else:  # mosaic
        g = float(p["gap"]) * cw / 2
        rad = max(0.0, cw * 0.18)
        for r in range(rows):
            for c in range(cols):
                k = ink[r, c]
                if k <= 0:
                    continue
                col = tuple(int(v * 255) for v in color[r, c])
                # on light pages translucent tiles read as washed-out pastel: keep them solid
                a = 255 if theme == "light" else int(255 * min(1.0, 0.25 + 0.75 * k))
                draw.rounded_rectangle((c * cw + g, r * ch + g, (c + 1) * cw - g, (r + 1) * ch - g), radius=rad, fill=(*col, a))

    layer = layer.resize((W, H), Image.Resampling.LANCZOS)
    glow = float(p["glow"])
    if glow > 0 and theme == "dark":
        layer = add_glow(layer, glow, radius=max(2.0, cell_w * 0.9))
    return layer


def add_glow(layer: Image.Image, strength: float, radius: float) -> Image.Image:
    """Soft bloom under the art: blurred copy, additive-ish, kept transparent."""
    blur = layer.filter(ImageFilter.GaussianBlur(radius))
    b = np.asarray(blur, dtype=np.float32)
    b[..., 3] *= min(1.0, 0.9 * strength)
    base = Image.fromarray(np.clip(b, 0, 255).astype(np.uint8), "RGBA")
    base.alpha_composite(layer)
    return base


def dark_card(art: Image.Image, pad: int = 18) -> Image.Image:
    """Dark rounded panel (GitHub dark palette) holding the dark-theme render."""
    w, h = art.size
    card = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, w + 2 * pad - 1, h + 2 * pad - 1), radius=pad, fill=(*CARD_BG, 255), outline=(48, 54, 61, 255), width=2)
    card.alpha_composite(art, (pad, pad))
    return card


def encode(img: Image.Image, theme: str, quality: int = 82) -> tuple[bytes, str]:
    """-> (bytes, mime). Flattened onto the profile card colour (the art sits on
    exactly that fill, so the edge is invisible) and saved as opaque WebP: a
    soft alpha channel (glow, anti-aliasing) costs ~2-3x the bytes."""
    buf = io.BytesIO()
    preview_on_bg(img, theme).save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue(), "image/webp"


def preview_on_bg(img: Image.Image, theme: str) -> Image.Image:
    bg = Image.new("RGBA", img.size, (*THEME_BG[theme], 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")
