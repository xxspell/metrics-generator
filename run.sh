#!/bin/bash
set -euo pipefail

LOCK_DIR="/tmp/metrics-generator.lock"
TARGET_DIR="target-repo-temp"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another metrics run is already in progress, skipping."
  exit 0
fi

cleanup() {
  rm -rf "$TARGET_DIR"
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

trap cleanup EXIT

# Clone target repository
if [ -z "$TARGET_REPOSITORY" ]; then
  echo "TARGET_REPOSITORY not set"
  exit 1
fi

rm -rf "$TARGET_DIR"
git clone --depth 1 "https://x-access-token:${ACCESS_TOKEN}@github.com/${TARGET_REPOSITORY}.git" "$TARGET_DIR"

# Copy required files from target repository
mkdir -p cache arts
if [ -d "$TARGET_DIR/cache" ]; then
  cp -r "$TARGET_DIR/cache/." cache/ || echo "No cache directory found"
fi
if [ -d "$TARGET_DIR/arts" ]; then
  cp -r "$TARGET_DIR/arts/." arts/ || echo "No arts directory found"
fi
if [ -f "$TARGET_DIR/dark_mode.svg" ]; then
  cp "$TARGET_DIR/dark_mode.svg" . || echo "No dark_mode.svg found"
fi
if [ -f "$TARGET_DIR/light_mode.svg" ]; then
  cp "$TARGET_DIR/light_mode.svg" . || echo "No light_mode.svg found"
fi

# Build ASCII art assets from manifest (no-op when all fresh)
uv run art_engine.py build

# Run the metrics generator
uv run a.py

# Copy updated files back to target repository
mkdir -p "$TARGET_DIR/cache" "$TARGET_DIR/arts"
if [ -d "cache" ]; then
  cp -r cache/. "$TARGET_DIR/cache/" || echo "No cache directory to copy back"
fi
if [ -d "arts" ]; then
  # arts/gen is fully owned by `art_engine.py build` (it started from the target's
  # copy and garbage-collected stale assets), so mirror it to propagate deletions.
  if [ -d "arts/gen" ]; then
    rm -rf "$TARGET_DIR/arts/gen"
  fi
  cp -r arts/. "$TARGET_DIR/arts/" || echo "No arts directory to copy back"
fi
if [ -f "dark_mode.svg" ]; then
  cp dark_mode.svg "$TARGET_DIR" || echo "No dark_mode.svg to copy back"
fi
if [ -f "light_mode.svg" ]; then
  cp light_mode.svg "$TARGET_DIR" || echo "No light_mode.svg to copy back"
fi

# Commit and push changes
cd "$TARGET_DIR"
# --porcelain also sees untracked files (new arts/gen assets), unlike `git diff`
if [ -z "$(git status --porcelain)" ]; then
  echo "No changes to commit"
else
  # Configure git
  git config user.name "${GIT_USER_NAME:-GitHub Action}"
  git config user.email "${GIT_USER_EMAIL:-action@github.com}"

  # Add and commit changes
  git add -A
  git commit -m "Update GitHub metrics [$(date)]"
  git push origin "${TARGET_BRANCH:-main}"
fi

cd ..
