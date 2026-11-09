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

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"  # independent of the working directory
FONTS = {"regular": "DejaVuSansMono.ttf", "bold": "DejaVuSansMono-Bold.ttf"}
RENDER_VERSION = 1          # bump on any change to the output: it is part of the asset hash
GLYPH_ASPECT = 2.0          # cell height / width for monospace text cells
MODES = ("ascii", "halftone", "mosaic")
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
    "bg_remove": 0.0,       # 0 = off; tolerance for keying out the border colour
    "crop": None,           # [x, y, w, h] in 0..1 of the source
    "ink_floor": 0.1,       # ink below this is empty (cleans noise in dark areas)
    "vignette": 0.35,       # 0 = off; fade ink towards the frame edge
    "gap": 0.18,            # mosaic tile gap (fraction of cell)
    "invert": False,
    "levels": True,         # auto-levels: stretch the 1st..99th luminance percentile to full range
    "underlay": 0.2,        # ascii: tint each cell with its colour under the glyph (0 = off)
    "light_style": "print", # light theme: "print" (darkness = ink, reads like print) | "match" (dark theme's silhouette) | "card" (dark panel)
    "light_mode": "halftone",  # light theme draws a halftone (glyph strokes carry no tone on white) | "same" keeps `mode`
    "fit": "cover",         # "cover" fills the art zone (centre crop) | "contain" keeps the whole image
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

def load_source(path_or_bytes, crop=None) -> tuple[np.ndarray, np.ndarray]:
    """-> (rgb float32 HxWx3 in 0..1, alpha float32 HxW)."""
    src = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes)
    src.load()
    img = src.convert("RGBA")
    if crop:
        x, y, w, h = (float(v) for v in crop)
        W, H = img.size
        box = (int(max(0, x) * W), int(max(0, y) * H), int(min(1, x + w) * W), int(min(1, y + h) * H))
        if box[2] - box[0] > 4 and box[3] - box[1] > 4:
            img = img.crop(box)
    # Work at a bounded size: plenty for grids up to ~160 cols, fast to filter.
    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return arr[..., :3].copy(), arr[..., 3].copy()


def key_background(rgb: np.ndarray, alpha: np.ndarray, tolerance: float) -> np.ndarray:
    """Fade out pixels close to the dominant border colour (flat backgrounds)."""
    if tolerance <= 0:
        return alpha
    h, w, _ = rgb.shape
    b = max(2, min(h, w) // 40)
    border = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
                             rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)])
    key = np.median(border, axis=0)
    dist = np.sqrt(((rgb - key) ** 2).sum(axis=2)) / math.sqrt(3)
    lo, hi = tolerance * 0.6, tolerance * 1.4 + 1e-6
    keep = np.clip((dist - lo) / (hi - lo), 0.0, 1.0)
    # soften the matte edge a little
    keep_img = Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    return alpha * (np.asarray(keep_img, dtype=np.float32) / 255.0)


def grade(rgb: np.ndarray, p: dict) -> np.ndarray:
    out = rgb
    if p["levels"]:
        y = luminance(out)
        lo, hi = np.percentile(y, 1), np.percentile(y, 99)
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


def fit_to_theme(rgb: np.ndarray, theme: str) -> np.ndarray:
    """Keep hue/chroma, move lightness into a band readable on the theme bg."""
    y = luminance(rgb)[..., None]
    if theme == "dark":
        target = np.clip(y, 0.38, 1.0)            # lift murky colours
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


def edge_field(lum: np.ndarray, alpha: np.ndarray, cols: int, rows: int):
    """Per-cell edge magnitude (0..1) and orientation glyph index 0..3."""
    sub = 4
    L = _resize(lum * alpha, (cols * sub, rows * sub))
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


def vignette_mask(rows: int, cols: int, strength: float) -> np.ndarray:
    """1 in the middle, smoothly -> 0 at the frame edge, so the art dissolves
    into the page instead of ending in a hard rectangle. strength 0 = off."""
    if strength <= 0:
        return np.ones((rows, cols), dtype=np.float32)
    y = (np.arange(rows, dtype=np.float32) + 0.5) / rows * 2 - 1
    x = (np.arange(cols, dtype=np.float32) + 0.5) / cols * 2 - 1
    # superellipse distance: keeps corners soft but the sides mostly intact
    d = (np.abs(x)[None, :] ** 4 + np.abs(y)[:, None] ** 4) ** 0.25
    start = 1.0 - min(0.95, strength)
    t = np.clip((d - start) / (1.0 - start), 0.0, 1.0)
    return (1.0 - t * t * (3 - 2 * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def cover_crop(rgb: np.ndarray, alpha: np.ndarray, aspect: float):
    """Centre-crop to the zone's aspect (w/h) so the art fills the whole block."""
    h, w = alpha.shape
    if w / h > aspect:            # too wide: trim the sides
        nw = max(1, int(round(h * aspect)))
        x0 = (w - nw) // 2
        return rgb[:, x0:x0 + nw], alpha[:, x0:x0 + nw]
    nh = max(1, int(round(w / aspect)))  # too tall: trim top/bottom
    y0 = (h - nh) // 2
    return rgb[y0:y0 + nh], alpha[y0:y0 + nh]


def canvas_size(src_w: int, src_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    s = min(box_w / src_w, box_h / src_h)
    return max(8, int(round(src_w * s))), max(8, int(round(src_h * s)))


def render(source, params: dict | None, theme: str, box: tuple[int, int] = (740, 1000)) -> Image.Image:
    """Render to an RGBA image that fits `box` (px), transparent background."""
    p = merged(params)
    if theme == "light" and p["light_style"] == "card":
        return dark_card(render(source, params, "dark", box))
    rgb, alpha = load_source(source, p["crop"])
    if p["fit"] == "cover":
        rgb, alpha = cover_crop(rgb, alpha, box[0] / box[1])
    alpha = key_background(rgb, alpha, float(p["bg_remove"]))
    rgb = grade(rgb, p)
    lum = luminance(rgb)
    # "match": light theme keeps the dark theme's silhouette (bright = ink), only
    # colours are deepened for the light page. "print": darkness = ink.
    ink_src = 1.0 - lum if (theme == "light" and p["light_style"] == "print") else lum

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
    cell_h = cell_w * (GLYPH_ASPECT if mode == "ascii" else 1.0)
    rows = max(1, int(round(H / cell_h)))
    H = int(round(rows * cell_h))

    color, cover = sample_grid(rgb, alpha, cols, rows)
    ink = _resize(ink_src * alpha, (cols, rows)) / np.maximum(cover, 1e-4)
    ink = np.clip(ink, 0, 1) * cover
    ink = ink * vignette_mask(rows, cols, float(p["vignette"]))
    floor = float(p["ink_floor"])
    ink = np.where(ink < floor, 0.0, (ink - floor) / (1 - floor))
    color = fit_to_theme(color, theme)

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
        edges, orient = (edge_field(lum, alpha, cols, rows) if p["edges"] else (None, None))
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
