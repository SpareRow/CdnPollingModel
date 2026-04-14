#!/usr/bin/env python3
"""
Weekly polling update pipeline — Step 1 of 2.

Fetches the latest national and regional polls, recomputes the weighted
average, and refreshes the rolling-average chart.

Run order:
  python3 update_polls.py          ← this script
  python3 update_projections.py    ← seat projection + riding HTML table

Outputs updated:
  raw_polls.csv, regional_polls.csv, regional_average.json,
  current_average.json, polling_average.csv, polling_averages.png
"""

from datetime import date

print(f"=== Poll update — {date.today()} ===\n")

print("[ 1 / 4 ]  Scraping national polls from canadianpolling.ca …")
from canadianpolling_scraper import main as scrape_national
scrape_national()

print("\n[ 2 / 4 ]  Scraping regional polls from canadianpolling.ca …")
from regional_scraper import main as scrape_regional
scrape_regional()

print("\n[ 3 / 4 ]  Computing weighted polling average …")
from polling_model import main as run_model
run_model()

print("\n[ 4 / 4 ]  Plotting 90-day rolling average …")
from plot_averages import main as plot_averages
plot_averages()

print("\nDone. Run update_projections.py to refresh the riding-level seat projection.")
