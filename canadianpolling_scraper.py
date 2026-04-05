#!/usr/bin/env python3
"""
Scraper for canadianpolling.ca/canada-2025/
Extracts federal polling data and saves to raw_polls.csv

Actual HTML structure (confirmed):
  div.pollRow
    button.pollLink
      p.pollInfo          ← firm name
      p.pollInfo.pollDate ← date string
      div.entryContainer
        div.pollEntry     ← one per party
          div.pollScore   ← numeric percentage
          div.pollParty   ← party abbreviation
"""

import csv
import re
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

URL = "https://canadianpolling.ca/canada-2025/"

PARTY_MAP = {
    "LPC": "LPC",
    "CPC": "CPC",
    "NDP": "NDP",
    "BQ": "BQ",
    "GPC": "GPC",
    "PPC": "PPC",
    "LIB": "LPC",
    "CON": "CPC",
    "GRN": "GPC",
    "BLQ": "BQ",
}

OUTPUT_COLS = ["date", "firm", "LPC", "CPC", "NDP", "BQ", "GPC", "PPC", "sample_size"]


def fetch_page(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; cdn-polling-model/1.0)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_date(raw: str) -> str:
    """Return ISO date string from formats found on the site (e.g. 'Mar 7, 2026')."""
    raw = raw.strip()
    # Normalise non-standard abbreviation "Sept" → "Sep"
    raw = re.sub(r"\bSept\b", "Sep", raw, flags=re.I)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%b. %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


def parse_polls(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    polls = []

    for row in soup.select("div.pollRow"):
        # Firm name: first .pollInfo (not .pollDate)
        info_els = row.select("p.pollInfo")
        firm = ""
        date_str = ""
        for el in info_els:
            if "pollDate" in el.get("class", []):
                date_str = parse_date(el.get_text(strip=True))
            else:
                firm = el.get_text(strip=True)

        if not firm or not date_str:
            continue

        # Party scores from .pollEntry children
        parties_in_poll = {}
        for entry in row.select("div.pollEntry"):
            score_el = entry.select_one(".pollScore")
            party_el = entry.select_one(".pollParty")
            if not score_el or not party_el:
                continue
            party_raw = party_el.get_text(strip=True).upper()
            party_key = PARTY_MAP.get(party_raw)
            if party_key is None:
                continue  # skip "Others" etc.
            try:
                parties_in_poll[party_key] = float(score_el.get_text(strip=True))
            except ValueError:
                pass

        if not parties_in_poll:
            continue

        row_data = {
            "date": date_str,
            "firm": firm,
            "LPC": parties_in_poll.get("LPC", ""),
            "CPC": parties_in_poll.get("CPC", ""),
            "NDP": parties_in_poll.get("NDP", ""),
            "BQ": parties_in_poll.get("BQ", ""),
            "GPC": parties_in_poll.get("GPC", ""),
            "PPC": parties_in_poll.get("PPC", ""),
            "sample_size": "",  # not available in page HTML
        }
        polls.append(row_data)

    return polls


def save_csv(polls: list[dict], path: str = "raw_polls.csv") -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
        writer.writeheader()
        writer.writerows(polls)
    print(f"Saved {len(polls)} polls → {path}")


def main():
    print(f"Fetching {URL} …")
    try:
        html = fetch_page(URL)
    except requests.RequestException as e:
        print(f"ERROR fetching page: {e}", file=sys.stderr)
        sys.exit(1)

    polls = parse_polls(html)
    if not polls:
        print("WARNING: no polls parsed — check HTML structure", file=sys.stderr)
        sys.exit(1)

    save_csv(polls)

    dated = [p for p in polls if p["date"]]
    if dated:
        print(f"Date range: {min(p['date'] for p in dated)} → {max(p['date'] for p in dated)}")
    print(f"Firms found: {sorted(set(p['firm'] for p in polls))}")


if __name__ == "__main__":
    main()
