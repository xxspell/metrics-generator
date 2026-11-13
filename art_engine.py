"""Manifest-driven ASCII art engine.

Renders art sources (text files or images) to standalone SVG assets with the
ASCII raster embedded as base64 PNG, so viewer fonts never affect the result.
Assets are pre-generated at build time and embedded into the main metrics SVG
as a nested <svg id="ascii"> scaled with preserveAspectRatio into the art zone.

Asset freshness: filename carries a content hash of
(source bytes + render params + ENGINE_VERSION); a fresh asset exists <=> no
work needed. Stale assets are garbage-collected on build.

Manifest (arts/manifest.json) is the single source of truth for art lists,
zones, ramps and per-art render params. See README.md for the schema.
"""

import argparse
import base64
import hashlib
import io
import json
import random
import re
import sys
from pathlib import Path

import numpy as np
from lxml import etree
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

ENGINE_VERSION = 1
MANIFEST_PATH = Path("arts/manifest.json")
GEN_DIR = Path("arts/gen")
SRC_DIR = Path("arts/src")
FONT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "DejaVuSansMono.ttf"  # independent of the working directory

THEMES = ("dark", "light")
THEME_INK = {"dark": "#c9d1d9", "light": "#24292f"}
DEFAULT_ZONE = {"x": 15, "y": 15, "width": 370, "height": 500}

DEFAULT_CELL = 16          # glyph font size in px (raster intrinsic scale)
SUPERSAMPLE = 2            # render glyphs at Nx, downscale for AA
MAX_COLS = 128             # image->ascii horizontal cap
DEFAULT_DETAIL = 64        # image->ascii columns
MAX_ASSET_BYTES = 220_000  # keep README payload sane
ASSET_KEY_HEX = 10
ASSET_RE = re.compile(r"^(?P<id>[A-Za-z0-9_-]+)\.(?P<theme>dark|light)\.(?P<key>[0-9a-f]{10})\.svg$")

BUILTIN_RAMPS = {
    "blocks": " ░▒▓█",
    "blocks3": " ▒▓█",
    "classic": " .:-=+*#%@",
    "fine": " .·∘°*◦◌●◍◎◯",
    "shades": " ·▂▄▆█",
    "lines": " .:-=┆┊╎╏|║",
    "binary": " 01",
    "ascii": " .'|()\u005c{}?[]<>/!*+qTJY1VZX25E7F$#0@8&W",
}

IMAGE_PARAM_KEYS = ("cell", "ramp", "detail", "contrast", "brightness", "gamma", "invert", "shading", "equalize", "color", "scale")
ENGINE_PARAM_KEYS = ("cell", "scale")
OUTPUT_SCALE = 2  # raster targets 2x zone pixels for crisp retina display
RASTER_TYPES = ("engine", "image", "render")  # arts backed by pre-built assets in arts/gen
ART_TYPES = ("text",) + RASTER_TYPES


class ManifestError(Exception):
    pass


def _canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path}: invalid JSON: {exc}") from exc
    validate_manifest(manifest, base_dir=path.parent)
    return manifest


def save_manifest(manifest: dict, path: Path = MANIFEST_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_manifest(manifest: dict, base_dir: Path = Path(".")) -> None:
    """Cheap structural validation with actionable errors; run before every use."""
    if not manifest:
        return
    arts = manifest.get("arts")
    if not isinstance(arts, list):
        raise ManifestError("manifest: 'arts' must be a list")
    share = (manifest.get("rotation") or {}).get("render_share")
    if share is not None and (isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 1):
        raise ManifestError("manifest: rotation.render_share must be a number from 0 to 1")
    seen_ids = set()
    for i, art in enumerate(arts):
        if not isinstance(art, dict):
            raise ManifestError(f"manifest: arts[{i}] must be an object")
        art_id = art.get("id")
        if not art_id or not re.fullmatch(r"[A-Za-z0-9_-]+", str(art_id)):
            raise ManifestError(f"manifest: arts[{i}].id missing or not [A-Za-z0-9_-]+")
        if art_id in seen_ids:
            raise ManifestError(f"manifest: duplicate art id '{art_id}'")
        seen_ids.add(art_id)
        if art.get("type") not in ART_TYPES:
            raise ManifestError(f"manifest: art '{art_id}' has unknown type {art.get('type')!r}")
        source = art.get("source")
        source_missing = not source or not (base_dir / source).is_file() and not Path(source).is_file()
        key = art.get("key")
        if key is not None and not re.fullmatch(rf"[0-9a-f]{{{ASSET_KEY_HEX}}}", str(key)):
            raise ManifestError(f"manifest: art '{art_id}' key must be {ASSET_KEY_HEX} hex chars")
        if source_missing and not (art.get("type") in RASTER_TYPES and key):
            raise ManifestError(f"manifest: art '{art_id}' source not found: {source}")
        if art["type"] == "render":
            _validate_render_params(art_id, art.get("render"))
        ramp = art.get("ramp")
        if ramp is not None:
            try:
                resolve_ramp(resolve_ramps(manifest), ramp)
            except ManifestError as exc:
                raise ManifestError(f"manifest: art '{art_id}' {exc}") from exc


def resolve_ramps(manifest: dict) -> dict:
    ramps = dict(BUILTIN_RAMPS)
    ramps.update((manifest.get("ramps") or {}))
    return ramps


def get_zone(manifest: dict) -> dict:
    zone = dict(DEFAULT_ZONE)
    zone.update(manifest.get("zone") or {})
    return zone


def enabled_arts(manifest: dict, types: tuple | None = None) -> list:
    return [
        art for art in (manifest.get("arts") or [])
        if art.get("enabled", True) and (types is None or art["type"] in types)
    ]


def render_share(manifest: dict) -> float | None:
    """manifest.rotation.render_share: fraction of runs that show a colour (render) art."""
    share = (manifest.get("rotation") or {}).get("render_share")
    return None if share is None else float(share)


def pick_art(manifest: dict, avoid_id: str | None = None, rng=random) -> dict | None:
    """Weighted pick among enabled arts, never repeating the previous art id.

    With rotation.render_share set, the colour (render) arts and the rest form
    two pools: a run draws from the colour pool with that probability (falling
    back to the other pool when one is empty after excluding the previous art).
    """
    arts = enabled_arts(manifest)
    if not arts:
        return None
    if avoid_id:
        rest = [a for a in arts if a["id"] != avoid_id]
        if rest:
            arts = rest
    share = render_share(manifest)
    if share is not None:
        colour = [a for a in arts if a["type"] == "render"]
        other = [a for a in arts if a["type"] != "render"]
        if colour and other:
            arts = colour if rng.random() < share else other
    weights = [max(0, int(a.get("weight", 1))) for a in arts]
    if sum(weights) == 0:
        weights = [1] * len(arts)
    return rng.choices(arts, weights=weights, k=1)[0]


def find_art(manifest: dict, art_id: str) -> dict | None:
    return next((a for a in manifest.get("arts") or [] if a["id"] == art_id), None)


# ---------------------------------------------------------------------------
# Grid model: list of rows, each row a list of (char, alpha) with alpha 0 = background
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size_px: int) -> ImageFont.FreeTypeFont:
    if size_px not in _FONT_CACHE:
        _FONT_CACHE[size_px] = ImageFont.truetype(str(FONT_PATH), size_px)
    return _FONT_CACHE[size_px]


def cell_metrics(size_px: int) -> tuple[float, int]:
    """(advance, line_height) in px for the bundled mono font."""
    font = _font(size_px)
    ascent, descent = font.getmetrics()
    return font.getlength("M"), ascent + descent


_NOTDEF_PROBE = "\U0010fffd"  # private-use codepoint: always rendered as the font's .notdef (tofu)
_GLYPH_CACHE: dict[str, bool] = {}


def _glyph_signature(font: ImageFont.FreeTypeFont, ch: str) -> tuple:
    mask = font.getmask(ch)
    return mask.size, bytes(mask)


def missing_glyphs(chars: str) -> set[str]:
    """Chars the bundled font cannot draw (rendered as .notdef tofu or nothing)."""
    font = _font(DEFAULT_CELL)
    notdef = None
    missing = set()
    for ch in set(chars):
        if ch.isspace():
            continue
        if ch not in _GLYPH_CACHE:
            if notdef is None:
                notdef = _glyph_signature(font, _NOTDEF_PROBE)
            sig = _glyph_signature(font, ch)
            _GLYPH_CACHE[ch] = sig == notdef or font.getmask(ch).getbbox() is None
        if _GLYPH_CACHE[ch]:
            missing.add(ch)
    return missing


def text_to_cells(text: str) -> list[list[tuple[str, int]]]:
    rows = []
    missing = set()
    for line in text.split("\n"):
        line = line.rstrip()
        cells = [(ch, 255) for ch in line]
        missing |= missing_glyphs(line)
        rows.append(cells)
    if missing:
        logger.warning("Font {font} lacks glyphs: {chars}", font=FONT_PATH.name, chars="".join(sorted(missing)))
    return rows


def resolve_ramp(ramps: dict, name_or_chars: str | None) -> str:
    """Ramp by name (built-in or manifest) or raw chars (2-40, sparse->dense)."""
    if not name_or_chars:
        return BUILTIN_RAMPS["blocks"]
    if name_or_chars in ramps:
        chars = ramps[name_or_chars]
    elif 1 < len(name_or_chars) <= 40 and "\n" not in name_or_chars and name_or_chars.isprintable():
        chars = name_or_chars
    else:
        raise ManifestError(f"unknown ramp and not usable raw chars: {name_or_chars!r}")
    missing = missing_glyphs(chars)
    if missing:
        raise ManifestError(
            f"ramp {name_or_chars!r} uses glyphs missing from {FONT_PATH.name} "
            f"(would render as boxes): {''.join(sorted(missing))!r}")
    return chars


def image_to_cells(
    path: Path,
    ramp: str,
    detail: int = DEFAULT_DETAIL,
    contrast: float = 1.0,
    gamma: float = 1.0,
    brightness: float = 0.0,
    invert: bool = False,
    shading: str = "opacity",
    equalize: bool = False,
    color: bool = False,
) -> list[list[tuple]]:
    """Bitmap -> (glyph, alpha[, rgb]) grid. Bright pixels get denser glyphs.

    equalize=True spreads a narrow/centered histogram across the full range —
    the fix for photos that render as uniform gray "soup".
    color=True tints every glyph with the source pixel color (chroma kept).
    """
    detail = max(8, min(MAX_COLS, int(detail)))
    img = Image.open(path)
    img.load()
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(flat, rgba)
    img_rgb = img.convert("RGB")
    img = img.convert("L")
    from PIL import ImageOps
    if invert:
        img = ImageOps.invert(img)
        img_rgb = ImageOps.invert(img_rgb)
    if equalize:
        img = ImageOps.equalize(img)
    else:
        img = ImageOps.autocontrast(img, cutoff=1)

    cols = detail
    w, h = img.size
    adv, line = cell_metrics(DEFAULT_CELL)
    rows = max(1, round(cols * (h / w) * (adv / line)))
    grid = np.asarray(img.resize((cols, rows), Image.Resampling.BOX), dtype=np.float32) / 255.0
    rgb_grid = np.asarray(img_rgb.resize((cols, rows), Image.Resampling.BOX), dtype=np.float32) if color else None

    v = np.clip(grid, 0.0, 1.0)
    if brightness:
        v = np.clip(v + float(brightness), 0.0, 1.0)
    if contrast != 1.0:
        v = np.clip((v - 0.5) * float(contrast) + 0.5, 0.0, 1.0)
    if gamma != 1.0:
        v = v ** gamma

    if rgb_grid is not None:
        rgb_grid = rgb_grid / 255.0
        if brightness:
            rgb_grid = np.clip(rgb_grid + float(brightness), 0.0, 1.0)
        if contrast != 1.0:
            rgb_grid = np.clip((rgb_grid - 0.5) * float(contrast) + 0.5, 0.0, 1.0)
        if gamma != 1.0:
            rgb_grid = rgb_grid ** gamma

    n = len(ramp)
    scaled = v * (n - 1)
    idx = np.minimum(np.floor(scaled).astype(np.int64), n - 1)
    frac = scaled - idx
    if shading == "opacity":
        alphas = np.where(idx > 0, 0.45 + 0.55 * frac, 0.0)
    else:
        alphas = np.where(idx > 0, 1.0, 0.0)

    cells = []
    for r in range(rows):
        row = []
        for c_ in range(cols):
            a = int(round(float(alphas[r, c_]) * 255))
            if not a:
                row.append((" ", 0))
            elif rgb_grid is not None:
                px = tuple(int(x) for x in (np.clip(rgb_grid[r, c_], 0, 1) * 255))
                row.append((ramp[int(idx[r, c_])], a, px))
            else:
                row.append((ramp[int(idx[r, c_])], a))
        cells.append(row)
    return cells


def render_cells_to_png(cells: list[list[tuple[str, int]]], size_px: int, ink: str, ss: int = SUPERSAMPLE) -> bytes:
    """Rasterize glyph grid to transparent PNG bytes (supersampled for AA)."""
    big_px = size_px * ss
    adv, line = cell_metrics(big_px)
    adv = float(adv)
    cols = max((len(r) for r in cells), default=1) or 1
    canvas = (max(1, int(round(cols * adv))), max(1, len(cells) * int(line)))
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _font(big_px)
    r_, g_, b_ = (int(ink[i:i + 2], 16) for i in (1, 3, 5))

    for row_idx, row in enumerate(cells):
        y = row_idx * int(line)
        col = 0
        while col < len(row):
            alpha = row[col][1]
            if alpha == 0:
                col += 1
                continue
            color = row[col][2] if len(row[col]) > 2 else None
            # batch consecutive equal-(alpha,color) visible glyphs into one draw call
            end = col
            while (end < len(row) and row[end][1] == alpha and row[end][0] != " "
                   and (row[end][2] if len(row[end]) > 2 else None) == color):
                end += 1
            chunk = "".join(cell[0] for cell in row[col:end])
            fill = (*color, alpha) if color else (r_, g_, b_, alpha)
            if chunk.strip("\x00"):
                draw.text((col * adv, y), chunk, font=font, fill=fill)
            col = end
    if ss > 1:
        img = img.resize((canvas[0] // ss, canvas[1] // ss), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Assets: hash keys, build, gc
# ---------------------------------------------------------------------------

def _validate_render_params(art_id: str, params) -> None:
    import art_render
    if params is None:
        return
    if not isinstance(params, dict):
        raise ManifestError(f"manifest: art '{art_id}' render must be an object")
    unknown = sorted(set(params) - set(art_render.DEFAULTS))
    if unknown:
        raise ManifestError(f"manifest: art '{art_id}' render has unknown keys {unknown}")
    if params.get("mode", "ascii") not in art_render.MODES:
        raise ManifestError(f"manifest: art '{art_id}' render.mode must be one of {art_render.MODES}")
    for key, allowed in art_render.ENUMS.items():
        if key in params and params[key] not in allowed:
            raise ManifestError(f"manifest: art '{art_id}' render.{key} must be one of {allowed}")
    if "chars" in params:
        missing = missing_glyphs(str(params["chars"]))
        if missing:
            raise ManifestError(f"manifest: art '{art_id}' render.chars uses glyphs missing from the font: {''.join(sorted(missing))!r}")


def art_params(art: dict) -> dict:
    if art["type"] == "render":
        return {"render": art.get("render") or {}, "scale": art.get("scale")}
    keys = IMAGE_PARAM_KEYS if art["type"] == "image" else ENGINE_PARAM_KEYS
    return {k: art[k] for k in keys if k in art}


def asset_key(art: dict, source_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update(f"v{ENGINE_VERSION}".encode())
    if art["type"] == "render":
        import art_render
        h.update(f"r{art_render.RENDER_VERSION}".encode())
    h.update(b"\0")
    h.update(_canonical_json(art_params(art)))
    h.update(b"\0")
    h.update(source_bytes)
    return h.hexdigest()[:ASSET_KEY_HEX]


def source_bytes(art: dict) -> bytes:
    """Everything the render reads: the image, plus the subject mask when used."""
    data = Path(art["source"]).read_bytes()
    if art.get("type") == "render" and (art.get("render") or {}).get("cutout") == "subject":
        import subject_mask
        mask = subject_mask.find_mask(art["source"])
        data += b"\0mask\0" + (mask.read_bytes() if mask else b"")
    return data


def has_source(art: dict) -> bool:
    return bool(art.get("source")) and Path(art["source"]).is_file()


def art_key(art: dict) -> str | None:
    """Asset key from the source when it is here, else the key recorded in the
    manifest at build time (profiles can ship assets without the originals)."""
    if has_source(art):
        return asset_key(art, source_bytes(art))
    return art.get("key")


def asset_path(art_id: str, theme: str, key: str) -> Path:
    return GEN_DIR / f"{art_id}.{theme}.{key}.svg"


def current_asset(manifest: dict, art: dict, theme: str) -> Path | None:
    """Path of the up-to-date asset file for (art, theme), or None if not built."""
    key = art_key(art)
    if key is None:
        return None
    path = asset_path(art["id"], theme, key)
    return path if path.is_file() else None


def autosize_cell(cells: list[list[tuple[str, int]]], zone: dict) -> int:
    """Font px so the raster covers ~2x zone pixels on its longest axis."""
    adv, line = cell_metrics(DEFAULT_CELL)
    cols = max((len(r) for r in cells), default=1) or 1
    w0, h0 = cols * adv, len(cells) * line
    factor = min(2 * zone["width"] / w0, 2 * zone["height"] / h0)
    return max(4, min(128, int(DEFAULT_CELL * factor)))


def build_art_svg(art: dict, ink: str, ramps: dict, zone: dict) -> tuple[bytes, int, int]:
    """Render art source to (png_bytes, width, height) at intrinsic size."""
    if art["type"] == "image":
        ramp = resolve_ramp(ramps, art.get("ramp"))
        cells = image_to_cells(
            Path(art["source"]),
            ramp=ramp,
            detail=art.get("detail", DEFAULT_DETAIL),
            contrast=art.get("contrast", 1.0),
            gamma=art.get("gamma", 1.0),
            brightness=art.get("brightness", 0.0),
            invert=art.get("invert", False),
            shading=art.get("shading", "opacity"),
            equalize=art.get("equalize", False),
            color=art.get("color", False),
        )
    else:
        cells = text_to_cells(Path(art["source"]).read_text(encoding="utf-8", errors="replace"))

    size_px = art.get("cell") or autosize_cell(cells, zone)

    ss = SUPERSAMPLE
    png = render_cells_to_png(cells, size_px, ink, ss=ss)
    if len(png) > MAX_ASSET_BYTES and ss > 1:
        logger.warning("Art {art_id}: {size} bytes at {ss}x supersample, falling back to 1x",
                       art_id=art["id"], size=len(png), ss=ss)
        png = render_cells_to_png(cells, size_px, ink, ss=1)
    with Image.open(__import__("io").BytesIO(png)) as raster:
        width, height = raster.size
    return png, width, height


def build_render_raster(manifest: dict, art: dict, theme: str) -> tuple[bytes, str, int, int]:
    """type=render: styled colour raster (art_render) -> (bytes, mime, w, h)."""
    import art_render
    zone = get_zone(manifest)
    box = (zone["width"] * OUTPUT_SCALE, zone["height"] * OUTPUT_SCALE)
    img = art_render.render(art["source"], art.get("render") or {}, theme, box=box)
    data, mime = art_render.encode(img, theme)
    return data, mime, img.width, img.height


def build_asset(manifest: dict, art: dict, theme: str) -> Path:
    key = asset_key(art, source_bytes(art))
    path = asset_path(art["id"], theme, key)
    if art["type"] == "render":
        data, mime, width, height = build_render_raster(manifest, art, theme)
    else:
        data, width, height = build_art_svg(art, THEME_INK[theme], resolve_ramps(manifest), get_zone(manifest))
        mime = "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<image width="{width}" height="{height}" href="data:{mime};base64,{b64}"/></svg>\n'
    )
    if len(svg) > MAX_ASSET_BYTES:
        logger.warning("Art {art_id}: asset payload {size} bytes exceeds cap {cap}",
                       art_id=art["id"], size=len(svg), cap=MAX_ASSET_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    logger.info("Built asset {path} ({size} bytes)", path=path, size=len(svg))
    return path


def gc_assets(manifest: dict, report_only: bool = False, keep_disabled: bool = True) -> list[Path]:
    """Delete stale theme/key variants and orphans in arts/gen (matched files only).

    With keep_disabled=False assets of disabled arts count as stale too, so the
    publishing build leaves only what can actually be shown.
    """
    current = {}
    for art in manifest.get("arts") or []:
        if art["type"] in RASTER_TYPES and (keep_disabled or art.get("enabled", True)):
            key = art_key(art)
            if key is not None:
                current[art["id"]] = key

    stale: list[Path] = []
    if GEN_DIR.is_dir():
        for path in GEN_DIR.iterdir():
            match = ASSET_RE.match(path.name)
            if not match:
                continue
            if current.get(match["id"]) != match["key"]:
                stale.append(path)
                if not report_only:
                    path.unlink()
                    logger.info("Removed stale asset {path}", path=path)
    return stale


def ensure_assets(manifest: dict, ids: list[str] | None = None, gc: bool = True,
                  include_disabled: bool = False) -> bool:
    """Build any missing/stale assets for raster arts (both themes).

    Disabled arts are skipped unless named in `ids` or `include_disabled` is
    set, and a plain build also garbage-collects their assets, so a production
    build never publishes assets for arts under review.

    Records each art's asset key in the manifest entry (`key`), so the assets
    stay usable where the source image is not shipped. Returns True when a key
    changed and the manifest should be saved.
    """
    if not manifest:
        return False
    changed = False
    for art in manifest.get("arts") or []:
        if art["type"] not in RASTER_TYPES or ids and art["id"] not in ids:
            continue
        if not art.get("enabled", True) and not ids and not include_disabled:
            continue
        if not has_source(art):
            if not asset_fresh(manifest, art):
                logger.warning("Art {id}: no source image and no built assets; it will not be shown", id=art["id"])
            continue
        key = asset_key(art, source_bytes(art))
        for theme in THEMES:
            if not asset_path(art["id"], theme, key).is_file():
                build_asset(manifest, art, theme)
        if art.get("key") != key:
            art["key"] = key
            changed = True
    if gc:
        gc_assets(manifest, keep_disabled=include_disabled or bool(ids))
    return changed


def asset_fresh(manifest: dict, art: dict) -> bool:
    key = art_key(art)
    if key is None:
        return False
    return all(asset_path(art["id"], theme, key).is_file() for theme in THEMES)


# ---------------------------------------------------------------------------
# Fragment for the main metrics SVG
# ---------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"


def build_fragment(manifest: dict, art: dict, theme: str) -> str | None:
    """Nested <svg> fragment sized to the art zone, or None when asset missing."""
    path = current_asset(manifest, art, theme)
    if path is None:
        return None
    root = etree.fromstring(path.read_bytes())
    image = root.find(f"{{{SVG_NS}}}image")
    return fragment_for_image(manifest, art, image.get("href"),
                              int(float(root.get("width"))), int(float(root.get("height"))), path.name)


def fragment_for_image(manifest: dict, art: dict, href: str, width: int, height: int, asset_name: str = "") -> str:
    """Nested <svg> placing a raster (data: URI) into the art zone."""
    zone = get_zone(manifest)
    scale = max(0.2, min(1.5, float(art.get("scale", 1) or 1)))
    w = int(round(zone["width"] * scale))
    h = int(round(zone["height"] * scale))
    x = zone["x"] + (zone["width"] - w) // 2
    y = zone["y"] + (zone["height"] - h) // 2
    # No xmlns here: the fragment inherits the main SVG's default namespace.
    return (
        f'<svg x="{x}" y="{y}" width="{w}" height="{h}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" overflow="hidden" '
        f'class="ascii" id="ascii" data-art-id="{art["id"]}" data-art-type="{art["type"]}" '
        f'data-art-hash="{asset_name}">'
        f'<image x="0" y="0" width="{width}" height="{height}" href="{href}"/></svg>'
    )


# ---------------------------------------------------------------------------
# Manifest editing helpers (CLI)
# ---------------------------------------------------------------------------

def seed_manifest(manifest: dict, arts_dir: Path = Path("arts")) -> int:
    """Add type=text entries for arts/*.txt missing from the manifest. Returns count added."""
    known_sources = {a.get("source") for a in manifest.get("arts") or []}
    added = 0
    for path in sorted(arts_dir.glob("*.txt")):
        source = str(path)
        if source in known_sources or path.stat().st_size == 0:
            continue
        manifest.setdefault("arts", []).append({
            "id": path.stem,
            "type": "text",
            "source": source,
            "enabled": True,
        })
        added += 1
    return added


def default_manifest() -> dict:
    return {
        "version": 1,
        "zone": dict(DEFAULT_ZONE),
        "themes": dict(THEME_INK),
        "ramps": {},
        "arts": [],
    }


def add_art(manifest: dict, art_id: str, art_type: str, source: str, **params) -> dict:
    if find_art(manifest, art_id):
        raise ManifestError(f"art id '{art_id}' already exists")
    art = {"id": art_id, "type": art_type, "source": source, "enabled": False}
    art.update({k: v for k, v in params.items() if v is not None})
    manifest.setdefault("arts", []).append(art)
    return art


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_cli_manifest(path: Path) -> dict:
    manifest = load_manifest(path)
    if not manifest:
        manifest = default_manifest()
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ASCII art SVG engine (manifest-driven)")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="convert/stale assets, garbage-collect old ones")
    p_build.add_argument("ids", nargs="*", help="restrict to these art ids")
    p_build.add_argument("--no-gc", action="store_true")
    p_build.add_argument("--rebuild", action="store_true", help="force rebuild listed/all engine+image arts")
    p_build.add_argument("--all", action="store_true", help="also build disabled arts (local review only)")

    sub.add_parser("check", help="exit non-zero when assets are missing/stale")

    sub.add_parser("seed", help="add manifest entries for arts/*.txt (idempotent)")

    p_add_image = sub.add_parser("add-image", help="register an image art source")
    p_add_image.add_argument("path", type=Path)
    p_add_image.add_argument("--id", required=True)
    p_add_image.add_argument("--ramp", default=None)
    p_add_image.add_argument("--detail", type=int, default=None)
    p_add_image.add_argument("--contrast", type=float, default=None)
    p_add_image.add_argument("--gamma", type=float, default=None)
    p_add_image.add_argument("--invert", action="store_true", default=None)
    p_add_image.add_argument("--shading", choices=("opacity", "none"), default=None)
    p_add_image.add_argument("--equalize", action="store_true", default=None, help="histogram equalization for low-contrast photos")
    p_add_image.add_argument("--brightness", type=float, default=None)
    p_add_image.add_argument("--scale", type=float, default=None, help="art box size vs zone (0.2-1.5, default 1)")
    p_add_image.add_argument("--color", action="store_true", default=None, help="tint glyphs with source colors")
    p_add_image.add_argument("--in-place", action="store_true", help="reference the path instead of copying into arts/src/")

    p_add_text = sub.add_parser("add-text", help="register an engine-rendered text art source")
    p_add_text.add_argument("path", type=Path)
    p_add_text.add_argument("--id", required=True)
    p_add_text.add_argument("--cell", type=int, default=None)
    p_add_text.add_argument("--scale", type=float, default=None)
    p_add_text.add_argument("--in-place", action="store_true")

    sub.add_parser("list", help="show arts and asset freshness")

    args = parser.parse_args(argv)
    manifest_path = args.manifest
    manifest = _load_cli_manifest(manifest_path)

    if args.cmd == "seed":
        added = seed_manifest(manifest)
        if not manifest.get("zone"):
            manifest = {**default_manifest(), **manifest}
        save_manifest(manifest, manifest_path)
        logger.info("Seeded {added} text arts into {path}", added=added, path=manifest_path)
        return 0

    if args.cmd == "add-image":
        if not args.path.is_file():
            parser.error(f"no such file: {args.path}")
        if args.in_place:
            source = str(args.path)
        else:
            SRC_DIR.mkdir(parents=True, exist_ok=True)
            dest = SRC_DIR / f"{args.id}{args.path.suffix.lower()}"
            dest.write_bytes(args.path.read_bytes())
            source = str(dest)
        add_art(manifest, args.id, "image", source, ramp=args.ramp, detail=args.detail,
                contrast=args.contrast, gamma=args.gamma, brightness=args.brightness,
                invert=args.invert, shading=args.shading, equalize=args.equalize,
                color=args.color, scale=args.scale)
        save_manifest(manifest, manifest_path)
        logger.info("Registered image art '{id}' (enabled: false) — moderate the manifest, then `build`", id=args.id)
        return 0

    if args.cmd == "add-text":
        if not args.path.is_file():
            parser.error(f"no such file: {args.path}")
        if args.in_place:
            source = str(args.path)
        else:
            SRC_DIR.mkdir(parents=True, exist_ok=True)
            dest = SRC_DIR / f"{args.id}.txt"
            dest.write_bytes(args.path.read_bytes())
            source = str(dest)
        add_art(manifest, args.id, "engine", source, cell=args.cell, scale=args.scale)
        save_manifest(manifest, manifest_path)
        logger.info("Registered engine text art '{id}' (enabled: false) — moderate, then `build`", id=args.id)
        return 0

    if args.cmd == "build":
        if args.rebuild:
            # only arts whose source is here can be rendered again
            targets = [a for a in manifest.get("arts") or []
                       if a["type"] in RASTER_TYPES and has_source(a) and (not args.ids or a["id"] in args.ids)]
            for art in targets:
                key = asset_key(art, source_bytes(art))
                for theme in THEMES:
                    path = asset_path(art["id"], theme, key)
                    if path.is_file():
                        path.unlink()
        if ensure_assets(manifest, ids=args.ids or None, gc=not args.no_gc, include_disabled=args.all):
            save_manifest(manifest, manifest_path)
        return 0

    if args.cmd == "check":
        stale = [a["id"] for a in manifest.get("arts") or []
                 if a["type"] in RASTER_TYPES and a.get("enabled", True) and not asset_fresh(manifest, a)]
        for art_id in stale:
            logger.warning("Asset missing/stale for art '{id}'", id=art_id)
        return 1 if stale else 0

    if args.cmd == "list":
        for art in manifest.get("arts") or []:
            if art["type"] in RASTER_TYPES:
                state = "fresh" if asset_fresh(manifest, art) else "NOT BUILT"
            else:
                state = "inline"
            enabled = "on " if art.get("enabled", True) else "off"
            logger.info("{enabled} {id:<16} {type:<7} {state:<9} {source}",
                        enabled=enabled, id=art["id"], type=art["type"], state=state, source=art["source"])
        return 0


if __name__ == "__main__":
    sys.exit(main())
