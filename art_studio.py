"""Art Studio: local web app for making profile arts at volume.

Workflow: import images (drop many / paste / URL) as *drafts*, style each one
with live dark+light previews at README size, then *publish* the good ones.

- Drafts live in .studio/ (gitignored, never copied to the profile repo).
- Publish copies the source into arts/src/, writes a `type: "render"` entry
  into arts/manifest.json and builds both theme assets into arts/gen/.
  Only published arts can reach the public profile (run.sh copies arts/).

Binds 127.0.0.1 only; no auth — a local dev tool.

    uv run art_studio.py [--port 8765]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote

import aiohttp
from aiohttp import web
from PIL import Image

import art_engine
import art_render

MAX_UPLOAD = 25 * 1024 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
IMAGE_EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}
PAGE_PATH = Path(__file__).with_name("art_studio.html")

# Named looks. Each is a full param set so switching presets is predictable.
PRESETS = {
    "neon-ascii": {"mode": "ascii", "cols": 52, "saturation": 1.4, "contrast": 1.1, "glow": 0.8, "underlay": 0.15, "edges": 0.35},
    "dense-ascii": {"mode": "ascii", "cols": 72, "saturation": 1.25, "contrast": 1.1, "glow": 0.5, "underlay": 0.3, "edges": 0.3},
    "halftone": {"mode": "halftone", "cols": 64, "saturation": 1.35, "contrast": 1.1, "glow": 0.35},
    "led-wall": {"mode": "mosaic", "cols": 44, "saturation": 1.3, "contrast": 1.15, "glow": 0.7, "gap": 0.22},
    "ink-print": {"mode": "ascii", "cols": 60, "saturation": 1.2, "glow": 0.0, "underlay": 0.1, "light_style": "print"},
}
RAMPS = {
    "letters": " .,:;i1tfLCG08@",
    "classic": " .:-=+*#%@",
    "blocks": " ░▒▓█",
    "dots": " .·:•●",
    "binary": " 01",
    "symbols": " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
}


class Store:
    """Drafts on disk: .studio/drafts/<id>.json + .studio/src/<id>.<ext>."""

    def __init__(self, root: Path, manifest_path: Path, arts_src: Path):
        self.root = root
        self.drafts_dir = root / "drafts"
        self.src_dir = root / "src"
        self.manifest_path = manifest_path
        self.arts_src = arts_src
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.src_dir.mkdir(parents=True, exist_ok=True)

    # -- drafts -------------------------------------------------------------
    def _meta_path(self, art_id: str) -> Path:
        return self.drafts_dir / f"{art_id}.json"

    def list(self) -> list[dict]:
        published = self.published()
        out = []
        for p in sorted(self.drafts_dir.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            d["published"] = d["id"] in published
            d["enabled"] = published.get(d["id"], {}).get("enabled", False)
            d["dirty"] = d["published"] and published[d["id"]].get("render", {}) != clean_params(d["params"])
            out.append(d)
        out.sort(key=lambda d: d.get("updated", 0), reverse=True)
        return out

    def get(self, art_id: str) -> dict:
        path = self._meta_path(art_id)
        if not ID_RE.match(art_id) or not path.is_file():
            raise web.HTTPNotFound(text=f"no draft '{art_id}'")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, draft: dict) -> dict:
        draft["updated"] = time.time()
        self._meta_path(draft["id"]).write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return draft

    def source_path(self, draft: dict) -> Path:
        return self.root / draft["source"]

    def taken_ids(self) -> set[str]:
        try:
            manifest_ids = {a["id"] for a in (art_engine.load_manifest(self.manifest_path) or {}).get("arts", [])}
        except art_engine.ManifestError:
            manifest_ids = set()
        return {p.stem for p in self.drafts_dir.glob("*.json")} | manifest_ids

    def unique_id(self, base: str) -> str:
        slug = slugify(base) or "art"
        taken = self.taken_ids()
        candidate, n = slug, 2
        while candidate in taken:
            candidate, n = f"{slug}-{n}", n + 1
        return candidate

    def create(self, data: bytes, name: str, origin: str | None = None) -> dict:
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt = im.format
                im.verify()
        except Exception as exc:
            raise web.HTTPBadRequest(text=f"not an image: {exc}")
        ext = IMAGE_EXT.get(fmt or "")
        if not ext:
            raise web.HTTPBadRequest(text=f"unsupported image format {fmt}")
        art_id = self.unique_id(Path(name).stem)
        rel = Path("src") / f"{art_id}{ext}"
        (self.root / rel).write_bytes(data)
        draft = {"id": art_id, "source": str(rel), "params": dict(PRESETS["neon-ascii"]),
                 "scale": 1.0, "origin": origin, "created": time.time()}
        return self.save(draft)

    def duplicate(self, art_id: str) -> dict:
        draft = self.get(art_id)
        new_id = self.unique_id(art_id)
        src = self.source_path(draft)
        rel = Path("src") / f"{new_id}{src.suffix}"
        (self.root / rel).write_bytes(src.read_bytes())
        copy = {**draft, "id": new_id, "source": str(rel), "created": time.time()}
        return self.save(copy)

    def rename(self, art_id: str, name: str) -> dict:
        """Change a draft's id (and, if published, its manifest entry + files)."""
        draft = self.get(art_id)
        new_id = slugify(name)
        if not new_id:
            raise web.HTTPBadRequest(text="use Latin letters or digits in the name")
        if new_id == art_id:
            return draft
        if new_id in self.taken_ids():
            raise web.HTTPConflict(text=f"the name {new_id!r} is already taken")

        src = self.source_path(draft)
        rel = Path("src") / f"{new_id}{src.suffix}"
        src.rename(self.root / rel)
        renamed = {**draft, "id": new_id, "source": str(rel)}
        self.save(renamed)
        self._meta_path(art_id).unlink()

        manifest = self.manifest()
        art = next((a for a in manifest.get("arts", []) if a.get("id") == art_id and a.get("type") == "render"), None)
        if art is not None:  # keep what is published as-is; only the name changes
            old_src = Path(art["source"])
            new_src = old_src.with_name(f"{new_id}{old_src.suffix}")
            old_src.rename(new_src)
            art["id"], art["source"] = new_id, str(new_src)
            art_engine.validate_manifest(manifest, base_dir=self.manifest_path.parent)
            art_engine.ensure_assets(manifest, ids=[new_id], gc=False)
            art_engine.save_manifest(manifest, self.manifest_path)
            art_engine.gc_assets(manifest)
        return renamed

    def delete(self, art_id: str) -> None:
        draft = self.get(art_id)
        if art_id in self.published():
            raise web.HTTPConflict(text="unpublish it first")
        self.source_path(draft).unlink(missing_ok=True)
        self._meta_path(art_id).unlink()

    # -- publishing ---------------------------------------------------------
    def manifest(self) -> dict:
        return art_engine.load_manifest(self.manifest_path) or art_engine.default_manifest()

    def published(self) -> dict:
        try:
            m = art_engine.load_manifest(self.manifest_path)
        except art_engine.ManifestError:
            m = {}
        return {a["id"]: a for a in (m or {}).get("arts", []) if a.get("type") == "render"}

    def publish(self, art_id: str, enabled: bool) -> dict:
        draft = self.get(art_id)
        manifest = self.manifest()
        src = self.source_path(draft)
        self.arts_src.mkdir(parents=True, exist_ok=True)
        dest = self.arts_src / f"{art_id}{src.suffix}"
        dest.write_bytes(src.read_bytes())
        art = {"id": art_id, "type": "render", "source": str(dest), "enabled": bool(enabled),
               "render": clean_params(draft["params"])}
        if draft.get("scale", 1.0) != 1.0:
            art["scale"] = draft["scale"]
        arts = [a for a in manifest.get("arts", []) if a.get("id") != art_id]
        manifest["arts"] = arts + [art]
        art_engine.validate_manifest(manifest, base_dir=self.manifest_path.parent)
        art_engine.ensure_assets(manifest, ids=[art_id], gc=False)  # also records art["key"]
        art_engine.save_manifest(manifest, self.manifest_path)
        art_engine.gc_assets(manifest)
        return art

    def unpublish(self, art_id: str) -> None:
        manifest = self.manifest()
        art = next((a for a in manifest.get("arts", []) if a.get("id") == art_id and a.get("type") == "render"), None)
        if art is None:
            raise web.HTTPNotFound(text="not published")
        manifest["arts"] = [a for a in manifest["arts"] if a is not art]
        art_engine.save_manifest(manifest, self.manifest_path)
        Path(art["source"]).unlink(missing_ok=True)
        art_engine.gc_assets(manifest)


def slugify(name: str) -> str:
    """'Café Noir!' -> 'cafe-noir' (ids end up in file names, so ASCII only)."""
    latin = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", latin).strip("-")[:40].strip("-")


def clean_params(params: dict) -> dict:
    """Only keys the renderer knows, minus defaults: small, stable manifests."""
    merged = art_render.merged(params)
    return {k: v for k, v in merged.items() if v != art_render.DEFAULTS[k]}


def sanitize_params(raw) -> dict:
    if not isinstance(raw, dict):
        raise web.HTTPBadRequest(text="params must be an object")
    params = {k: v for k, v in raw.items() if k in art_render.DEFAULTS}
    if params.get("mode", "ascii") not in art_render.MODES:
        raise web.HTTPBadRequest(text=f"mode must be one of {art_render.MODES}")
    chars = params.get("chars")
    if chars is not None:
        if not isinstance(chars, str) or not 2 <= len(chars) <= 80 or "\n" in chars:
            raise web.HTTPBadRequest(text="chars: 2-80 characters, sparse -> dense")
        missing = art_engine.missing_glyphs(chars)
        if missing:
            raise web.HTTPBadRequest(text=f"the bundled font cannot draw {''.join(sorted(missing))!r} (would render as boxes)")
    return params


# ---------------------------------------------------------------------------
# Rendering (off the event loop) + small cache for thumbnails
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[bytes, dict]] = {}
_CACHE_MAX = 256


def _render_bytes(src: Path, params: dict, theme: str, box: tuple[int, int]) -> tuple[bytes, dict]:
    t0 = time.perf_counter()
    img = art_render.render(str(src), params, theme, box=box)
    data, mime = art_render.encode(img, theme)
    info = {"w": img.width, "h": img.height, "bytes": len(data), "ms": int((time.perf_counter() - t0) * 1000), "mime": mime}
    return data, info


async def render_cached(src: Path, params: dict, theme: str, box: tuple[int, int]) -> tuple[bytes, dict]:
    key = hashlib.sha1(json.dumps([str(src), src.stat().st_mtime_ns, params, theme, box], sort_keys=True).encode()).hexdigest()
    hit = _CACHE.get(key)
    if hit:
        return hit
    result = await asyncio.get_running_loop().run_in_executor(None, _render_bytes, src, params, theme, box)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result


def _image_response(data: bytes, info: dict) -> web.Response:
    return web.Response(body=data, content_type=info["mime"], headers={
        "X-Size": f'{info["w"]}x{info["h"]}', "X-Bytes": str(info["bytes"]), "X-Ms": str(info["ms"]),
        "Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

def store(request) -> Store:
    return request.app["store"]


async def handle_page(request):
    return web.FileResponse(PAGE_PATH, headers={"Cache-Control": "no-store"})


async def handle_meta(request):
    zone = art_engine.get_zone(store(request).manifest())
    return web.json_response({"defaults": art_render.DEFAULTS, "modes": art_render.MODES, "presets": PRESETS,
                              "ramps": RAMPS, "zone": zone, "theme_bg": art_render.THEME_BG,
                              "cap_bytes": art_engine.MAX_ASSET_BYTES})


async def handle_list(request):
    return web.json_response(store(request).list())


async def handle_create(request):
    st = store(request)
    created = []
    if request.content_type.startswith("multipart/"):
        reader = await request.multipart()
        async for part in reader:
            if part.filename:
                data = await part.read(decode=False)
                if len(data) > MAX_UPLOAD:
                    raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=len(data))
                created.append(st.create(bytes(data), unquote(part.filename)))
    else:
        body = await request.json()
        if body.get("url"):
            data = await fetch_url(body["url"])
            created.append(st.create(data, body.get("name") or Path(body["url"].split("?")[0]).name or "web", origin=body["url"]))
        elif body.get("image"):
            header, _, b64 = body["image"].partition(",")
            created.append(st.create(base64.b64decode(b64), body.get("name") or "pasted"))
    if not created:
        raise web.HTTPBadRequest(text="nothing to import")
    return web.json_response(created)


async def fetch_url(url: str) -> bytes:
    if not re.match(r"^https?://", url):
        raise web.HTTPBadRequest(text="only http(s) URLs")
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "art-studio/1.0"}) as s:
        async with s.get(url) as r:
            if r.status != 200:
                raise web.HTTPBadRequest(text=f"download failed: HTTP {r.status}")
            data = await r.content.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise web.HTTPBadRequest(text="image too large")
    return data


async def handle_update(request):
    st = store(request)
    draft = st.get(request.match_info["id"])
    body = await request.json()
    if "params" in body:
        draft["params"] = sanitize_params(body["params"])
    if "scale" in body:
        draft["scale"] = max(0.2, min(1.5, float(body["scale"])))
    if "note" in body:
        draft["note"] = str(body["note"])[:200]
    return web.json_response(st.save(draft))


async def handle_delete(request):
    store(request).delete(request.match_info["id"])
    return web.json_response({"ok": True})


async def handle_rename(request):
    body = await request.json()
    renamed = await asyncio.get_running_loop().run_in_executor(
        None, store(request).rename, request.match_info["id"], str(body.get("name", "")))
    return web.json_response(renamed)


async def handle_duplicate(request):
    return web.json_response(store(request).duplicate(request.match_info["id"]))


async def handle_source(request):
    st = store(request)
    return web.FileResponse(st.source_path(st.get(request.match_info["id"])))


async def handle_thumb(request):
    """Rendered thumbnail with the draft's saved params (library cards)."""
    st = store(request)
    draft = st.get(request.match_info["id"])
    theme = request.query.get("theme", "dark")
    data, info = await render_cached(st.source_path(draft), draft["params"], theme, (240, 320))
    return _image_response(data, info)


async def handle_preview(request):
    """Live render with unsaved params at the asset's real resolution."""
    st = store(request)
    body = await request.json()
    draft = st.get(str(body.get("id", "")))
    theme = body.get("theme", "dark")
    if theme not in art_render.THEME_BG:
        raise web.HTTPBadRequest(text="theme must be dark|light")
    params = sanitize_params(body.get("params") or {})
    zone = art_engine.get_zone(st.manifest())
    box = (zone["width"] * art_engine.OUTPUT_SCALE, zone["height"] * art_engine.OUTPUT_SCALE)
    try:
        data, info = await render_cached(st.source_path(draft), params, theme, box)
    except Exception as exc:  # bad image / params: show it in the UI instead of a 500
        raise web.HTTPBadRequest(text=f"render failed: {exc}")
    return _image_response(data, info)


def _card_svg(src: Path, params: dict, theme: str, scale: float, manifest: dict, identity: str) -> bytes:
    import mock_svg_generator
    zone = art_engine.get_zone(manifest)
    box = (zone["width"] * art_engine.OUTPUT_SCALE, zone["height"] * art_engine.OUTPUT_SCALE)
    img = art_render.render(str(src), params, theme, box=box)
    data, mime = art_render.encode(img, theme)
    href = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    fragment = art_engine.fragment_for_image(manifest, {"id": "draft", "type": "render", "scale": scale},
                                             href, img.width, img.height)
    return mock_svg_generator.build_mock_svg(theme, identity, ascii_fragment=fragment)


async def handle_card(request):
    """Whole profile card (mock stats) with the draft's current, unsaved look."""
    st = store(request)
    body = await request.json()
    draft = st.get(str(body.get("id", "")))
    theme = body.get("theme", "dark")
    if theme not in art_render.THEME_BG:
        raise web.HTTPBadRequest(text="theme must be dark|light")
    if not Path(f"{theme}_mode.svg").is_file():
        raise web.HTTPConflict(text=f"{theme}_mode.svg not found: copy dark_mode.svg and light_mode.svg from your profile repository into the project root")
    params = sanitize_params(body.get("params") or {})
    scale = max(0.2, min(1.5, float(body.get("scale", draft.get("scale", 1.0)))))
    svg = await asyncio.get_running_loop().run_in_executor(
        None, _card_svg, st.source_path(draft), params, theme, scale, st.manifest(), request.app["identity"])
    return web.Response(body=svg, content_type="image/svg+xml", headers={"Cache-Control": "no-store"})


async def handle_rotation_get(request):
    st = store(request)
    manifest = st.manifest()
    arts = [a for a in manifest.get("arts", []) if a.get("enabled", True)]
    return web.json_response({"render_share": art_engine.render_share(manifest),
                              "colour": sum(a["type"] == "render" for a in arts),
                              "other": sum(a["type"] != "render" for a in arts)})


async def handle_rotation_put(request):
    st = store(request)
    share = (await request.json()).get("render_share")
    if share is not None and (isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 1):
        raise web.HTTPBadRequest(text="render_share must be a number from 0 to 1")
    manifest = st.manifest()
    if share is None:
        manifest.pop("rotation", None)
    else:
        manifest.setdefault("rotation", {})["render_share"] = round(float(share), 3)
    art_engine.validate_manifest(manifest, base_dir=st.manifest_path.parent)
    art_engine.save_manifest(manifest, st.manifest_path)
    return await handle_rotation_get(request)


async def handle_publish(request):
    body = await request.json() if request.can_read_body else {}
    art = await asyncio.get_running_loop().run_in_executor(
        None, store(request).publish, request.match_info["id"], bool(body.get("enabled", True)))
    return web.json_response(art)


async def handle_unpublish(request):
    store(request).unpublish(request.match_info["id"])
    return web.json_response({"ok": True})


@web.middleware
async def errors_as_text(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except art_engine.ManifestError as exc:
        return web.Response(status=400, text=str(exc))


def build_app(root: Path = Path(".studio"), manifest_path: Path = art_engine.MANIFEST_PATH,
              arts_src: Path = art_engine.SRC_DIR) -> web.Application:
    app = web.Application(client_max_size=MAX_UPLOAD * 4, middlewares=[errors_as_text])
    app["store"] = Store(root, manifest_path, arts_src)
    app["identity"] = os.environ.get("SVG_HEADER_IDENTITY") or os.environ.get("USER_NAME") or "you@github"
    r = app.router
    r.add_get("/", handle_page)
    r.add_get("/api/meta", handle_meta)
    r.add_get("/api/drafts", handle_list)
    r.add_post("/api/drafts", handle_create)
    r.add_put("/api/drafts/{id}", handle_update)
    r.add_delete("/api/drafts/{id}", handle_delete)
    r.add_post("/api/drafts/{id}/duplicate", handle_duplicate)
    r.add_post("/api/drafts/{id}/rename", handle_rename)
    r.add_get("/api/drafts/{id}/source", handle_source)
    r.add_get("/api/drafts/{id}/thumb", handle_thumb)
    r.add_post("/api/drafts/{id}/publish", handle_publish)
    r.add_post("/api/drafts/{id}/unpublish", handle_unpublish)
    r.add_post("/api/preview", handle_preview)
    r.add_post("/api/card", handle_card)
    r.add_get("/api/rotation", handle_rotation_get)
    r.add_put("/api/rotation", handle_rotation_put)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Art Studio (local web UI)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"Art Studio: http://{args.host}:{args.port}")
    web.run_app(build_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
