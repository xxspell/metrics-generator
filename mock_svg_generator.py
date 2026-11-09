import argparse
from pathlib import Path

from lxml import etree
from loguru import logger

import art_engine
from art import ascii_to_svg, load_ascii_from_file
from github_stats import generate_github_stats_svg
from languages_svg import generate_languages_svg
from lastfm import generate_lastfm_svg
from svg_header import make_header_tail


def overwrite_block(root, svg_block: str, block_id: str):
    new_text_element = etree.fromstring(svg_block)
    candidates = root.xpath(f"//*[@id='{block_id}']")
    if not candidates:
        raise ValueError(f"SVG element with id={block_id} not found")
    old_text_element = candidates[0]
    parent = old_text_element.getparent()
    parent.replace(old_text_element, new_text_element)


def overwrite_identity_header(root, identity: str):
    header_text_candidates = root.xpath("//*[local-name()='text' and @x='390' and @y='30']")
    if not header_text_candidates:
        raise ValueError("Top identity header not found")

    header_text = header_text_candidates[0]
    tspan_candidates = header_text.xpath("./*[local-name()='tspan']")
    if not tspan_candidates:
        raise ValueError("Top identity header tspan not found")

    tspan = tspan_candidates[0]
    tspan.text = identity
    tspan.tail = make_header_tail(identity)


def normalize_header_tails(root):
    header_nodes = root.xpath(
        "//*[local-name()='text' and @x='390']/*[local-name()='tspan' and starts-with(normalize-space(text()), '- ')]"
    )
    for tspan in header_nodes:
        title = (tspan.text or "").strip()
        tspan.tail = make_header_tail(title)


def build_mock_ascii_svg(theme: str, fill_color: str, art_id: str | None = None):
    """Prefer a built engine/image art; fall back to the first legacy text art."""
    manifest = art_engine.load_manifest()
    if manifest:
        candidates = art_engine.enabled_arts(manifest, types=art_engine.RASTER_TYPES)
        if art_id:
            art = art_engine.find_art(manifest, art_id)
            if art is None:
                raise FileNotFoundError(f"Art id not found in manifest: {art_id}")
            candidates = [art]
        for art in candidates:
            fragment = art_engine.build_fragment(manifest, art, theme)
            if fragment:
                return fragment
            logger.warning(f"Asset for art '{art['id']}' is not built; run `uv run art_engine.py build {art['id']}`")
    files = sorted(p for p in Path("arts").glob("*.txt") if p.is_file() and p.stat().st_size)
    if not files:
        raise FileNotFoundError("No usable arts found (manifest empty and no arts/*.txt)")
    return ascii_to_svg(load_ascii_from_file(str(files[0])), 15, 30, fill_color)


def build_mock_lastfm_svg():
    scrobbles = {
        "tracks": [
            {"number": "1", "title": "Dreamer Drill", "artist": "Luciano", "track_url": ""},
            {"number": "2", "title": "Z-BRAVO", "artist": "DJ VETA3", "track_url": ""},
            {"number": "3", "title": "RISE AGAIN", "artist": "Airglo", "track_url": ""},
            {"number": "4", "title": "Unlock", "artist": "Luciano", "track_url": ""},
            {
                "number": "5",
                "title": "NÚCLEO TITÂNICO - Ultra Slowed",
                "artist": "MC LOCKED & dufuza",
                "track_url": "",
            },
        ],
        "summary": {"total_scrobbles": 36123, "last_scrobble_date": None},
    }
    return generate_lastfm_svg(scrobbles, start_x=390, start_y=60, max_line_length=60)


def build_mock_languages_svg(fill_color: str):
    languages = [
        {"name": "Python", "color": "#3572A5", "percentage": 63.5, "bytes_count": 11_100_000},
        {"name": "JavaScript", "color": "#c9bb4d", "percentage": 13.0, "bytes_count": 2_300_000},
        {"name": "TypeScript", "color": "#2b7489", "percentage": 10.8, "bytes_count": 1_900_000},
        {"name": "Swift", "color": "#fa7343", "percentage": 8.3, "bytes_count": 1_400_000},
        {"name": "Rust", "color": "#dea584", "percentage": 4.4, "bytes_count": 784_600},
    ]
    return generate_languages_svg(x=390, y=220, fill_color=fill_color, languages=languages)


def build_mock_github_stats_svg(fill_color: str):
    return generate_github_stats_svg(
        x=390,
        y=390,
        fill_color=fill_color,
        commit_data=2158,
        star_data=246,
        repo_data=114,
        contrib_data=118,
        follower_data=1435,
        loc_total=3_520_267,
        loc_add=5_660_304,
        loc_del=2_140_037,
        recent_commit_data=66,
        streak_data=1,
    )


THEME_FILL = {"dark": "#c9d1d9", "light": "#24292f"}


def build_mock_svg(theme: str, identity: str, art_id: str | None = None, ascii_fragment: str | None = None,
                   template_dir: Path = Path(".")) -> bytes:
    """Full profile card on mock data. `ascii_fragment` overrides the art block
    (used by the Art Studio to preview an unpublished draft)."""
    fill_color = THEME_FILL[theme]
    tree = etree.parse(str(template_dir / f"{theme}_mode.svg"))
    root = tree.getroot()

    overwrite_identity_header(root, identity)
    overwrite_block(root, build_mock_lastfm_svg(), "lastfm_block")
    overwrite_block(root, ascii_fragment or build_mock_ascii_svg(theme, fill_color, art_id), "ascii")
    overwrite_block(root, build_mock_languages_svg(fill_color), "languages_block")
    overwrite_block(root, build_mock_github_stats_svg(fill_color), "github_stats")
    normalize_header_tails(root)
    return etree.tostring(tree, encoding="utf-8", xml_declaration=True)


def generate_mock_file(template_path: Path, output_path: Path, fill_color: str, identity: str, art_id: str | None = None):
    theme = "dark" if "dark" in str(template_path) else "light"
    output_path.write_bytes(build_mock_svg(theme, identity, art_id, template_dir=template_path.parent))


def main():
    parser = argparse.ArgumentParser(description="Offline SVG generator without API calls")
    parser.add_argument("--identity", default="xxspell@hachimaru", help="Identity in top header")
    parser.add_argument(
        "--out-dir",
        default="mock_output",
        help="Directory for generated mock SVG files",
    )
    parser.add_argument("--art-id", default=None, help="Force a manifest art id (engine/image) for the preview")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    generate_mock_file(
        template_path=Path("dark_mode.svg"),
        output_path=out_dir / "dark_mode.mock.svg",
        fill_color="#c9d1d9",
        identity=args.identity,
        art_id=args.art_id,
    )
    generate_mock_file(
        template_path=Path("light_mode.svg"),
        output_path=out_dir / "light_mode.mock.svg",
        fill_color="#24292f",
        identity=args.identity,
        art_id=args.art_id,
    )

    print(f"Generated: {out_dir / 'dark_mode.mock.svg'}")
    print(f"Generated: {out_dir / 'light_mode.mock.svg'}")


if __name__ == "__main__":
    main()
