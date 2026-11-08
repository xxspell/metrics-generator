import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from lxml import etree
from PIL import Image

import art_engine

SVG_NS = "http://www.w3.org/2000/svg"


def make_image(path: Path, size=(8, 8), left_half=0, right_half=255):
    img = Image.new("L", size)
    px = [[left_half if x < size[0] // 2 else right_half for x in range(size[0])] for _ in range(size[1])]
    img.putdata([v for row in px for v in row])
    img.save(path)


class TempGenDirMixin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._orig_gen_dir = art_engine.GEN_DIR
        art_engine.GEN_DIR = self.tmp / "gen"
        self.addCleanup(setattr, art_engine, "GEN_DIR", self._orig_gen_dir)


class TestAssetKey(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "a.png"
        self.src.write_bytes(b"pixels-v1")
        self.art = {"id": "a", "type": "image", "source": str(self.src), "detail": 40}

    def test_key_is_stable_and_tracks_source_bytes(self):
        k1 = art_engine.asset_key(self.art, self.src.read_bytes())
        k2 = art_engine.asset_key(self.art, self.src.read_bytes())
        self.assertEqual(k1, k2)
        self.src.write_bytes(b"pixels-v2")
        self.assertNotEqual(k1, art_engine.asset_key(self.art, self.src.read_bytes()))

    def test_key_tracks_render_params(self):
        base = art_engine.asset_key(self.art, b"x")
        changed = art_engine.asset_key({**self.art, "detail": 80}, b"x")
        self.assertNotEqual(base, changed)

    def test_key_tracks_engine_version(self):
        base = art_engine.asset_key(self.art, b"x")
        with mock.patch.object(art_engine, "ENGINE_VERSION", art_engine.ENGINE_VERSION + 1):
            self.assertNotEqual(base, art_engine.asset_key(self.art, b"x"))


class TestBuildAndGc(TempGenDirMixin):
    def setUp(self):
        super().setUp()
        self.src = self.tmp / "orb.png"
        make_image(self.src)
        self.art = {"id": "orb", "type": "image", "source": str(self.src), "detail": 16}
        self.manifest = {"zone": {"x": 0, "y": 0, "width": 100, "height": 100}, "arts": [self.art]}

    def test_build_is_fresh_then_stale_after_source_edit(self):
        art_engine.ensure_assets(self.manifest)
        self.assertTrue(art_engine.asset_fresh(self.manifest, self.art))
        first = sorted((self.tmp / "gen").iterdir())
        self.assertEqual(len(first), 2)  # dark + light

        make_image(self.src, left_half=128)
        self.assertFalse(art_engine.asset_fresh(self.manifest, self.art))
        art_engine.ensure_assets(self.manifest)

        files = sorted(p.name for p in (self.tmp / "gen").iterdir())
        self.assertEqual(len(files), 2, "stale variants must be garbage-collected")
        self.assertTrue(all(f.startswith("orb.") for f in files))
        self.assertTrue(art_engine.asset_fresh(self.manifest, self.art))

    def test_build_skips_disabled_unless_named_or_all(self):
        self.art["enabled"] = False
        art_engine.ensure_assets(self.manifest)
        self.assertFalse(art_engine.asset_fresh(self.manifest, self.art),
                         "disabled arts must not be built by a plain build (they would be published)")

        art_engine.ensure_assets(self.manifest, ids=["orb"])
        self.assertTrue(art_engine.asset_fresh(self.manifest, self.art))

        for path in (self.tmp / "gen").iterdir():
            path.unlink()
        art_engine.ensure_assets(self.manifest, include_disabled=True)
        self.assertTrue(art_engine.asset_fresh(self.manifest, self.art))

    def test_plain_build_gcs_assets_of_disabled_arts(self):
        art_engine.ensure_assets(self.manifest)
        self.assertTrue(art_engine.asset_fresh(self.manifest, self.art))

        self.art["enabled"] = False
        art_engine.ensure_assets(self.manifest, include_disabled=True)
        self.assertTrue(art_engine.asset_fresh(self.manifest, self.art),
                        "--all keeps assets of disabled arts for local review")

        art_engine.ensure_assets(self.manifest)
        self.assertEqual(list((self.tmp / "gen").iterdir()), [],
                         "a plain (publishing) build must not leave assets of disabled arts")

    def test_gc_preserves_unmatched_and_removes_orphans(self):
        gen = self.tmp / "gen"
        gen.mkdir(parents=True)
        (gen / "manual-notes.svg").write_text("<svg/>")
        (gen / "ghost.dark.0000000000.svg").write_text("<svg/>")  # id not in manifest -> orphan
        art_engine.build_asset(self.manifest, self.art, "dark")
        removed = art_engine.gc_assets(self.manifest)
        self.assertTrue((gen / "manual-notes.svg").exists())  # foreign file untouched
        self.assertFalse((gen / "ghost.dark.0000000000.svg").exists())
        self.assertIn(gen / "ghost.dark.0000000000.svg", removed)


class TestImageToCells(unittest.TestCase):
    def test_brightness_maps_to_glyph_density(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "split.png"
            make_image(path, size=(16, 16), left_half=0, right_half=255)
            cells = art_engine.image_to_cells(path, ramp=art_engine.BUILTIN_RAMPS["blocks"], detail=16)
            left = [ch for ch, _ in cells[len(cells) // 2][:3]]
            right = [ch for ch, _ in cells[len(cells) // 2][-3:]]
            self.assertTrue(all(ch == " " for ch in left), f"dark side should be blank: {left}")
            self.assertTrue(all(ch == "█" for ch in right), f"bright side should be dense: {right}")

    def test_invert_swaps_sides(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "split.png"
            make_image(path, size=(16, 16), left_half=0, right_half=255)
            cells = art_engine.image_to_cells(path, ramp=art_engine.BUILTIN_RAMPS["blocks"], detail=16, invert=True)
            right = [ch for ch, _ in cells[len(cells) // 2][-3:]]
            self.assertTrue(all(ch == " " for ch in right), f"bright side blank when inverted: {right}")

    def test_detail_is_clamped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.png"
            make_image(path, size=(8, 8))
            cells = art_engine.image_to_cells(path, ramp=" ░█", detail=10000)
            self.assertEqual(max(len(r) for r in cells), art_engine.MAX_COLS)


class TestBuildFragment(TempGenDirMixin):
    def test_fragment_fits_zone_and_embeds(self):
        src = self.tmp / "orb.png"
        make_image(src)
        art = {"id": "orb", "type": "image", "source": str(src), "detail": 16}
        manifest = {"zone": {"x": 15, "y": 15, "width": 370, "height": 500}, "arts": [art]}
        art_engine.build_asset(manifest, art, "dark")
        fragment = art_engine.build_fragment(manifest, art, "dark")
        self.assertIsNotNone(fragment)

        # must parse standalone inside the main SVG's namespace and fit the zone
        wrapped = f'<svg xmlns="{SVG_NS}">{fragment}</svg>'
        root = etree.fromstring(wrapped.encode())
        nested = root.find(f"{{{SVG_NS}}}svg")
        self.assertEqual(nested.get("x"), "15")
        self.assertEqual(nested.get("width"), "370")
        self.assertEqual(nested.get("height"), "500")
        self.assertEqual(nested.get("preserveAspectRatio"), "xMidYMid meet")
        self.assertEqual(nested.get("overflow"), "hidden")
        self.assertEqual(nested.get("id"), "ascii")
        self.assertEqual(nested.get("data-art-id"), "orb")
        self.assertIsNotNone(nested.find(f"{{{SVG_NS}}}image").get("href").removeprefix("data:image/png;base64,"))

    def test_missing_asset_returns_none(self):
        src = self.tmp / "orb.png"
        make_image(src)
        art = {"id": "orb", "type": "image", "source": str(src)}
        self.assertIsNone(art_engine.build_fragment({"arts": [art]}, art, "dark"))

    def test_scale_shrinks_fragment_box_centered(self):
        src = self.tmp / "orb.png"
        make_image(src)
        art = {"id": "orb", "type": "image", "source": str(src), "detail": 16, "scale": 0.5}
        manifest = {"zone": {"x": 15, "y": 15, "width": 370, "height": 500}, "arts": [art]}
        art_engine.build_asset(manifest, art, "dark")
        nested = etree.fromstring(
            f'<svg xmlns="{SVG_NS}">{art_engine.build_fragment(manifest, art, "dark")}</svg>'.encode()
        ).find(f"{{{SVG_NS}}}svg")
        self.assertEqual((nested.get("x"), nested.get("y")), ("107", "140"))
        self.assertEqual((nested.get("width"), nested.get("height")), ("185", "250"))


class TestColorMode(unittest.TestCase):
    def test_glyphs_keep_source_hue(self):
        # smooth horizontal gradient with strong red cast; luminance must stay monotone-ish
        x = np.linspace(60, 255, 128).astype(np.uint8)
        arr = np.zeros((96, 128, 3), np.uint8)
        arr[..., 0] = x[None, :]
        arr[..., 1] = (x * 0.35)[None, :]
        arr[..., 2] = (120 - x * 0.3)[None, :]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "g.png"
            Image.fromarray(arr).save(path)
            cells = art_engine.image_to_cells(path, ramp=" ░▒▓█", detail=48, color=True)
            colored = [c for row in cells for c in row if len(c) == 3]
            self.assertTrue(colored, "color mode must emit rgb cells")
            distinct = {c[2] for c in colored}
            self.assertGreater(len(distinct), 8, "colors must vary across the gradient")
            self.assertGreater(
                sum(1 for r, g, b in distinct if r > g and r > b), len(distinct) // 2,
                "red cast of the source must survive",
            )


class TestSelectionAndValidation(unittest.TestCase):
    def test_pick_art_avoids_previous_and_honours_enabled(self):
        manifest = {"arts": [
            {"id": "a", "type": "text", "source": "x", "enabled": True},
            {"id": "b", "type": "text", "source": "y", "enabled": True},
            {"id": "c", "type": "text", "source": "z", "enabled": False},
        ]}

        calls = []
        class Rng:
            def choices(self, pop, weights, k):
                calls.append([a["id"] for a in pop])
                return [{"id": pop[0]["id"]}]

        art_engine.pick_art(manifest, avoid_id=None, rng=Rng())
        self.assertEqual(calls[-1], ["a", "b"], "disabled arts must not be pickable")
        art_engine.pick_art(manifest, avoid_id="a", rng=Rng())
        self.assertEqual(calls[-1], ["b"], "previous art id must be excluded")
        art_engine.pick_art(manifest, avoid_id="zzz", rng=Rng())
        self.assertEqual(calls[-1], ["a", "b"], "unknown avoid id is harmless")

    def _rotation_manifest(self, share):
        arts = [{"id": f"t{i}", "type": "text", "source": "x"} for i in range(40)]
        arts += [{"id": "r1", "type": "render", "source": "x"}, {"id": "r2", "type": "render", "source": "x"}]
        m = {"arts": arts}
        if share is not None:
            m["rotation"] = {"render_share": share}
        return m

    def test_render_share_controls_how_often_colour_arts_appear(self):
        import random
        for share, lo, hi in ((0.5, 0.45, 0.55), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)):
            m = self._rotation_manifest(share)
            rng = random.Random(7)
            picks = [art_engine.pick_art(m, rng=rng)["type"] for _ in range(4000)]
            frac = picks.count("render") / len(picks)
            self.assertTrue(lo <= frac <= hi, f"share={share}: got {frac:.3f}")

    def test_without_render_share_all_enabled_arts_are_equal(self):
        import random
        rng = random.Random(3)
        m = self._rotation_manifest(None)
        frac = [art_engine.pick_art(m, rng=rng)["type"] for _ in range(6000)].count("render") / 6000
        self.assertAlmostEqual(frac, 2 / 42, delta=0.015)

    def test_render_share_never_repeats_the_only_colour_art(self):
        import random
        m = {"rotation": {"render_share": 1.0}, "arts": [
            {"id": "t", "type": "text", "source": "x"}, {"id": "solo", "type": "render", "source": "x"}]}
        rng = random.Random(1)
        self.assertEqual(art_engine.pick_art(m, avoid_id="solo", rng=rng)["id"], "t")

    def test_validate_rejects_bad_render_share(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "x.png"
            src.write_bytes(b"x")
            for bad in (1.5, -0.1, "half"):
                m = {"rotation": {"render_share": bad}, "arts": [{"id": "a", "type": "text", "source": str(src)}]}
                with self.assertRaises(art_engine.ManifestError):
                    art_engine.validate_manifest(m, base_dir=Path(td))

    def test_validate_rejects_duplicates_and_unknown_ramps(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "x.png"
            src.write_bytes(b"x")
            dup = {"arts": [
                {"id": "a", "type": "image", "source": str(src)},
                {"id": "a", "type": "text", "source": str(src)},
            ]}
            with self.assertRaises(art_engine.ManifestError):
                art_engine.validate_manifest(dup, base_dir=Path(td))
            # raw chars (2-40 printable) are valid ramps; unusable ones are rejected
            ok_raw = {"arts": [{"id": "b", "type": "image", "source": str(src), "ramp": " .oX#"}]}
            art_engine.validate_manifest(ok_raw, base_dir=Path(td))
            bad_raw = {"arts": [{"id": "b", "type": "image", "source": str(src), "ramp": "x" * 41}]}
            with self.assertRaises(art_engine.ManifestError):
                art_engine.validate_manifest(bad_raw, base_dir=Path(td))


class TestGlyphCoverage(unittest.TestCase):
    """The bundled font draws a tofu box for missing glyphs; ramps must not use them."""

    def test_missing_glyphs_detects_tofu(self):
        self.assertEqual(art_engine.missing_glyphs(" .:@#"), set())
        self.assertEqual(art_engine.missing_glyphs("@\u28ff"), {"\u28ff"})  # braille is not in DejaVu Sans Mono

    def test_builtin_ramps_are_fully_covered(self):
        for name, chars in art_engine.BUILTIN_RAMPS.items():
            self.assertEqual(art_engine.missing_glyphs(chars), set(), f"ramp {name!r}")

    def test_raw_ramp_with_missing_glyphs_is_rejected(self):
        with self.assertRaises(art_engine.ManifestError) as ctx:
            art_engine.resolve_ramp(art_engine.BUILTIN_RAMPS, " \u2840\u28ff")
        self.assertIn("\u28ff", str(ctx.exception))

    def test_manifest_ramp_with_missing_glyphs_is_rejected(self):
        with self.assertRaises(art_engine.ManifestError):
            art_engine.resolve_ramp({"mine": " \u28ff"}, "mine")


@unittest.skipUnless(art_engine.MANIFEST_PATH.is_file(),
                     "arts/ is local-only (gitignored; canonical copy lives in the target repo)")
class TestLocalManifest(unittest.TestCase):
    def test_local_manifest_valid_and_enabled_arts_built(self):
        manifest = art_engine.load_manifest()  # raises ManifestError when invalid
        self.assertTrue(manifest.get("arts"), "manifest must list arts")
        stale = [a["id"] for a in art_engine.enabled_arts(manifest, types=art_engine.RASTER_TYPES)
                 if not art_engine.asset_fresh(manifest, a)]
        self.assertEqual(stale, [], "run `uv run art_engine.py build`")


if __name__ == "__main__":
    unittest.main()
