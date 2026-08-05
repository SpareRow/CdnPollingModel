#!/usr/bin/env python3
"""
Weekly polling update pipeline — Step 2 of 2.

Runs the Monte Carlo seat projection from the latest polling averages and
regenerates the riding projections HTML table and seat chart.

Requires update_polls.py to have been run first (needs current_average.json
and regional_average.json to be up to date).

Outputs updated:
  riding_vacancy.csv, seat_projection.json, riding_projections.csv,
  riding_projections.html, seat_projection.png, docs/index.html
"""

from datetime import date

print(f"=== Projection update — {date.today()} ===\n")

print("[ 1 / 5 ]  Checking for vacant seats (ourcommons.ca) …")
from mp_vacancy_scraper import main as scrape_vacancies
scrape_vacancies()

print("\n[ 2 / 5 ]  Running 10,000-simulation seat projection …")
from seat_projection import main as run_projection
run_projection()

print("\n[ 3 / 5 ]  Generating riding projections HTML table …")
from generate_riding_table import main as generate_table
generate_table()

print("\n[ 4 / 5 ]  Plotting seat projection chart …")
from plot_seats import main as plot_seats
plot_seats()

print("\n[ 5 / 5 ]  Building GitHub Pages site …")
from generate_site import main as generate_site
generate_site()

print("\nDone. Site updated at docs/index.html")
