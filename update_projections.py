#!/usr/bin/env python3
"""
Weekly polling update pipeline — Step 2 of 2.

Runs the Monte Carlo seat projection from the latest polling averages and
regenerates the riding projections HTML table and seat chart.

Requires update_polls.py to have been run first (needs current_average.json
and regional_average.json to be up to date).

Outputs updated:
  seat_projection.json, riding_projections.csv,
  riding_projections.html, seat_projection.png, docs/index.html
"""

from datetime import date

print(f"=== Projection update — {date.today()} ===\n")

print("[ 1 / 4 ]  Running 10,000-simulation seat projection …")
from seat_projection import main as run_projection
run_projection()

print("\n[ 2 / 4 ]  Generating riding projections HTML table …")
from generate_riding_table import main as generate_table
generate_table()

print("\n[ 3 / 4 ]  Plotting seat projection chart …")
from plot_seats import main as plot_seats
plot_seats()

print("\n[ 4 / 4 ]  Building GitHub Pages site …")
from generate_site import main as generate_site
generate_site()

print("\nDone. Site updated at docs/index.html")
