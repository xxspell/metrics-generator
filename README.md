# GitHub Metrics Generator

<a href="https://github.com/xxspell/xxspell">
<picture>
<source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/xxspell/xxspell/main/dark_mode.svg">
<img alt="xxspell GitHub Profile README" src="https://raw.githubusercontent.com/xxspell/xxspell/main/light_mode.svg">
</picture>
</a>

Python tool that generates GitHub metrics (stats, languages, Last.fm) and updates SVG files in a target repository.

## Features

- GitHub stats generation (repos, stars, commits, followers, lines of code)
- Language usage visualization
- Last.fm integration
- Two run modes: GitHub Actions or Docker Compose scheduler

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- For Docker mode: Docker + Docker Compose

## Configuration

Create `.env` from template:

```bash
cp .env.example .env
```

Main variables:

- `ACCESS_TOKEN`: GitHub token with push access to target repository
- `TARGET_REPOSITORY`: repository to update (`owner/repo`)
- `USER_NAME`: GitHub username for metrics
- `TARGET_BRANCH`: target branch (default: `main`)
- `LASTFM_TOKEN`: Last.fm API token (optional)
- `LASTFM_USER`: Last.fm username (optional)
- `EXCLUDED_REPOS`: comma-separated repos to exclude (optional)
- `EXCLUDED_LANGUAGES`: comma-separated languages to exclude (optional)
- `PROXY`: proxy URL (optional)

Docker scheduler variables:

- `CRON_SCHEDULE`: cron expression (default: `*/30 * * * *`)
- `TZ`: timezone for cron (default: `UTC`)

## Run with GitHub Actions

Use reusable action in workflow:

```yaml
- name: Generate and commit metrics
  uses: xxspell/metrics-generator/action.yml@main
  with:
    target_repository: your-username/repo
    target_branch: main
    access_token: ${{ secrets.METRICS_ACCESS_TOKEN }}
    user_name: ${{ secrets.METRICS_USER_NAME }}
    proxy: ${{ secrets.METRICS_PROXY }}
    excluded_repos: ${{ secrets.METRICS_EXCLUDED_REPOS }}
    git_user_name: ${{ secrets.METRICS_GIT_USER_NAME }}
    git_user_email: ${{ secrets.METRICS_GIT_USER_EMAIL }}
    lastfm_token: ${{ secrets.LASTFM_TOKEN }}
    last_fm_user: ${{ secrets.LAST_FM_USER }}
    excluded_languages: php,html,css
```

Full example: `example-action-usage.yml`

## Run with Docker Compose (self-hosted scheduler)

Start background scheduler:

```bash
docker compose up -d --build
```

View logs:

```bash
docker compose logs -f metrics-generator
```

Stop scheduler:

```bash
docker compose down
```

Manual one-time run:

```bash
docker compose run --rm metrics-generator ./run.sh
```

Schedule examples (`CRON_SCHEDULE`):

- Every 30 minutes: `*/30 * * * *`
- Every day at 06:00: `0 6 * * *`
- Every day at 06:00 and 18:00: `0 6,18 * * *`

How scheduler works:

1. Container runs built-in cron via `start-cron.sh`.
2. Cron launches `run.sh` by `CRON_SCHEDULE`.
3. `run.sh` clones target repository and generates metrics (`uv run a.py`).
4. Updated files are committed and pushed when there are changes.
5. If previous run is still active, next run is skipped (lock protection).

No host cron/system timer is required.

## Local development run

Install dependencies:

```bash
uv sync
```

Run generator only:

```bash
uv run a.py
```

## Customization

To customize SVG templates:

1. Copy `dark_mode.svg` and `light_mode.svg` from [xxspell/xxspell](https://github.com/xxspell/xxspell).
2. Replace names/labels with your own.
3. Put files in target repository and reference them in your profile README.

You can also copy the `arts` folder for additional ASCII options.
