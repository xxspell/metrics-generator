import base64
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
from lxml import etree
from PIL import Image

import art_engine
import art_render

SVG_NS = "http://www.w3.org/2000/svg"


def make_photo(path: Path, size=(96, 128)):
    """Colourful gradient with a bright disc: enough structure for every mode."""
    w, h = size
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    rgb = np.stack([x / w, y / h, 1 - x / w], axis=-1)
    disc = ((x - w / 2) ** 2 + (y - h / 2) ** 2) < (min(w, h) / 4) ** 2
    rgb[disc] = [1.0, 0.9, 0.3]
    Image.fromarray((rgb * 255).astype(np.uint8)).save(path)


class TestRender(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "p.png"
        make_photo(self.src)

    def test_every_mode_renders_inside_box_with_ink(self):
        for mode in art_render.MODES:
            for theme in ("dark", "light"):
                img = art_render.render(str(self.src), {"mode": mode, "cols": 24}, theme, box=(200, 300))
                self.assertEqual(img.mode, "RGBA")
                self.assertLessEqual(img.width, 200 + 2 * 18)
                self.assertLessEqual(img.height, 300 + 2 * 18)
                self.assertGreater(np.asarray(img)[..., 3].mean(), 5, f"{mode}/{theme} rendered nothing")

    def test_default_fit_fills_the_whole_zone(self):
        square = Path(self._tmp.name) / "sq.png"
        make_photo(square, size=(100, 100))
        img = art_render.render(str(square), {"cols": 24}, "dark", box=(370, 500))
        self.assertAlmostEqual(img.width / img.height, 370 / 500, delta=0.03)
        self.assertGreaterEqual(img.width, 360)
        contain = art_render.render(str(square), {"cols": 24, "fit": "contain"}, "dark", box=(370, 500))
        self.assertAlmostEqual(contain.width / contain.height, 1.0, delta=0.03)

    def test_light_theme_defaults_to_print_halftone(self):
        """Glyph strokes carry no tone on white; light pages get a print halftone."""
        default = np.asarray(art_render.render(str(self.src), {"mode": "ascii", "cols": 24}, "light", box=(200, 300)))
        # ascii cells are 2:1, dots 1:1 -> the auto halftone uses ~0.7x columns (same cell count)
        explicit = np.asarray(art_render.render(str(self.src), {"mode": "halftone", "cols": 17, "light_style": "print"}, "light", box=(200, 300)))
        self.assertTrue(np.array_equal(default, explicit))
        same = np.asarray(art_render.render(str(self.src), {"mode": "ascii", "cols": 24, "light_mode": "same"}, "light", box=(200, 300)))
        self.assertFalse(np.array_equal(default, same), "light_mode=same keeps the dark theme's mode")

    def test_light_match_keeps_dark_silhouette_without_card(self):
        dark = art_render.render(str(self.src), {"cols": 24, "glow": 0}, "dark", box=(200, 300))
        light = art_render.render(str(self.src), {"cols": 24, "light_style": "match", "light_mode": "same"}, "light", box=(200, 300))
        self.assertEqual(light.size, dark.size, "no card padding")
        a_dark = np.asarray(dark)[..., 3] > 0
        a_light = np.asarray(light)[..., 3] > 0
        self.assertGreater((a_dark == a_light).mean(), 0.97, "same glyph layout as the dark theme")

    def test_light_card_style_still_available(self):
        img = art_render.render(str(self.src), {"cols": 24, "light_style": "card"}, "light", box=(200, 300))
        a = np.asarray(img)
        padding_px = a[a.shape[0] // 2, 9]  # inside the card's padding, left of the art
        self.assertEqual(tuple(int(v) for v in padding_px[:3]), art_render.CARD_BG)
        self.assertEqual(int(padding_px[3]), 255)

    def test_light_print_style_uses_dark_ink(self):
        img = art_render.render(str(self.src), {"cols": 24, "light_style": "print", "glow": 0}, "light", box=(200, 300))
        a = np.asarray(img).astype(np.float32)
        inked = a[..., 3] > 200
        self.assertTrue(inked.any())
        lum = (a[..., :3][inked] @ np.array([0.2126, 0.7152, 0.0722])) / 255
        self.assertLess(float(np.median(lum)), 0.5, "print style must deepen colours for white paper")

    def test_encode_is_opaque_webp_on_card_colour(self):
        img = art_render.render(str(self.src), {"cols": 24}, "dark", box=(200, 300))
        data, mime = art_render.encode(img, "dark")
        self.assertEqual(mime, "image/webp")
        out = Image.open(io.BytesIO(data))
        self.assertEqual(out.format, "WEBP")
        self.assertNotIn("A", out.mode)

    def test_vignette_fades_edges_only(self):
        m = art_render.vignette_mask(40, 30, 0.35)
        self.assertAlmostEqual(float(m[20, 15]), 1.0, places=3)
        self.assertLess(float(m[0, 0]), 0.05, "corners dissolve")
        self.assertTrue(np.all(art_render.vignette_mask(4, 4, 0) == 1), "0 turns it off")

    def test_crop_selects_region(self):
        full = art_render.render(str(self.src), {"cols": 16, "fit": "contain"}, "dark", box=(200, 200))
        top = art_render.render(str(self.src), {"cols": 16, "fit": "contain", "crop": [0, 0, 1, 0.25]}, "dark", box=(200, 200))
        self.assertGreater(top.width / top.height, full.width / full.height * 2)


class TestRenderArtType(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._orig_gen_dir = art_engine.GEN_DIR
        art_engine.GEN_DIR = self.tmp / "gen"
        self.addCleanup(setattr, art_engine, "GEN_DIR", self._orig_gen_dir)
        self.src = self.tmp / "p.png"
        make_photo(self.src)
        self.art = {"id": "pic", "type": "render", "source": str(self.src), "enabled": True,
                    "render": {"mode": "halftone", "cols": 20}}
        self.manifest = {"zone": {"x": 15, "y": 15, "width": 370, "height": 500}, "arts": [self.art]}

    def test_validate_accepts_render_and_rejects_unknown_mode(self):
        art_engine.validate_manifest(self.manifest, base_dir=self.tmp)
        bad = {"arts": [{**self.art, "render": {"mode": "laser"}}]}
        with self.assertRaises(art_engine.ManifestError):
            art_engine.validate_manifest(bad, base_dir=self.tmp)

    def test_key_tracks_render_params(self):
        k1 = art_engine.asset_key(self.art, b"x")
        k2 = art_engine.asset_key({**self.art, "render": {"mode": "halftone", "cols": 21}}, b"x")
        self.assertNotEqual(k1, k2)

    def test_key_tracks_renderer_version(self):
        from unittest import mock
        base = art_engine.asset_key(self.art, b"x")
        with mock.patch.object(art_render, "RENDER_VERSION", art_render.RENDER_VERSION + 1):
            self.assertNotEqual(base, art_engine.asset_key(self.art, b"x"),
                                "renderer changes must invalidate published render assets")

    def test_build_writes_webp_assets_and_fragment(self):
        art_engine.ensure_assets(self.manifest)
        self.assertTrue(art_engine.asset_fresh(self.manifest, self.art))
        for theme in art_engine.THEMES:
            frag = art_engine.build_fragment(self.manifest, self.art, theme)
            self.assertIn("data:image/webp;base64,", frag)
            root = etree.fromstring(frag.replace("<svg ", f'<svg xmlns="{SVG_NS}" ', 1))
            href = root.find(f"{{{SVG_NS}}}image").get("href")
            Image.open(io.BytesIO(base64.b64decode(href.split(",", 1)[1]))).verify()


if __name__ == "__main__":
    unittest.main()
