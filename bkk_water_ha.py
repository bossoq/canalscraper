#!/usr/bin/env python3
"""Publish a Bangkok water-level station to Home Assistant: live MQTT sensor + 48h of history.

Usage:
    export MQTT_HOST=... HA_URL=http://homeassistant.local:8123 HA_TOKEN=...
    python bkk_water_ha.py 162
    python bkk_water_ha.py 162 --file page.html --dry-run   # print the payloads, send nothing

Two independent halves, each skipped when its config is absent:

  * MQTT (MQTT_HOST) - a discovery config and a state message, both retained, so
    HA creates the entity itself and recovers the value across a restart.
  * Statistics (HA_URL + HA_TOKEN) - the page's 48h as hourly long-term
    statistics, over the WebSocket API.

The split is not a preference, it is a constraint: HA stamps every MQTT state
with its arrival time, so replaying old readings onto a state topic records them
all at once and draws a flat line ending in a jump. History has to go in as
statistics instead, which land in a Statistics graph card rather than the
history graph - statistics and states are separate stores and this writes only
the former.

Requires: pip install requests beautifulsoup4 paho-mqtt websocket-client
Needs bkk_water_scraper.py in the same folder, and HA 2025.11 or newer.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import websocket
from paho.mqtt import publish

from bkk_water_scraper import BASE_URL, fetch_fresh, parse, station_name

# Thailand is UTC+7 year-round and has never run DST, so a fixed offset spares
# the slim image the tzdata install that zoneinfo would need.
TZ = timezone(timedelta(hours=7))

DISCOVERY_PREFIX = "homeassistant"
# External statistic ids are "<source>:<object_id>" - a colon, not a period, and
# the source has to match the part in front of it.
STAT_SOURCE = "canalscraper"


def hourly_stats(rows: list[dict]) -> list[dict]:
    """5-minute readings -> one mean/min/max bucket per hour, the only shape HA stores.

    Long-term statistics are hourly and every `start` must sit on the hour. The
    newest bucket is still filling when we send it; the next round re-sends the
    same hour and HA overwrites it, so partial hours correct themselves.
    """
    buckets = defaultdict(list)
    for r in rows:
        hour = datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M").replace(minute=0, tzinfo=TZ)
        buckets[hour].append(r["water_level_m_msl"])
    return [
        {"start": hour.isoformat(), "mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals)}
        for hour, vals in sorted(buckets.items())
    ]


def mqtt_messages(station_id: int, name: str, latest: dict, prefix: str, expire_after: int) -> list[dict]:
    """Discovery config + state, both retained so a restarted HA finds them waiting."""
    node = f"canalscraper_{station_id}"
    state_topic = f"{prefix}/{station_id}/state"
    config = {
        "name": "Water level",
        "unique_id": f"{node}_level",
        "object_id": f"{node}_level",
        "state_topic": state_topic,
        "value_template": "{{ value_json.level }}",
        "json_attributes_topic": state_topic,
        # Project only reading_time. Without this every key in the payload
        # becomes an attribute, and `level` would duplicate the state exactly.
        "json_attributes_template": "{{ {'reading_time': value_json.reading_time} | tojson }}",
        "unit_of_measurement": "m",
        "device_class": "distance",
        # Without state_class HA keeps no statistics for the entity at all.
        "state_class": "measurement",
        "suggested_display_precision": 2,
        # Publishing is periodic, so go unavailable rather than sit on a stale
        # value if a few rounds in a row fail.
        "expire_after": expire_after,
        "device": {
            "identifiers": [node],
            # Short and predictable: HA builds the entity id from the device
            # name plus the entity name, ignoring object_id below, and the full
            # station name here produced a 94-character entity id. The real name
            # lives in model, which is display-only.
            "name": f"Canalscraper {station_id}",
            "manufacturer": "weather.bangkok.go.th",
            "model": name,
            "configuration_url": BASE_URL.format(id=station_id),
        },
    }
    reading_time = datetime.strptime(latest["datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    state = {
        "level": latest["water_level_m_msl"],
        # HA's own "last updated" is the arrival time, which lags the reading by
        # up to a round, so the real observation time rides along beside it.
        "reading_time": reading_time.isoformat(),
    }
    return [
        {"topic": f"{DISCOVERY_PREFIX}/sensor/{node}/config", "payload": json.dumps(config), "qos": 1, "retain": True},
        {"topic": state_topic, "payload": json.dumps(state), "qos": 1, "retain": True},
    ]


def expect(ws, want: str) -> dict:
    msg = json.loads(ws.recv())
    if msg.get("type") != want:
        raise RuntimeError(f"expected {want} from Home Assistant, got {msg}")
    return msg


def import_statistics(ha_url: str, token: str, station_id: int, name: str,
                      stats: list[dict], timeout: int = 30) -> None:
    """Write the hourly buckets in as an external statistic over the WebSocket API."""
    ws_url = (ha_url.rstrip("/")
              .replace("https://", "wss://", 1)
              .replace("http://", "ws://", 1)) + "/api/websocket"
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        expect(ws, "auth_required")
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        expect(ws, "auth_ok")
        ws.send(json.dumps({
            "id": 1,
            "type": "recorder/import_statistics",
            "metadata": {
                "has_sum": False,
                # mean_type 1 = arithmetic. Replaces has_mean, which HA removes
                # in 2026.11, and which is why this needs HA 2025.11 or newer.
                "mean_type": 1,
                "name": f"{name} water level",
                "source": STAT_SOURCE,
                "statistic_id": f"{STAT_SOURCE}:station_{station_id}_level",
                "unit_of_measurement": "m",
            },
            "stats": stats,
        }))
        reply = json.loads(ws.recv())
        if not reply.get("success"):
            raise RuntimeError(f"Home Assistant rejected the import: {reply.get('error', reply)}")
    finally:
        ws.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("station_id", type=int)
    ap.add_argument("--file", help="parse a saved HTML file instead of fetching")
    ap.add_argument("--dry-run", action="store_true", help="print the payloads, send nothing")
    args = ap.parse_args()

    html = open(args.file, encoding="utf-8").read() if args.file else fetch_fresh(args.station_id)
    rows = [r for r in parse(html) if r["water_level_m_msl"] is not None]
    if not rows:
        sys.exit("No readings found in the table")
    name = station_name(html, "en")  # English: it ends up in entity ids and topics

    prefix = os.environ.get("MQTT_TOPIC_PREFIX", "canalscraper")
    # Three missed rounds before the entity goes unavailable.
    expire_after = int(os.environ.get("INTERVAL_SECONDS") or 3600) * 3
    msgs = mqtt_messages(args.station_id, name, rows[-1], prefix, expire_after)
    stats = hourly_stats(rows)

    if args.dry_run:
        print(json.dumps({"mqtt": msgs, "statistics_tail": stats[-3:]}, ensure_ascii=False, indent=2))
        print(f"{len(stats)} hourly buckets, {stats[0]['start']} .. {stats[-1]['start']} (last 3 shown)")
        return

    mqtt_host = os.environ.get("MQTT_HOST")
    if mqtt_host:
        auth = {"username": os.environ["MQTT_USER"], "password": os.environ.get("MQTT_PASS", "")} \
            if os.environ.get("MQTT_USER") else None
        publish.multiple(msgs, hostname=mqtt_host, port=int(os.environ.get("MQTT_PORT") or 1883),
                         client_id=f"canalscraper_{args.station_id}", auth=auth)
        print(f"Published {len(msgs)} MQTT messages to {mqtt_host}")
    else:
        print("MQTT_HOST unset, skipping the MQTT sensor", file=sys.stderr)

    ha_url, token = os.environ.get("HA_URL"), os.environ.get("HA_TOKEN")
    if ha_url and token:
        import_statistics(ha_url, token, args.station_id, name, stats)
        print(f"Imported {len(stats)} hourly statistics into {ha_url}")
    else:
        print("HA_URL/HA_TOKEN unset, skipping the statistics import", file=sys.stderr)


if __name__ == "__main__":
    main()
