#!/usr/bin/env python3
"""Scrape a Bangkok water-level station, chart it, and post the chart to a Discord webhook.

Usage:
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    python bkk_water_discord.py 162
    python bkk_water_discord.py 162 --hours 12              # only chart the last 12 hours
    python bkk_water_discord.py 162 --file page.html --dry-run   # test locally, saves chart.png, no post

Requires: pip install requests beautifulsoup4 matplotlib
Needs bkk_water_scraper.py in the same folder.
"""
import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests

from bkk_water_scraper import BASE_URL, fetch, parse

# Dark theme to match Discord
SURFACE = "#1a1a19"
TEXT = "#ffffff"
TEXT_2 = "#c3c2b7"
GRID = "#3a3a38"
LINE = "#3987e5"

# The table pre-creates its newest row with a "-" placeholder, so a fetch can
# land in the gap before the reading is published.
STALE_ATTEMPTS = 3
STALE_WAIT = 30.0

# Spans the Change field reports, on top of the step since the previous reading
# and the charted window as a whole. Keep ascending: the scan stops at the first
# lookback the window does not cover.
LOOKBACK_HOURS = (1, 6, 24)


def station_name(html: str, lang: str = "en") -> str:
    """Pull the station name out of the page's stationNameData block."""
    m = re.search(rf'"{lang}":\s*".*?<strong>(.*?)</strong>', html)
    return m.group(1).strip() if m else "Unknown station"


def fetch_fresh(station_id: int, attempts: int = STALE_ATTEMPTS, wait: float = STALE_WAIT) -> str:
    """Fetch until the newest row carries a numeric reading.

    Gives up after `attempts` and returns the page anyway, leaving the caller to
    fall back to the newest reading that does have a value.
    """
    for attempt in range(1, attempts + 1):
        html = fetch(station_id)
        rows = parse(html)
        if rows and rows[-1]["water_level_m_msl"] is not None:
            return html
        newest = rows[-1]["datetime_th"] if rows else "no rows"
        if attempt == attempts:
            print(f"[stale] {newest} still unpublished after {attempts} tries, "
                  f"falling back to the last numeric reading", file=sys.stderr)
            return html
        print(f"[stale {attempt}/{attempts}] {newest} not published yet, "
              f"retrying in {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)


def span(delta: timedelta) -> str:
    """timedelta -> '5 min' or '24 h'."""
    minutes = delta.total_seconds() / 60
    if minutes < 60:
        return f"{minutes:.0f} min"
    return f"{round(minutes / 60, 1):g} h"


def changes(rows: list[dict], times: list[datetime]) -> list[str]:
    """Latest reading minus the previous one, each covered lookback, and the window start."""
    latest, now = rows[-1]["water_level_m_msl"], times[-1]
    earlier = [len(rows) - 2] if len(rows) > 1 else []
    for hours in LOOKBACK_HOURS:
        cutoff = now - timedelta(hours=hours)
        if cutoff < times[0]:
            break
        earlier.append(max(i for i, t in enumerate(times) if t <= cutoff))
    earlier.append(0)

    lines, seen = [], {len(rows) - 1}
    for i in earlier:  # already ordered newest-first, so spans come out ascending
        if i in seen:
            continue
        seen.add(i)
        lines.append(f"{latest - rows[i]['water_level_m_msl']:+.2f} m ({span(now - times[i])})")
    return lines


def make_chart(rows: list[dict], title: str) -> bytes:
    times = [datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M") for r in rows]
    levels = [r["water_level_m_msl"] for r in rows]

    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.plot(times, levels, color=LINE, linewidth=2, solid_capstyle="round")
    ax.fill_between(times, levels, min(levels), color=LINE, alpha=0.12, linewidth=0)

    # Label only the latest reading
    ax.scatter([times[-1]], [levels[-1]], s=40, color=LINE, zorder=3,
               edgecolors=SURFACE, linewidths=2, clip_on=False)
    ax.annotate(f"{levels[-1]:+.2f} m", (times[-1], levels[-1]),
                xytext=(-8, 10), textcoords="offset points", ha="right",
                color=TEXT, fontsize=10, fontweight="bold")

    ax.set_title(title, color=TEXT, fontsize=13, loc="left", pad=12)
    ax.set_ylabel("Water level (m above MSL)", color=TEXT_2, fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
    ax.tick_params(colors=TEXT_2, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_xlim(times[0], times[-1])

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


def post_to_discord(webhook: str, png: bytes, embed: dict, retries: int = 5) -> None:
    payload = {"embeds": [embed]}
    for attempt in range(1, retries + 1):
        resp = requests.post(
            webhook,
            data={"payload_json": json.dumps(payload)},
            files={"files[0]": ("chart.png", png, "image/png")},
            timeout=30,
        )
        if resp.status_code in (200, 204):
            return
        if resp.status_code == 429:  # Discord rate limit
            wait = float(resp.json().get("retry_after", 2))
        elif resp.status_code >= 500:
            wait = 2 ** attempt
        else:
            raise RuntimeError(f"Discord rejected the post: HTTP {resp.status_code} {resp.text}")
        print(f"[discord {attempt}/{retries}] HTTP {resp.status_code}, retrying in {wait:.1f}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError("Discord post failed after retries")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("station_id", type=int)
    ap.add_argument("--hours", type=float, help="only chart the last N hours")
    ap.add_argument("--file", help="parse a saved HTML file instead of fetching")
    ap.add_argument("--webhook", default=os.environ.get("DISCORD_WEBHOOK_URL"))
    ap.add_argument("--dry-run", action="store_true", help="save chart.png, don't post")
    args = ap.parse_args()

    html = open(args.file, encoding="utf-8").read() if args.file else fetch_fresh(args.station_id)
    rows = [r for r in parse(html) if r["water_level_m_msl"] is not None]
    if not rows:
        sys.exit("No readings found in the table")

    if args.hours:
        cutoff = datetime.strptime(rows[-1]["datetime"], "%Y-%m-%d %H:%M") - timedelta(hours=args.hours)
        rows = [r for r in rows if datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M") >= cutoff]

    name_en = station_name(html, "en")
    name_th = station_name(html, "th")
    png = make_chart(rows, name_en)  # English title: matplotlib's default font has no Thai glyphs

    levels = [r["water_level_m_msl"] for r in rows]
    latest = rows[-1]
    times = [datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M") for r in rows]

    change_value = "\n".join(changes(rows, times)) or "n/a"
    embed = {
        "title": f"💧 {name_th}",
        "description": name_en,
        "url": BASE_URL.format(id=args.station_id),
        "color": int(LINE[1:], 16),
        "fields": [
            {"name": "Latest", "value": f"**{latest['water_level_m_msl']:+.2f} m**\n{latest['datetime_th']}", "inline": True},
            {"name": "Change", "value": change_value, "inline": True},
            {"name": "Min / Max", "value": f"{min(levels):+.2f} / {max(levels):+.2f} m", "inline": True},
        ],
        "image": {"url": "attachment://chart.png"},
        "footer": {"text": f"Station {args.station_id} · {len(rows)} readings · weather.bangkok.go.th"},
    }

    if args.dry_run:
        open("chart.png", "wb").write(png)
        print("Saved chart.png\n" + json.dumps(embed, ensure_ascii=False, indent=2))
        return
    if not args.webhook:
        sys.exit("Set DISCORD_WEBHOOK_URL or pass --webhook")
    post_to_discord(args.webhook, png, embed)
    print("Posted to Discord")


if __name__ == "__main__":
    main()
