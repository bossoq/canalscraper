#!/bin/sh
# Post a chart per station to Discord, then sleep. Repeat forever.
set -u

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"; }
terminate() { log "shutting down"; exit 0; }
trap terminate TERM INT

: "${STATION_IDS:=162}"
: "${INTERVAL_SECONDS:=3600}"
HOURS="${HOURS:-}"

log "starting: stations=$STATION_IDS hours=${HOURS:-all} interval=${INTERVAL_SECONDS}s"

while true; do
    for id in $(echo "$STATION_IDS" | tr ',' ' '); do
        log "posting station $id"
        # No set -e and a || here on purpose: a transient upstream 403 or an
        # empty table must not kill the container, just skip to the next round.
        if [ -n "$HOURS" ]; then
            python /app/bkk_water_discord.py "$id" --hours "$HOURS" || log "station $id FAILED"
        else
            python /app/bkk_water_discord.py "$id" || log "station $id FAILED"
        fi
    done
    log "sleeping ${INTERVAL_SECONDS}s"
    # Backgrounded sleep + wait, so the trap above can fire: a blocking sleep as
    # PID 1 ignores SIGTERM and `compose down` would stall until the SIGKILL.
    sleep "$INTERVAL_SECONDS" &
    wait $!
done
