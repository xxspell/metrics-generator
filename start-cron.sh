#!/bin/sh
set -eu

: "${CRON_SCHEDULE:=*/30 * * * *}"

CRON_FILE="/etc/cron.d/metrics-generator"

{
  echo "SHELL=/bin/sh"
  echo "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

  for var in ACCESS_TOKEN TARGET_REPOSITORY USER_NAME PROXY EXCLUDED_REPOS LASTFM_TOKEN LASTFM_USER EXCLUDED_LANGUAGES GIT_USER_NAME GIT_USER_EMAIL TARGET_BRANCH; do
    val="$(printenv "$var" || true)"
    if [ -n "$val" ]; then
      escaped="$(printf '%s' "$val" | sed "s/'/'\"'\"'/g")"
      echo "$var='$escaped'"
    fi
  done

  echo "${CRON_SCHEDULE} root cd /app && ./run.sh >> /var/log/metrics-cron.log 2>&1"
} > "$CRON_FILE"

chmod 0644 "$CRON_FILE"

echo "[metrics-generator] cron schedule: ${CRON_SCHEDULE}"
touch /var/log/metrics-cron.log

exec cron -f
