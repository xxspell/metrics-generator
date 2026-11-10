import io
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp import FormData
from aiohttp.test_utils import AioHTTPTestCase
from PIL import Image

import art_engine
import art_studio


def png_bytes(color=(200, 80, 160), size=(64, 80)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, (10, 10, 20))
    img.paste(Image.new("RGB", (size[0] // 2, size[1] // 2), color), (size[0] // 4, size[1] // 4))
    img.save(buf, "PNG")
    return buf.getvalue()


class StudioTest(AioHTTPTestCase):
    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._orig_gen = art_engine.GEN_DIR
        art_engine.GEN_DIR = self.tmp / "arts" / "gen"
        self.manifest_path = self.tmp / "arts" / "manifest.json"
        return art_studio.build_app(self.tmp / ".studio", self.manifest_path, self.tmp / "arts" / "src")

    async def asyncTearDown(self):
        await super().asyncTearDown()
        art_engine.GEN_DIR = self._orig_gen
        self._tmp.cleanup()

    async def upload(self, name="Neon Girl.png"):
        form = FormData()
        form.add_field("file", png_bytes(), filename=name, content_type="image/png")
        r = await self.client.post("/api/drafts", data=form)
        self.assertEqual(r.status, 200, await r.text())
        return (await r.json())[0]

    async def test_import_creates_slugged_unique_drafts(self):
        a = await self.upload()
        b = await self.upload()
        self.assertEqual(a["id"], "neon-girl")
        self.assertEqual(b["id"], "neon-girl-2")
        listing = await (await self.client.get("/api/drafts")).json()
        self.assertEqual({d["id"] for d in listing}, {"neon-girl", "neon-girl-2"})
        self.assertTrue(all(not d["published"] for d in listing))

    async def test_rejects_non_images(self):
        form = FormData()
        form.add_field("file", b"not an image", filename="x.png")
        r = await self.client.post("/api/drafts", data=form)
        self.assertEqual(r.status, 400)

    async def test_preview_renders_webp_with_size_headers(self):
        d = await self.upload()
        for mode in ("ascii", "halftone", "mosaic"):
            r = await self.client.post("/api/preview", json={"id": d["id"], "theme": "light", "params": {"mode": mode, "cols": 20}})
            if r.status != 200:
                self.fail(await r.text())
            self.assertEqual(r.content_type, "image/webp")
            self.assertGreater(int(r.headers["X-Bytes"]), 0)

    @unittest.skipUnless(Path("dark_mode.svg").is_file() and Path("light_mode.svg").is_file(),
                         "profile SVG templates are local-only (gitignored)")
    async def test_card_embeds_draft_into_full_profile_svg(self):
        d = await self.upload()
        for theme in ("dark", "light"):
            r = await self.client.post("/api/card", json={"id": d["id"], "theme": theme, "params": {"cols": 20}, "scale": 0.8})
            if r.status != 200:
                self.fail(await r.text())
            svg = await r.text()
            self.assertIn('id="ascii"', svg)
            self.assertIn("data:image/webp;base64,", svg)
            self.assertIn('id="github_stats"', svg)

    async def test_missing_glyphs_are_reported_not_rendered(self):
        d = await self.upload()
        r = await self.client.post("/api/preview", json={"id": d["id"], "params": {"chars": " ⡀⣿"}})
        self.assertEqual(r.status, 400)
        self.assertIn("⣿", await r.text())

    async def test_publish_unpublish_lifecycle(self):
        d = await self.upload()
        r = await self.client.put(f"/api/drafts/{d['id']}", json={"params": {"mode": "halftone", "cols": 24}})
        self.assertEqual(r.status, 200)

        r = await self.client.post(f"/api/drafts/{d['id']}/publish", json={"enabled": True})
        self.assertEqual(r.status, 200, await r.text())
        manifest = json.loads(self.manifest_path.read_text())
        art = next(a for a in manifest["arts"] if a["id"] == d["id"])
        self.assertEqual(art["type"], "render")
        self.assertEqual(art["render"], {"mode": "halftone", "cols": 24})  # defaults stripped
        self.assertTrue(Path(art["source"]).is_file())
        self.assertTrue(art_engine.asset_fresh(manifest, art))
        self.assertEqual(art["key"], art_engine.asset_key(art, Path(art["source"]).read_bytes()),
                         "publish records the asset key so profiles can ship without sources")

        listing = await (await self.client.get("/api/drafts")).json()
        self.assertTrue(listing[0]["published"])
        self.assertFalse(listing[0]["dirty"])

        r = await self.client.delete(f"/api/drafts/{d['id']}")
        self.assertEqual(r.status, 409, "published drafts must be unpublished before deletion")

        r = await self.client.post(f"/api/drafts/{d['id']}/unpublish")
        self.assertEqual(r.status, 200)
        manifest = json.loads(self.manifest_path.read_text())
        self.assertFalse(any(a["id"] == d["id"] for a in manifest["arts"]))
        self.assertFalse(Path(art["source"]).exists())
        self.assertEqual(list(art_engine.GEN_DIR.glob(f"{d['id']}.*")), [])

        r = await self.client.delete(f"/api/drafts/{d['id']}")
        self.assertEqual(r.status, 200)

    async def test_rename_draft_slugifies_and_moves_files(self):
        d = await self.upload()
        r = await self.client.post(f"/api/drafts/{d['id']}/rename", json={"name": "Café Noir!"})
        self.assertEqual(r.status, 200, await r.text())
        new = await r.json()
        self.assertEqual(new["id"], "cafe-noir")
        self.assertEqual((await self.client.get("/api/drafts/cafe-noir/source")).status, 200)
        self.assertEqual((await self.client.get(f"/api/drafts/{d['id']}/source")).status, 404)

        other = await self.upload("other.png")
        r = await self.client.post(f"/api/drafts/{other['id']}/rename", json={"name": "cafe-noir"})
        self.assertEqual(r.status, 409, "names must stay unique")
        r = await self.client.post(f"/api/drafts/{other['id']}/rename", json={"name": "!!!"})
        self.assertEqual(r.status, 400)

    async def test_rename_published_updates_manifest_and_assets(self):
        d = await self.upload()
        await self.client.post(f"/api/drafts/{d['id']}/publish", json={"enabled": True})
        r = await self.client.post(f"/api/drafts/{d['id']}/rename", json={"name": "sakura"})
        self.assertEqual(r.status, 200, await r.text())
        manifest = json.loads(self.manifest_path.read_text())
        ids = [a["id"] for a in manifest["arts"]]
        self.assertIn("sakura", ids)
        self.assertNotIn(d["id"], ids)
        art = next(a for a in manifest["arts"] if a["id"] == "sakura")
        self.assertTrue(Path(art["source"]).is_file())
        self.assertTrue(art["enabled"])
        self.assertTrue(art_engine.asset_fresh(manifest, art))
        self.assertEqual(list(art_engine.GEN_DIR.glob(f"{d['id']}.*")), [], "old assets are gc'd")

    async def test_rotation_share_roundtrip(self):
        r = await self.client.get("/api/rotation")
        self.assertEqual((await r.json())["render_share"], None, "unset = everything equal")
        d = await self.upload()
        await self.client.post(f"/api/drafts/{d['id']}/publish", json={"enabled": True})
        r = await self.client.put("/api/rotation", json={"render_share": 0.5})
        self.assertEqual(r.status, 200, await r.text())
        manifest = json.loads(self.manifest_path.read_text())
        self.assertEqual(manifest["rotation"], {"render_share": 0.5})
        self.assertEqual((await (await self.client.get("/api/rotation")).json())["render_share"], 0.5)
        r = await self.client.put("/api/rotation", json={"render_share": None})
        self.assertNotIn("rotation", json.loads(self.manifest_path.read_text()))
        r = await self.client.put("/api/rotation", json={"render_share": 2})
        self.assertEqual(r.status, 400)

    async def test_editing_after_publish_marks_dirty(self):
        d = await self.upload()
        await self.client.post(f"/api/drafts/{d['id']}/publish", json={"enabled": False})
        await self.client.put(f"/api/drafts/{d['id']}", json={"params": {"mode": "mosaic"}})
        listing = await (await self.client.get("/api/drafts")).json()
        self.assertTrue(listing[0]["dirty"])
        self.assertFalse(listing[0]["enabled"])


if __name__ == "__main__":
    unittest.main()
