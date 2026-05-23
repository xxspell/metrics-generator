import re
import unittest

from github_stats import generate_github_stats_svg, justify_text


class TestGithubStatsFormatting(unittest.TestCase):
    def extract_cc_segment(self, svg: str, segment_id: str) -> str:
        pattern = rf'id="{segment_id}">(.*?)</tspan>'
        match = re.search(pattern, svg)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_justify_text_uses_short_suffix_for_very_large_numbers(self):
        _, value = justify_text(3_500_000, length=8, use_k=True)
        self.assertLessEqual(len(value), 8)
        self.assertTrue(value.lower().endswith("m"))

    def test_justify_text_uses_suffixes_for_extreme_values(self):
        _, value = justify_text(2_138_000_000, length=9, use_k=True)
        self.assertLessEqual(len(value), 9)
        self.assertTrue(value.lower().endswith(("m", "b", "t")))

    def test_github_stats_contributed_padding_is_not_empty_for_3_digits(self):
        svg = generate_github_stats_svg(
            x=390,
            y=390,
            fill_color="#c9d1d9",
            commit_data=1643,
            star_data=245,
            repo_data=1011,
            contrib_data=115,
            follower_data=1421,
            loc_total=3_500_000,
            loc_add=5_600_000,
            loc_del=2_138_000,
            recent_commit_data=22,
            streak_data=10,
        )

        contrib_dots = self.extract_cc_segment(svg, "contrib_data_dots")
        self.assertEqual(contrib_dots.strip(" ."), "")
        self.assertGreaterEqual(len(contrib_dots), 1)

    def test_github_stats_loc_segments_fit_compact_width(self):
        svg = generate_github_stats_svg(
            x=390,
            y=390,
            fill_color="#c9d1d9",
            commit_data=1643,
            star_data=245,
            repo_data=1011,
            contrib_data=115,
            follower_data=1421,
            loc_total=3_500_000,
            loc_add=5_600_000,
            loc_del=2_138_000,
            recent_commit_data=22,
            streak_data=10,
        )

        loc_val = re.search(r'id="loc_data">(.*?)</tspan>', svg).group(1)
        loc_add_val = re.search(r'id="loc_add">(.*?)</tspan>', svg).group(1)
        loc_del_val = re.search(r'id="loc_del">(.*?)</tspan>', svg).group(1)

        self.assertLessEqual(len(loc_val), 9)
        self.assertLessEqual(len(loc_add_val), 9)
        self.assertLessEqual(len(loc_del_val), 9)


if __name__ == "__main__":
    unittest.main()
