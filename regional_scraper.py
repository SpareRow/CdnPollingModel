#!/usr/bin/env python3
"""
Scrape regional federal polling pages from canadianpolling.ca and produce
regional_average.json using the same weighting as polling_model.py.

Replaces wikipedia_scraper.py as the source of regional polling data.
Reuses parse_polls() and fetch_page() from canadianpolling_scraper.py.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from canadianpolling_scraper import fetch_page, parse_polls
from polling_model import (
    PARTIES,
    age_weight,
    sample_weight,
    get_pollster_rating,
    weighted_stats,
)

REGIONAL_URLS: dict[str, str] = {
    "ON":      "https://canadianpolling.ca/Canada-ON-2025/",
    "QC":      "https://canadianpolling.ca/Canada-QC-2025/",
    "BC":      "https://canadianpolling.ca/Canada-BC-2025/",
    "AB":      "https://canadianpolling.ca/Canada-AB-2025/",
    "MB_SK":   "https://canadianpolling.ca/Canada-SKMB-2025",
    "Atlantic": "https://canadianpolling.ca/Canada-ATL-2025",
}

OUTPUT_JSON = Path("regional_average.json")
OUTPUT_CSV  = Path("regional_polls.csv")
CSV_COLS    = ["date", "firm", "region", "LPC", "CPC", "NDP", "BQ", "GPC", "PPC", "sample_size"]


def scrape_region(region: str, url: str) -> list[dict]:
    """Fetch and parse one regional page; returns poll dicts tagged with region."""
    print(f"  Fetching {region} — {url} …", end="", flush=True)
    try:
        html = fetch_page(url)
    except Exception as e:
        print(f" ERROR: {e}")
        return []

    polls = parse_polls(html)
    for p in polls:
        p["region"] = region
    print(f" {len(polls)} polls")
    return polls


def compute_regional_average(
    polls: list[dict],
    reference: date,
) -> dict[str, dict]:
    """Weighted average for a list of regional polls (same weights as polling_model)."""
    party_vw: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for poll in polls:
        from polling_model import parse_date
        d = parse_date(poll["date"]) if isinstance(poll["date"], str) else poll["date"]
        if d is None or d > reference:
            continue
        aw = age_weight(d, reference)
        sw = sample_weight(int(poll.get("sample_size") or 1000))
        pr = get_pollster_rating(poll["firm"])
        w  = aw * sw * pr

        for p in PARTIES:
            val = poll.get(p, "")
            if val == "" or val is None:
                continue
            try:
                party_vw[p].append((float(val), w))
            except (ValueError, TypeError):
                pass

    result = {}
    for p in PARTIES:
        stats = weighted_stats(party_vw.get(p, []))
        if stats["mean"] is None:
            continue
        mean = stats["mean"]
        std  = stats["std"] or 0.0
        result[p] = {"mean": round(mean, 2), "std": round(std, 2)}
    return result


def main() -> None:
    today = date.today()
    all_polls: list[dict] = []
    region_averages: dict[str, dict | None] = {r: None for r in REGIONAL_URLS}

    print("Scraping regional pages from canadianpolling.ca:")
    for region, url in REGIONAL_URLS.items():
        polls = scrape_region(region, url)
        if polls:
            all_polls.extend(polls)
            avg = compute_regional_average(polls, today)
            if avg:
                region_averages[region] = avg
                lpc = avg.get("LPC", {}).get("mean", "?")
                cpc = avg.get("CPC", {}).get("mean", "?")
                print(f"    {region:8s}: LPC {lpc}%  CPC {cpc}%")

    # Save regional_polls.csv
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLS)
        writer.writeheader()
        for poll in all_polls:
            writer.writerow({col: poll.get(col, "") for col in CSV_COLS})
    print(f"\nSaved {len(all_polls)} regional polls → {OUTPUT_CSV}")

    # Save regional_average.json
    output = {
        "as_of": today.isoformat(),
        "regions": {
            **region_averages,
            "North": None,   # no regional polling available
        },
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"Saved → {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
