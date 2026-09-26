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

## Running locally without Docker

```sh
uv sync
uv run bkk_water_discord.py 162 --hours 12 --dry-run   # writes chart.png, posts nothing
uv run bkk_water_scraper.py 162                        # dumps readings to station_162.csv
```

## Notes

- The image is built on Python 3.12, not 3.13: `uv.lock` pins `numpy 2.0.2`, whose wheels stop at
  cp312.
- Chart titles are English on purpose — matplotlib's default font has no Thai glyphs. Thai text
  appears in the Discord embed, which Discord renders itself.
