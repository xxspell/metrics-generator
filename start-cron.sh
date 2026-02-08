#!/bin/sh
set -eu

: "${CRON_SCHEDULE:=*/30 * * * *}"
: "${CRON_LOG_MODE:=file}"
: "${CRON_LOG_FILE:=/var/log/metrics-cron.log}"

CRON_FILE="/etc/cron.d/metrics-generator"
CRON_COMMAND="cd /app && ./run.sh >> ${CRON_LOG_FILE} 2>&1"

case "$CRON_LOG_MODE" in
  stdout)
    CRON_COMMAND="cd /app && ./run.sh >> /proc/1/fd/1 2>> /proc/1/fd/2"
    ;;
  file)
    ;;
  *)
    echo "[metrics-generator] invalid CRON_LOG_MODE: ${CRON_LOG_MODE}. Use 'file' or 'stdout'."
    exit 1
    ;;
esac

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

  echo "${CRON_SCHEDULE} root ${CRON_COMMAND}"
} > "$CRON_FILE"

chmod 0644 "$CRON_FILE"

echo "[metrics-generator] cron schedule: ${CRON_SCHEDULE}"
echo "[metrics-generator] log mode: ${CRON_LOG_MODE}"
if [ "$CRON_LOG_MODE" = "file" ]; then
  echo "[metrics-generator] log file: ${CRON_LOG_FILE}"
  touch "$CRON_LOG_FILE"
fi

exec cron -f
