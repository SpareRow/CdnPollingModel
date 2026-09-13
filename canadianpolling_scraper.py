#!/usr/bin/env python3
"""
Scraper for canadianpolling.ca poll pages.

Extracts polling data and saves it to <jurisdiction>/raw_polls.csv. The
provincial pages (e.g. /QC-2022/ for the 2026 Quebec election) use identical
markup to the federal ones, so the same parser serves both — only the party
labels and output columns differ, and those come from the jurisdiction config.

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
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from jurisdictions import FEDERAL, Jurisdiction

URL = FEDERAL.national_urls[0]

PARTY_MAP = FEDERAL.party_label_map
OUTPUT_COLS = ["date", "firm", *FEDERAL.parties, "sample_size"]


def fetch_page(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
        "Connection": "keep-alive",
    }
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


def parse_polls(
    html: str,
    party_map: dict[str, str] | None = None,
    parties: tuple[str, ...] | None = None,
    others_key: str | None = None,
    others_from_residual: bool = False,
) -> list[dict]:
    """
    Parse a canadianpolling.ca page into poll rows.

    The provincial pages use identical markup to the federal ones, so the only
    per-jurisdiction differences are the party labels and the output columns.

    others_key / others_from_residual support an explicit "Others" bucket:
    labels folding into it are summed (PVQ + Others), and when the page omits
    it the residual 100 - sum(tracked) is used, so rows total 100 and the
    renormalisation in project_riding isn't silently reallocating untracked vote.
    """
    party_map = party_map if party_map is not None else PARTY_MAP
    parties = parties if parties is not None else tuple(FEDERAL.parties)

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
            party_key = party_map.get(party_raw)
            if party_key is None:
                continue  # label we don't track
            try:
                value = float(score_el.get_text(strip=True))
            except ValueError:
                continue
            if party_key == others_key:
                parties_in_poll[party_key] = parties_in_poll.get(party_key, 0.0) + value
            else:
                parties_in_poll[party_key] = value

        if not parties_in_poll:
            continue

        if others_from_residual and others_key and others_key not in parties_in_poll:
            tracked = sum(v for k, v in parties_in_poll.items() if k != others_key)
            residual = round(100.0 - tracked, 1)
            if residual > 0:
                parties_in_poll[others_key] = residual

        row_data = {"date": date_str, "firm": firm}
        row_data.update({p: parties_in_poll.get(p, "") for p in parties})
        row_data["sample_size"] = ""  # not available in page HTML
        polls.append(row_data)

    return polls


def save_csv(
    polls: list[dict],
    path: str | Path = "raw_polls.csv",
    columns: list[str] | None = None,
) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns or OUTPUT_COLS)
        writer.writeheader()
        writer.writerows(polls)
    print(f"Saved {len(polls)} polls → {path}")


def count_existing_polls(path: Path) -> int:
    """How many poll rows the committed CSV already holds (0 if absent)."""
    if not path.exists():
        return 0
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def main(cfg: Jurisdiction = FEDERAL):
    html = None
    for url in cfg.national_urls:
        print(f"Fetching {url} …")
        try:
            html = fetch_page(url)
            break
        except requests.RequestException as e:
            print(f"  failed: {e}", file=sys.stderr)
    if html is None:
        print(f"ERROR: could not fetch any of {list(cfg.national_urls)}", file=sys.stderr)
        sys.exit(1)

    others_key = "OTH" if "OTH" in cfg.parties else None
    polls = parse_polls(
        html,
        party_map=cfg.party_label_map,
        parties=cfg.parties,
        others_key=others_key,
        others_from_residual=cfg.others_from_residual,
    )
    if not polls:
        print("WARNING: no polls parsed — check HTML structure", file=sys.stderr)
        sys.exit(1)

    # The scraper overwrites the CSV wholesale, so a partial parse silently
    # destroys history. Refuse a big unexplained drop.
    output = cfg.p("raw_polls.csv")
    previous = count_existing_polls(output)
    if previous and len(polls) < previous * 0.8:
        print(
            f"ERROR: parsed only {len(polls)} polls but {output} holds {previous}. "
            "Refusing to overwrite — the page markup has probably changed.",
            file=sys.stderr,
        )
        sys.exit(1)

    columns = ["date", "firm", *cfg.parties, "sample_size"]
    output.parent.mkdir(parents=True, exist_ok=True)
    save_csv(polls, output, columns)

    dated = [p for p in polls if p["date"]]
    if dated:
        print(f"Date range: {min(p['date'] for p in dated)} → {max(p['date'] for p in dated)}")
    print(f"Firms found: {sorted(set(p['firm'] for p in polls))}")


if __name__ == "__main__":
    import argparse
    import jurisdictions

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", default="federal", choices=list(jurisdictions.ALL))
    main(jurisdictions.get(ap.parse_args().jurisdiction))
