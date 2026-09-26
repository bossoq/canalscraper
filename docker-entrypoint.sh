#!/bin/sh
# Fetch each station once, post a chart to Discord, publish to Home Assistant
# if it is configured, then sleep. Repeat forever.
set -u

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"; }
terminate() { log "shutting down"; exit 0; }
trap terminate TERM INT

: "${STATION_IDS:=162}"
: "${INTERVAL_SECONDS:=3600}"
HOURS="${HOURS:-}"

log "starting: stations=$STATION_IDS hours=${HOURS:-all} interval=${INTERVAL_SECONDS}s"

# One fetch per station per round, shared by every consumer below. /tmp is the
# only writable directory for the unprivileged user the image runs as.
PAGE=/tmp/station.html

while true; do
    for id in $(echo "$STATION_IDS" | tr ',' ' '); do
        # No set -e and a || here on purpose: a transient upstream 403 or an
        # empty table must not kill the container, just skip to the next round.
        if ! python /app/bkk_water_scraper.py "$id" --html-out "$PAGE" >/dev/null; then
            log "station $id FETCH FAILED"
            continue
        fi

        log "posting station $id"
        if [ -n "$HOURS" ]; then
            python /app/bkk_water_discord.py "$id" --file "$PAGE" --hours "$HOURS" || log "station $id FAILED"
        else
            python /app/bkk_water_discord.py "$id" --file "$PAGE" || log "station $id FAILED"
        fi

        # Home Assistant, only once configured. HOURS does not apply: the
        # statistics import wants every hour the page carries.
        if [ -n "${MQTT_HOST:-}${HA_URL:-}" ]; then
            log "publishing station $id to Home Assistant"
            python /app/bkk_water_ha.py "$id" --file "$PAGE" || log "station $id HA FAILED"
        fi

        rm -f "$PAGE"
    done
    log "sleeping ${INTERVAL_SECONDS}s"
    # Backgrounded sleep + wait, so the trap above can fire: a blocking sleep as
    # PID 1 ignores SIGTERM and `compose down` would stall until the SIGKILL.
    sleep "$INTERVAL_SECONDS" &
    wait $!
done
