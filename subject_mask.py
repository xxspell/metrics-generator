"""Subject mattes for colour arts: `<source stem>.mask.png` next to the source.

The mask is a greyscale PNG (white = subject) the size of the source image.
It is produced once, in the studio, by Apple Vision's foreground-instance
request (macOS 14+, the same "lift subject" as Photos) and then shipped with
the source. Rendering only ever *reads* the file, so a build is a pure
function of (source, mask, params) on any OS.
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL_SRC = ROOT / "tools" / "subject_mask.swift"
TOOL_BIN = ROOT / "tools" / ".bin" / "subject_mask"


def mask_path(source: str | os.PathLike) -> Path:
    src = Path(source)
    return src.with_name(f"{src.stem}.mask.png")


def find_mask(source) -> Path | None:
    """Existing mask for a source path (bytes sources have none)."""
    if not isinstance(source, (str, os.PathLike)):
        return None
    p = mask_path(source)
    return p if p.is_file() else None


def available() -> bool:
    return platform.system() == "Darwin" and TOOL_SRC.is_file()


def _tool() -> Path:
    if not TOOL_BIN.is_file() or TOOL_BIN.stat().st_mtime < TOOL_SRC.stat().st_mtime:
        TOOL_BIN.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(TOOL_SRC), "-o", str(TOOL_BIN)],
                       check=True, capture_output=True, timeout=300)
    return TOOL_BIN


def generate(source, force: bool = False) -> tuple[Path | None, str]:
    """-> (mask path or None, message). Never raises for a missing subject."""
    existing = find_mask(source)
    if existing and not force:
        return existing, "ok"
    if not available():
        return None, "subject cutout needs macOS 14+ (Apple Vision)"
    out = mask_path(source)
    try:
        r = subprocess.run([str(_tool()), str(source), str(out)], capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, OSError) as exc:
        return None, f"subject cutout failed: {exc}"
    if r.returncode == 3:
        out.unlink(missing_ok=True)
        return None, "no subject found in this image"
    if r.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        return None, f"subject cutout failed: {(r.stderr or '').strip()[:200]}"
    return out, "ok"


def subject_box(mask: Path, margin: float = 0.06) -> list[float] | None:
    """Bounding box of the subject as a crop [x, y, w, h] in 0..1, with a margin."""
    import numpy as np
    from PIL import Image
    with Image.open(mask) as im:
        m = np.asarray(im.convert("L"), dtype=np.uint8) > 127
    if not m.any():
        return None
    ys, xs = np.nonzero(m)
    h, w = m.shape
    x0, x1 = xs.min() / w, (xs.max() + 1) / w
    y0, y1 = ys.min() / h, (ys.max() + 1) / h
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    x0, y0, x1, y1 = max(0.0, x0 - mx), max(0.0, y0 - my), min(1.0, x1 + mx), min(1.0, y1 + my)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]
