# GitHub Metrics Generator
<a href="https://github.com/xxspell/xxspell">
<picture>
<source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/xxspell/xxspell/main/dark_mode.svg">
<img alt="xxspell GitHub Profile README" src="https://raw.githubusercontent.com/xxspell/xxspell/main/light_mode.svg">
</picture>
</a>

A Python tool that generates GitHub metrics (stats, languages, Last.fm integration) and commits them to a repository. Can be used as a GitHub Action or run locally.


## Features

- Generates GitHub statistics (repos, stars, commits, followers, lines of code)
- Supports language usage visualization
- Integrates with Last.fm for music stats
- Outputs SVG files for GitHub profile READMEs

## Installation

```bash
uv sync
```

## Usage

### As GitHub Action

Add to your workflow:

```yaml
- uses: xxspell/metrics-generator@v1
  with:
    target_repository: 'your-username/your-repo'
    access_token: ${{ secrets.GITHUB_TOKEN }}
    user_name: 'your-username'
    lastfm_token: ${{ secrets.LASTFM_TOKEN }}
    lastfm_user: 'your-lastfm-username'
```

See [`example-action-usage.yml`](example-action-usage.yml) for full configuration.

### Local Usage

```bash
uv run a.py
```

Set environment variables as needed (ACCESS_TOKEN, USER_NAME, etc.).

## Inputs

- `target_repository`: Repository to commit to (owner/repo)
- `access_token`: GitHub token
- `user_name`: GitHub username
- `lastfm_token`: Last.fm API token
- `lastfm_user`: Last.fm username
- `proxy`: Optional proxy
- `excluded_repos`: Comma-separated repos to exclude
- `excluded_languages`: Languages to exclude from stats
