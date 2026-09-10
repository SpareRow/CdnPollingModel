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
from jurisdictions import FEDERAL, Jurisdiction
from polling_model import (
    age_weight,
    sample_weight,
    get_pollster_rating,
    weighted_stats,
)

REGIONAL_URLS = FEDERAL.regional_urls


def scrape_region(region: str, url: str, cfg: Jurisdiction = FEDERAL) -> list[dict]:
    """Fetch and parse one regional page; returns poll dicts tagged with region."""
    print(f"  Fetching {region} — {url} …", end="", flush=True)
    try:
        html = fetch_page(url)
    except Exception as e:
        print(f" ERROR: {e}")
        return []

    polls = parse_polls(
        html,
        party_map=cfg.party_label_map,
        parties=cfg.parties,
        others_key="OTH" if "OTH" in cfg.parties else None,
        others_from_residual=cfg.others_from_residual,
    )
    for p in polls:
        p["region"] = region
    print(f" {len(polls)} polls")
    return polls


def compute_regional_average(
    polls: list[dict],
    reference: date,
    cfg: Jurisdiction = FEDERAL,
) -> dict[str, dict]:
    """Weighted average for a list of regional polls (same weights as polling_model)."""
    party_vw: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for poll in polls:
        from polling_model import parse_date
        d = parse_date(poll["date"]) if isinstance(poll["date"], str) else poll["date"]
        if d is None or d > reference:
            continue
        aw = age_weight(d, reference, cfg)
        sw = sample_weight(int(poll.get("sample_size") or cfg.default_sample_n), cfg)
        pr = get_pollster_rating(poll["firm"], cfg)
        w  = aw * sw * pr

        for p in cfg.parties:
            val = poll.get(p, "")
            if val == "" or val is None:
                continue
            try:
                party_vw[p].append((float(val), w))
            except (ValueError, TypeError):
                pass

    result = {}
    for p in cfg.parties:
        stats = weighted_stats(party_vw.get(p, []))
        if stats["mean"] is None:
            continue
        mean = stats["mean"]
        std  = stats["std"] or 0.0
        result[p] = {"mean": round(mean, 2), "std": round(std, 2)}
    return result


def main(cfg: Jurisdiction = FEDERAL) -> None:
    today = date.today()
    all_polls: list[dict] = []
    region_averages: dict[str, dict | None] = {r: None for r in cfg.regional_urls}

    print("Scraping regional pages from canadianpolling.ca:")
    for region, url in cfg.regional_urls.items():
        polls = scrape_region(region, url, cfg)
        if polls:
            all_polls.extend(polls)
            avg = compute_regional_average(polls, today, cfg)
            if avg:
                region_averages[region] = avg
                lead = ", ".join(
                    f"{p} {avg.get(p, {}).get('mean', '?')}%" for p in cfg.parties[:2]
                )
                print(f"    {region:8s}: {lead}")

    csv_cols = ["date", "firm", "region", *cfg.parties, "sample_size"]
    output_csv = cfg.p("regional_polls.csv")
    output_json = cfg.p("regional_average.json")
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_cols)
        writer.writeheader()
        for poll in all_polls:
            writer.writerow({col: poll.get(col, "") for col in csv_cols})
    print(f"\nSaved {len(all_polls)} regional polls → {output_csv}")

    # Swing groups with no page of their own get None, and fall back to the
    # national swing in seat_projection.compute_swings (federal: "North").
    output = {
        "as_of": today.isoformat(),
        "regions": {
            **{g: None for g in cfg.swing_groups if g not in region_averages},
            **region_averages,
        },
    }
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"Saved → {output_json}")


if __name__ == "__main__":
    import argparse
    import jurisdictions

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", default="federal", choices=list(jurisdictions.ALL))
    main(jurisdictions.get(ap.parse_args().jurisdiction))
