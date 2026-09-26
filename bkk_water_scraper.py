#!/usr/bin/env python3
"""Scrape the water-level table from weather.bangkok.go.th StationDetail pages.

Usage:
    python bkk_water_scraper.py 162                 # fetch station 162 -> station_162.csv
    python bkk_water_scraper.py 162 -o out.csv
    python bkk_water_scraper.py 162 --file page.html  # parse a saved page instead of fetching

Requires: pip install requests beautifulsoup4
"""
import argparse
import csv
import random
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://weather.bangkok.go.th/water/StationDetail?id={id}"
RETRY_STATUS = {403, 408, 429, 500, 502, 503, 504}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
}


def fetch(station_id: int, retries: int = 5, backoff: float = 2.0, timeout: int = 30) -> str:
    """GET the page, retrying on 403/429/5xx and network errors with exponential backoff + jitter."""
    url = BASE_URL.format(id=station_id)
    session = requests.Session()
    session.headers.update(HEADERS)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                return resp.text
            last_err = f"HTTP {resp.status_code}"
            if resp.status_code not in RETRY_STATUS:
                break
            # Honour Retry-After if the server sends one
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else None
        except requests.RequestException as e:
            last_err = repr(e)
            wait = None
        if attempt == retries:
            break
        wait = wait if wait is not None else backoff ** attempt + random.uniform(0, 1)
        print(f"[attempt {attempt}/{retries}] {last_err} - retrying in {wait:.1f}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts: {last_err}")


def buddhist_to_iso(s: str) -> str:
    """'24/09/2569 21:05' (Buddhist Era) -> '2026-09-24 21:05'."""
    d, t = s.split()
    day, month, year = map(int, d.split("/"))
    return datetime(year - 543, month, day, *map(int, t.split(":"))).strftime("%Y-%m-%d %H:%M")


def parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="example")
    if table is None:
        raise ValueError("Table #example not found - page layout may have changed or you got a block page")
    body = table.find("tbody") or table
    rows = []
    for tr in body.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue
        seq, dt, level = cells[:3]
        rows.append({
            "seq": int(seq),
            "datetime_th": dt,
            "datetime": buddhist_to_iso(dt),
            "water_level_m_msl": float(level) if level not in ("", "-") else None,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("station_id", type=int)
    ap.add_argument("-o", "--output")
    ap.add_argument("--file", help="parse a saved HTML file instead of fetching")
    ap.add_argument("--retries", type=int, default=5)
    args = ap.parse_args()

    html = open(args.file, encoding="utf-8").read() if args.file else fetch(args.station_id, args.retries)
    rows = parse(html)
    out = args.output or f"station_{args.station_id}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel shows Thai correctly
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
