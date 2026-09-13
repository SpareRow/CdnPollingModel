#!/usr/bin/env python3
"""
Scrape Quebec francophone / non-francophone voting-intention crosstabs.

Source: the "Polling by language" table in the Wikipedia article on the
2026 Quebec general election.

Language is the axis that matters in Quebec — the PLQ vote is overwhelmingly
non-francophone and concentrated on Montreal Island — and it is also the only
sub-provincial signal published at all: canadianpolling.ca has no Quebec
regional crosstabs. The most recent Léger poll has the PLQ at 14% among
francophones and 50% among non-francophones, so a uniform provincial swing
misallocates their vote badly.

Output deliberately uses the same filenames and shapes as the federal regional
scraper (regional_polls.csv, regional_average.json) with FR/NONFR in place of
region codes, so the existing swing machinery picks it up unchanged.

    python3 qc_language_polls_scraper.py
"""

import csv
import html as html_mod
import json
import re
import sys
from collections import defaultdict
from datetime import date

import requests

from jurisdictions import QUEBEC, Jurisdiction
from polling_model import (
    age_weight,
    get_pollster_rating,
    parse_date,
    sample_weight,
    weighted_stats,
)

WIKI_URL = "https://en.wikipedia.org/wiki/2026_Quebec_general_election"
SECTION_ID = "Polling_by_language"

# Column order in the wikitable, after date/firm/language/sample.
COLUMN_PARTIES = ["CAQ", "PLQ", "PQ", "QS", "PCQ", "OTH"]

LANGUAGE_MAP = {
    "francophone": "FR",
    "non-francophone": "NONFR",
    "nonfrancophone": "NONFR",
    "non francophone": "NONFR",
    "anglophone": "NONFR",
    "non-francophone/allophone": "NONFR",
}


def fetch_page(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.text


def _cell_text(cell: str) -> str:
    """Strip tags and references, collapse whitespace."""
    text = re.sub(r"<sup.*?</sup>", "", cell, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(html_mod.unescape(text).replace("\xa0", " ").split())


def _number(raw: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", raw.replace(",", ""))
    return float(m.group(1)) if m else None


def parse_language_table(html: str) -> list[dict]:
    """
    Parse the crosstab table into poll rows.

    The table uses rowspan for date and firm: a francophone row carries all
    columns, and the non-francophone row that follows omits date, firm and the
    reference. So a short row inherits the date and firm of the row above it.
    """
    idx = html.find(f'id="{SECTION_ID}"')
    if idx == -1:
        print(f"ERROR: no '{SECTION_ID}' section found — the article layout changed.",
              file=sys.stderr)
        return []

    table = re.search(r"<table[^>]*wikitable.*?</table>", html[idx:], re.S)
    if not table:
        print("ERROR: no wikitable in the language section.", file=sys.stderr)
        return []

    polls: list[dict] = []
    current_date = current_firm = None

    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S):
        cells = [_cell_text(c)
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) < 8 or cells[0].startswith("Date"):
            continue

        # Full-width row starts a new poll; short row continues the previous.
        if len(cells) >= 11:
            current_date = parse_date(cells[0]) or parse_date(cells[0].split("–")[-1].strip())
            current_firm = cells[1]
            lang_raw, sample, values = cells[2], cells[3], cells[4:10]
        else:
            lang_raw, sample, values = cells[0], cells[1], cells[2:8]

        group = LANGUAGE_MAP.get(lang_raw.lower())
        if group is None or current_date is None:
            continue

        pcts = {}
        for party, raw in zip(COLUMN_PARTIES, values):
            value = _number(raw)
            if value is not None:
                pcts[party] = value
        if not pcts:
            continue

        polls.append({
            "date": current_date.isoformat(),
            "firm": current_firm or "Unknown",
            "region": group,
            **{p: pcts.get(p, "") for p in QUEBEC.parties},
            "sample_size": _number(sample) and int(_number(sample)) or "",
        })

    return polls


def compute_language_averages(
    polls: list[dict], reference: date, cfg: Jurisdiction
) -> dict[str, dict]:
    """Weighted average per language group, same weighting as polling_model."""
    by_group: dict[str, list[dict]] = defaultdict(list)
    for poll in polls:
        by_group[poll["region"]].append(poll)

    result: dict[str, dict] = {}
    for group, group_polls in by_group.items():
        party_vw: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for poll in group_polls:
            d = parse_date(poll["date"])
            if d is None or d > reference:
                continue
            w = (age_weight(d, reference, cfg)
                 * sample_weight(int(poll.get("sample_size") or cfg.default_sample_n), cfg)
                 * get_pollster_rating(poll["firm"], cfg))
            for p in cfg.parties:
                val = poll.get(p, "")
                if val != "" and val is not None:
                    party_vw[p].append((float(val), w))

        stats_by_party = {}
        for p in cfg.parties:
            stats = weighted_stats(party_vw.get(p, []))
            if stats["mean"] is not None:
                stats_by_party[p] = {
                    "mean": round(stats["mean"], 2),
                    "std": round(stats["std"] or 0.0, 2),
                }
        if stats_by_party:
            result[group] = stats_by_party
    return result


def main(cfg: Jurisdiction = QUEBEC) -> None:
    print(f"Fetching {WIKI_URL} …")
    try:
        html = fetch_page(WIKI_URL)
    except requests.RequestException as e:
        print(f"ERROR fetching page: {e}", file=sys.stderr)
        sys.exit(1)

    polls = parse_language_table(html)
    print(f"  parsed {len(polls)} language crosstab rows")

    # The language axis is an enhancement, not a dependency: too few usable rows
    # and we leave the existing files alone, so seat_projection falls back to a
    # province-wide swing rather than the run failing.
    if len(polls) < 5:
        print(
            f"WARNING: only {len(polls)} usable rows — leaving existing files "
            "untouched and falling back to province-wide swing.",
            file=sys.stderr,
        )
        return

    today = date.today()
    averages = compute_language_averages(polls, today, cfg)
    for group in cfg.swing_groups:
        if group not in averages:
            print(f"WARNING: no polling for group {group}", file=sys.stderr)

    output_csv = cfg.p("regional_polls.csv")
    output_json = cfg.p("regional_average.json")
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    columns = ["date", "firm", "region", *cfg.parties, "sample_size"]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for poll in polls:
            writer.writerow({c: poll.get(c, "") for c in columns})
    print(f"Saved {len(polls)} polls → {output_csv}")

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump({"as_of": today.isoformat(), "regions": averages}, f, indent=2)
    print(f"Saved → {output_json}")

    for group, stats in averages.items():
        lead = "  ".join(
            f"{p} {stats[p]['mean']:.1f}%" for p in cfg.seat_eligible if p in stats
        )
        print(f"    {group:6s}: {lead}")


if __name__ == "__main__":
    main()
