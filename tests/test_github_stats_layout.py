import re
import unittest

from github_stats import generate_github_stats_svg


class TestGithubStatsLayout(unittest.TestCase):
    def test_loc_value_has_separator_after_colon_when_no_padding_left(self):
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

        plain = re.sub(r"<[^>]+>", "", svg)
        self.assertIn("Lines of Code on GitHub: 3,500,000", plain)


if __name__ == "__main__":
    unittest.main()
