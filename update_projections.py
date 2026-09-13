#!/usr/bin/env python3
"""
Weekly polling update pipeline — Step 2 of 2.

Runs the Monte Carlo seat projection from the latest polling averages and
regenerates the seat chart and dashboard, for one jurisdiction.

Requires update_polls.py to have been run first (needs current_average.json
to be up to date).

Outputs updated (under the jurisdiction's data root):
  riding_vacancy.csv, seat_projection.json, riding_projections.csv,
  seat_projection.png, seat_history.csv
plus the dashboard at <docs_dir>/index.html and the docs/ landing page.
"""

import argparse
import sys
from datetime import date

import jurisdictions
from generate_site import main as generate_site
from mp_vacancy_scraper import main as scrape_vacancies
from plot_seats import main as plot_seats
from seat_projection import main as run_projection


def main(cfg) -> None:
    print(f"=== Projection update [{cfg.key}] — {date.today()} ===\n")

    steps: list[tuple[str, callable]] = []
    # Vacancy tracking is federal-only: it reads the House of Commons member
    # list, and there is no National Assembly equivalent wired up.
    if cfg.key == "federal":
        steps.append(
            ("Checking for vacant seats (ourcommons.ca)", scrape_vacancies)
        )
    steps += [
        (f"Running {cfg.n_simulations:,}-simulation seat projection",
         lambda: run_projection(cfg)),
        ("Plotting seat projection chart", lambda: plot_seats(cfg)),
        ("Building dashboard", lambda: generate_site(cfg)),
    ]

    for i, (label, fn) in enumerate(steps, start=1):
        print(f"\n[ {i} / {len(steps)} ]  {label} …")
        fn()

    print(f"\nDone. Dashboard updated at {cfg.docs_dir}/index.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--jurisdiction", default="federal", choices=list(jurisdictions.ALL),
        help="which election to project (default: federal)",
    )
    sys.exit(main(jurisdictions.get(ap.parse_args().jurisdiction)))
