import re
import unittest

from github_stats import generate_github_stats_svg
from languages_svg import generate_languages_svg
from lastfm import generate_lastfm_svg


def plain_header_line(svg: str, marker: str) -> str:
    line = next(l for l in svg.split("\n") if marker in l)
    return re.sub(r"<[^>]+>", "", line).strip()


class TestSvgHeaders(unittest.TestCase):
    def test_header_lengths_match_reference(self):
        stats_svg = generate_github_stats_svg(
            x=390,
            y=390,
            fill_color="#c9d1d9",
            commit_data=2118,
            star_data=245,
            repo_data=114,
            contrib_data=116,
            follower_data=1434,
            loc_total=3514085,
            loc_add=5654001,
            loc_del=2139916,
            recent_commit_data=53,
            streak_data=3,
        )
        langs_svg = generate_languages_svg(
            x=390,
            y=220,
            fill_color="#c9d1d9",
            languages=[
                {"name": "Python", "color": "#3572A5", "percentage": 90.0, "bytes_count": 900000},
                {"name": "Lua", "color": "#000080", "percentage": 10.0, "bytes_count": 100000},
            ],
        )
        lastfm_svg = generate_lastfm_svg(
            {
                "tracks": [
                    {"number": "1", "title": "A", "artist": "B", "track_url": ""},
                    {"number": "2", "title": "C", "artist": "D", "track_url": ""},
                ],
                "summary": {"total_scrobbles": 0, "last_scrobble_date": None},
            },
            start_x=390,
            start_y=60,
            max_line_length=60,
        )

        ref = plain_header_line(stats_svg, "- GitHub Stats")
        langs = plain_header_line(langs_svg, "- Most used languages")
        lastfm = plain_header_line(lastfm_svg, "- Last.fm Recent Scrobbles")

        self.assertEqual(len(langs), len(ref))
        self.assertEqual(len(lastfm), len(ref))
        self.assertIn('</tspan> -', next(l for l in stats_svg.split('\n') if '- GitHub Stats' in l))
        self.assertIn('</tspan> -', next(l for l in langs_svg.split('\n') if '- Most used languages' in l))
        self.assertIn('</tspan> -', next(l for l in lastfm_svg.split('\n') if '- Last.fm Recent Scrobbles' in l))


if __name__ == "__main__":
    unittest.main()
