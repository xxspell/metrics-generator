import re
import unittest

from github_stats import generate_github_stats_svg


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


class TestGithubStatsAlignment(unittest.TestCase):
    def _stats_lines(self, svg: str):
        lines = []
        for line in svg.split("\n"):
            if "Repos" in line or "Commits</tspan>:" in line or "Commits (7d)" in line or "Lines of Code on GitHub" in line:
                lines.append(strip_tags(line))
        return lines

    def test_separator_pipe_column_is_consistent(self):
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
        lines = self._stats_lines(svg)
        repos_line = next(l for l in lines if "Repos" in l)
        commits_line = next(l for l in lines if "Commits:" in l and "(7d)" not in l)
        streak_line = next(l for l in lines if "Commits (7d)" in l)

        self.assertIn("|", repos_line)
        self.assertIn("|", commits_line)
        self.assertIn("|", streak_line)

        pipe_idx = commits_line.index("|")
        self.assertEqual(repos_line.index("|"), pipe_idx)
        self.assertEqual(streak_line.index("|"), pipe_idx)
        self.assertIn("} |", repos_line)
        self.assertRegex(repos_line, r"Repos:\s+\.+\s+\d+")
        self.assertIn("Repos: ... 112 {Contributed: 116} |", repos_line)

    def test_loc_line_is_compacted_for_large_numbers(self):
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
        loc_line = next(strip_tags(l) for l in svg.split("\n") if "Lines of Code on GitHub" in l)
        self.assertIn("k", loc_line.lower())


if __name__ == "__main__":
    unittest.main()
