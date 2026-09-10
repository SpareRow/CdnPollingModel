#!/usr/bin/env python3
"""
Weekly polling update pipeline — Step 1 of 2.

Fetches the latest polls, recomputes the weighted average, and refreshes the
rolling-average chart, for one jurisdiction.

Run order:
  python3 update_polls.py [--jurisdiction federal|qc]   ← this script
  python3 update_projections.py [--jurisdiction ...]    ← seat projection

Outputs updated (under the jurisdiction's data root):
  raw_polls.csv, regional_polls.csv, regional_average.json,
  current_average.json, polling_average.csv, polling_averages.png

Jurisdictions differ in which steps apply: the federal race has regional
crosstabs published per province-group, while Quebec has none — its
sub-provincial signal is the francophone/non-francophone split instead.
"""

import argparse
import sys
from datetime import date

import jurisdictions
from canadianpolling_scraper import main as scrape_national
from plot_averages import main as plot_averages
from polling_model import main as run_model
from qc_language_polls_scraper import main as scrape_language
from regional_scraper import main as scrape_regional


def main(cfg) -> None:
    print(f"=== Poll update [{cfg.key}] — {date.today()} ===\n")

    steps: list[tuple[str, callable]] = [
        ("Scraping polls from canadianpolling.ca", lambda: scrape_national(cfg)),
    ]
    # Where the sub-provincial signal comes from differs by jurisdiction:
    # the federal race has regional crosstabs on canadianpolling.ca, Quebec has
    # francophone/non-francophone ones on Wikipedia and no regional ones at all.
    if cfg.regional_urls:
        steps.append(
            ("Scraping regional polls from canadianpolling.ca",
             lambda: scrape_regional(cfg))
        )
    elif cfg.swing_axis == "language":
        steps.append(
            ("Scraping language crosstabs from Wikipedia",
             lambda: scrape_language(cfg))
        )
    steps += [
        ("Computing weighted polling average", lambda: run_model(cfg)),
        ("Plotting 90-day rolling average", lambda: plot_averages(cfg)),
    ]

    for i, (label, fn) in enumerate(steps, start=1):
        print(f"\n[ {i} / {len(steps)} ]  {label} …")
        fn()

    print("\nDone. Run update_projections.py to refresh the seat projection.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--jurisdiction", default="federal", choices=list(jurisdictions.ALL),
        help="which election to update (default: federal)",
    )
    sys.exit(main(jurisdictions.get(ap.parse_args().jurisdiction)))
