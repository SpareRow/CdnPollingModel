#!/usr/bin/env python3
"""
Fetch the current sitting House of Commons membership from ourcommons.ca
and flag which of our tracked ridings currently have no sitting MP
(vacant seat — e.g. resignation, with no byelection held yet).

Used by seat_projection.py to discount the incumbency bonus for ridings
where there is no current officeholder to carry a personal incumbency
advantage into the next election.

Source: ourcommons.ca's members XML export (all current members, one row
per MP, with constituency name).
"""

import csv
import re
import sys
import unicodedata
from pathlib import Path

import requests

URL = "https://www.ourcommons.ca/members/en/search/xml"
RIDING_CSV = Path("riding_results_2025.csv")
OUTPUT_CSV = Path("riding_vacancy.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
}


def normalize_riding_name(name: str) -> str:
    """
    Normalize a riding name for matching between our data (which sometimes
    stores bilingual names like 'Halifax West/Halifax-Ouest') and
    ourcommons.ca's English-only constituency names.
    """
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().replace("--", " ").replace("—", " ").replace("–", " ")
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def fetch_current_constituencies() -> set[str]:
    """Return the set of normalized constituency names that currently have a sitting MP."""
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    names = re.findall(r"<ConstituencyName>(.*?)</ConstituencyName>", resp.text)
    if not names:
        raise ValueError("No ConstituencyName entries found in response")
    return {normalize_riding_name(n) for n in names}


def main() -> None:
    if not RIDING_CSV.exists():
        print(f"ERROR: {RIDING_CSV} not found. Run elections_canada_scraper.py first.",
              file=sys.stderr)
        sys.exit(1)

    with open(RIDING_CSV, newline="", encoding="utf-8") as f:
        our_ridings = [(r["riding_code"], r["riding_name"]) for r in csv.DictReader(f)]

    try:
        current_constituencies = fetch_current_constituencies()
    except Exception as e:
        print(f"WARNING: could not fetch current MP list ({e}). "
              f"Leaving {OUTPUT_CSV} unchanged.", file=sys.stderr)
        return

    rows = []
    vacant_count = 0
    for code, name in our_ridings:
        candidates = name.split("/") if "/" in name else [name]
        is_filled = any(normalize_riding_name(c) in current_constituencies for c in candidates)
        if not is_filled:
            vacant_count += 1
        rows.append({"riding_code": code, "riding_name": name, "seat_vacant": not is_filled})

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["riding_code", "riding_name", "seat_vacant"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved → {OUTPUT_CSV}  ({vacant_count} vacant seat(s) of {len(rows)} ridings)")


if __name__ == "__main__":
    main()
