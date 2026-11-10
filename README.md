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
- Random art in the left block: text ASCII arts or colour arts made in a local studio
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
- `SVG_HEADER_IDENTITY`: custom top SVG header identity (optional, default: `USER_NAME`)

Docker scheduler variables:

- `CRON_SCHEDULE`: cron expression (default: `*/30 * * * *`)
- `CRON_LOG_MODE`: `file` or `stdout` (default: `file`)
- `CRON_LOG_FILE`: log file path when `CRON_LOG_MODE=file` (default: `/var/log/metrics-cron.log`)
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

If `CRON_LOG_MODE=file`, follow task logs inside container:

```bash
docker compose exec metrics-generator sh -lc 'tail -f /var/log/metrics-cron.log'
```

If `CRON_LOG_MODE=stdout`, task output is visible directly in `docker compose logs`.

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

## ASCII art

The left block of the card shows a random art on every run. Arts are listed in `arts/manifest.json` **in your profile repository** (the action and `run.sh` copy `arts/` from there and commit it back).

There are two kinds:

- **Text arts**: plain `.txt` files drawn as SVG text, as before. Any `arts/*.txt` still works with no manifest at all.
- **Colour arts** (`type: "render"`): an image turned into coloured ASCII, halftone dots or an LED mosaic. They are pre-rendered into `arts/gen/<id>.<theme>.<hash>.svg` (WebP inside, usually 50-200 KB) so the profile looks the same in every browser and no fonts are involved.

### Making colour arts

```bash
uv run art_studio.py           # http://127.0.0.1:8765
```

Art Studio is a small local web app:

1. Drop images (several at once), paste them or add a URL. Each one becomes a draft.
2. Pick a style and tune it. You see the dark and light versions side by side, or the whole profile card with mock data (*Full profile*). Drag on the source image to crop. Click the name to rename.
3. Press *Publish*. The image goes to `arts/src/`, the entry to `arts/manifest.json`, the rendered assets to `arts/gen/`.
4. Copy `arts/` into your profile repository and commit it (a local `./run.sh` does this for you).

Drafts stay in `.studio/` and never leave your machine. Published images do end up in a public repository, so only publish images you are allowed to share.

Some notes on how it renders:

- The art fills the whole 370x500 block (centre crop). *Fit whole* keeps the entire image instead.
- On the light theme the art is drawn like print: dark areas become dense, and it switches to a halftone because text glyphs carry almost no tone on white. *Match dark* and *Card* are there if you prefer them.
- Set how often colour arts show up with the *Colour arts in rotation* slider. It writes `"rotation": {"render_share": 0.5}` into the manifest. Without it every enabled art has the same chance, so a couple of colour arts among dozens of text ones show up rarely. The same art never shows twice in a row.

### Manifest

```json
{
  "version": 1,
  "zone": { "x": 15, "y": 15, "width": 370, "height": 500 },
  "rotation": { "render_share": 0.5 },
  "arts": [
    { "id": "41", "type": "text", "source": "arts/41.txt", "enabled": true },
    { "id": "sakura", "type": "render", "source": "arts/src/sakura.jpg", "enabled": true,
      "render": { "mode": "halftone", "cols": 60, "saturation": 1.35 } }
  ]
}
```

Common fields: `enabled` (in rotation or not), `weight` (relative chance inside its group, default 1), `scale` (size inside the block, 0.2-1.5).

`render` options (only non-defaults are stored): `mode` (`ascii`, `halftone`, `mosaic`), `cols`, `chars`, `edges`, `bold`, `saturation`, `contrast`, `brightness`, `gamma`, `levels`, `invert`, `glow`, `underlay`, `gap`, `ink_floor`, `vignette`, `bg_remove`, `crop` (`[x, y, w, h]` in 0-1), `fit` (`cover`, `contain`), `light_style` (`print`, `match`, `card`), `light_mode` (`halftone`, `same`).

The older monochrome types `image` and `engine` are still supported; see `uv run art_engine.py --help`.

### Build and preview

```bash
uv run art_engine.py build      # render enabled arts that changed, delete stale files
uv run art_engine.py check      # exit 1 if an enabled art has no up-to-date assets
uv run art_engine.py list       # id, type, enabled, built or not
uv run mock_svg_generator.py --art-id sakura   # full card with mock data -> mock_output/
```

An asset is rebuilt only when its image, its options or the renderer change (the hash in the file name covers all three). `build` skips disabled arts and removes their files, so nothing you switched off gets published. `run.sh` and the action run `build` before every update.

## Upgrading

Nothing breaks if you do nothing:

- **GitHub Action**: the next run picks up the new version, installs two new dependencies (`pillow`, `numpy`) and keeps drawing your text arts. No new inputs.
- **Docker**: rebuild the image with `docker compose up -d --build`.
- **Local**: `git pull && uv sync`.

Python 3.10 or newer is required (the code already needed it; `pyproject.toml` now says so).

To start using colour arts, run the studio, publish a few arts and commit `arts/` to your profile repository as described above.

## Customization

To customize SVG templates:

1. Copy `dark_mode.svg` and `light_mode.svg` from [xxspell/xxspell](https://github.com/xxspell/xxspell).
2. Replace names/labels with your own.
3. Put files in target repository and reference them in your profile README.

You can also copy the `arts` folder for additional ASCII options (keep `arts/manifest.json` with it; engine/image assets live in `arts/gen/`, originals in `arts/src/`).
