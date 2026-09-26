# canalscraper

Scrapes a Bangkok water-level station from
[weather.bangkok.go.th](https://weather.bangkok.go.th/water), charts it, and posts the chart to a
Discord webhook.

## Deploy with Docker

```sh
cp .env.example .env     # then put your webhook URL in it
docker compose up -d
docker compose logs -f
```

The container posts immediately on start, then every `INTERVAL_SECONDS`.

### Configuration

All via `.env`:

| Variable               | Default        | Meaning                                                  |
| ---------------------- | -------------- | -------------------------------------------------------- |
| `DISCORD_WEBHOOK_URL`  | **required**   | Channel → Integrations → Webhooks → Copy Webhook URL     |
| `STATION_IDS`          | `162`          | Comma-separated station ids; one chart posted per station |
| `HOURS`                | `24`           | Chart only the last N hours; empty means the whole page   |
| `INTERVAL_SECONDS`     | `3600`         | Seconds between rounds                                   |
| `TZ`                   | `Asia/Bangkok` | Affects log timestamps only                              |

Changed `.env`? `docker compose up -d` again to apply it — no rebuild needed.

### Checking on it

A per-station failure (upstream 403, empty table) is logged and skipped rather than crashing the
container, so the container stays up even if every post fails. That means failures show up **only in
the logs**:

```sh
docker compose logs | grep FAILED
```

## Home Assistant

Optional, off until configured, and two separate mechanisms — because HA stamps every MQTT state
with the time it *arrived*. Replaying old readings onto a state topic would record them all at
once and draw a flat line ending in a jump, so history cannot travel over MQTT at all.

| Variable            | Enables                                                      |
| ------------------- | ------------------------------------------------------------ |
| `MQTT_HOST`         | the live sensor (plus `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS`, `MQTT_TOPIC_PREFIX`) |
| `HA_URL`+`HA_TOKEN` | the 48h backfill                                             |

**Live sensor.** A retained MQTT discovery config creates the entity by itself — no YAML — and a
retained state message carries the value, so a restarted HA finds both waiting. The reading's own
timestamp rides along as a `reading_time` attribute, since HA's "last updated" is the arrival time
and lags the reading by up to a round. `expire_after` is three rounds, so the entity goes
unavailable rather than sitting on a stale value if scrapes keep failing.

**48h of history.** Goes in as an *external statistic* (`canalscraper:station_162_level`) over HA's
WebSocket API. Statistics are hourly, so the 5-minute readings are aggregated into mean/min/max
buckets; the newest bucket is still filling when it is sent, and the next round overwrites it, so
partial hours correct themselves. Needs **HA 2025.11 or newer** — the metadata uses `mean_type`,
which replaced `has_mean`, and HA removes `has_mean` in 2026.11.

The catch worth knowing up front: statistics appear in a **Statistics graph card**, not in the
history graph or logbook. Long-term statistics and states are separate stores and this writes only
the former. The upside is that long-term statistics are never purged, so this accumulates a
permanent record well past the 48h the station page itself exposes.

Run it by hand to check a setup before deploying:

```sh
uv run bkk_water_ha.py 162 --dry-run                      # prints both payloads, sends nothing
INTERVAL_SECONDS=3600 uv run bkk_water_ha.py 162          # actually publishes
```

Pass the `INTERVAL_SECONDS` you deploy with on that second one. The discovery config is retained, so
whatever `expire_after` it computes (three rounds) is what the broker hands HA on every restart —
a hand-run without it latches the 3h default in, even if you later deploy a shorter interval.

`HOURS` does not apply here — the statistics import wants every hour the page carries, whatever the
chart is cropped to. Enabling this costs no extra requests: the container fetches each station once
per round and the Discord post and this share the page.

## Running locally without Docker

```sh
uv sync
uv run bkk_water_discord.py 162 --hours 12 --dry-run   # writes chart.png, posts nothing
uv run bkk_water_ha.py 162 --dry-run                   # prints the MQTT + statistics payloads
uv run bkk_water_scraper.py 162                        # dumps readings to station_162.csv
uv run bkk_water_scraper.py 162 --html-out page.html   # saves the page; feed it to --file above
```

## Notes

- The image is built on Python 3.12, not 3.13: `uv.lock` pins `numpy 2.0.2`, whose wheels stop at
  cp312.
- Chart titles are English on purpose — matplotlib's default font has no Thai glyphs. Thai text
  appears in the Discord embed, which Discord renders itself.
