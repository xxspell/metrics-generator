import re
import unittest

from github_stats import generate_github_stats_svg


def text_line(svg: str, marker: str) -> str:
    line = next(l for l in svg.split("\n") if marker in l)
    return re.sub(r"<[^>]+>", "", line)


class TestLocOptimization(unittest.TestCase):
    def test_uses_minimal_required_compaction(self):
        svg = generate_github_stats_svg(
            x=390,
            y=390,
            fill_color="#c9d1d9",
            commit_data=2118,
            star_data=245,
            repo_data=112,
            contrib_data=116,
            follower_data=1434,
            loc_total=3_514_085,
            loc_add=5_654_001,
            loc_del=2_139_916,
            recent_commit_data=53,
            streak_data=3,
        )
        loc = text_line(svg, "Lines of Code on GitHub")

        self.assertIn("3,514,085", loc)
        self.assertIn("5,654k", loc)
        self.assertIn("2,139k", loc)
        self.assertEqual(loc.lower().count("k"), 2)

    def test_keeps_non_compacted_values_when_line_fits(self):
        svg = generate_github_stats_svg(
            x=390,
            y=390,
            fill_color="#c9d1d9",
            commit_data=2118,
            star_data=245,
            repo_data=112,
            contrib_data=116,
            follower_data=1434,
            loc_total=1_000_000,
            loc_add=250_000,
            loc_del=120_000,
            recent_commit_data=53,
            streak_data=3,
        )
        loc = text_line(svg, "Lines of Code on GitHub")

        self.assertIn("1,000,000", loc)
        self.assertIn("250,000", loc)
        self.assertIn("120k", loc)
        self.assertEqual(loc.lower().count("k"), 1)
        self.assertLessEqual(len(loc.strip()), len('. Commits: .................. 2,118 | Followers: ..... 1,434'))


if __name__ == "__main__":
    unittest.main()
