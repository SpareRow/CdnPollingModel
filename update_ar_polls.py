#!/usr/bin/env python3
"""
Fetches the latest Angus Reid federal vote intention data from Infogram
and appends any new rows to the CSV.
"""

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

INFOGRAM_URL = "https://e.infogram.com/d47d3404-0707-4992-aca8-588453669574"
CSV_PATH = Path(__file__).parent / "angus_reid_vote_intention.csv"
HEADERS = ["Date", "CPC", "Liberal", "NDP", "BQ", "Green"]


def fetch_data():
    response = requests.get(INFOGRAM_URL, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (compatible; poll-tracker/1.0)"
    })
    response.raise_for_status()

    match = re.search(r"window\.infographicData\s*=\s*(\{.*?\});</script>", response.text, re.DOTALL)
    if not match:
        raise ValueError("Could not find infographicData in page source")

    infographic_data = json.loads(match.group(1))

    # Navigate to the chart data array
    entities = infographic_data["elements"]["content"]["content"]["entities"]
    chart_entity = next(e for e in entities.values() if e.get("type") == "RESPONSIVE_CHART")
    rows = chart_entity["data"][0]

    # Skip the header row (first row has null + party names)
    data_rows = []
    for row in rows[1:]:
        values = [cell["value"] for cell in row]
        data_rows.append(values)

    return data_rows


def load_existing_csv():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        return [tuple(row) for row in reader]


def save_csv(all_rows):
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        writer.writerows(all_rows)


def main():
    print(f"Fetching data from Infogram...")
    fetched = fetch_data()
    print(f"Found {len(fetched)} rows in source")

    existing = load_existing_csv()
    existing_set = set(map(tuple, existing))

    new_rows = [row for row in fetched if tuple(row) not in existing_set]

    if not new_rows:
        print("No new data found — CSV is already up to date.")
        return

    all_rows = existing + new_rows
    save_csv(all_rows)

    print(f"Added {len(new_rows)} new row(s):")
    for row in new_rows:
        print(f"  {', '.join(row)}")
    print(f"CSV updated: {CSV_PATH}")


if __name__ == "__main__":
    main()
