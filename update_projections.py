#!/usr/bin/env python3
"""
Weekly polling update pipeline — Step 2 of 2.

Runs the Monte Carlo seat projection from the latest polling averages and
regenerates the riding projections HTML table and seat chart.

Requires update_polls.py to have been run first (needs current_average.json
and regional_average.json to be up to date).

Outputs updated:
  seat_projection.json, riding_projections.csv,
  riding_projections.html, seat_projection.png
"""

from datetime import date

print(f"=== Projection update — {date.today()} ===\n")

print("[ 1 / 3 ]  Running 10,000-simulation seat projection …")
from seat_projection import main as run_projection
run_projection()

print("\n[ 2 / 3 ]  Generating riding projections HTML table …")
from generate_riding_table import main as generate_table
generate_table()

print("\n[ 3 / 3 ]  Plotting seat projection chart …")
from plot_seats import main as plot_seats
plot_seats()

print("\nDone. Open riding_projections.html to view the updated table.")
