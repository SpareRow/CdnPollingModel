#!/usr/bin/env python3
"""Plot Canadian federal polling averages with 95% CI bands over time."""

import csv
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.dates import datestr2num

INPUT_CSV = Path("polling_average.csv")
OUTPUT_PNG = Path("polling_averages.png")

PARTY_COLORS = {
    "LPC": "#D71920",   # Liberal red
    "CPC": "#1A4782",   # Conservative blue
    "NDP": "#F4831F",   # NDP orange
    "BQ":  "#00A0C6",   # Bloc teal
    "GPC": "#3D9B35",   # Green
    "PPC": "#4B0082",   # PPC purple
}

PARTIES = ["LPC", "CPC", "NDP", "BQ", "GPC", "PPC"]


def load_rolling(path: Path) -> dict:
    data = {p: {"dates": [], "means": [], "low": [], "high": []} for p in PARTIES}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = datestr2num(row["date"])
            for p in PARTIES:
                mean_s = row.get(f"{p}_mean", "")
                std_s  = row.get(f"{p}_std",  "")
                if mean_s == "" or std_s == "":
                    continue
                mean = float(mean_s)
                std  = float(std_s)
                data[p]["dates"].append(d)
                data[p]["means"].append(mean)
                data[p]["low"].append(mean - 1.96 * std)
                data[p]["high"].append(mean + 1.96 * std)
    return data


def main():
    data = load_rolling(INPUT_CSV)

    fig, ax = plt.subplots(figsize=(12, 6))

    for party in PARTIES:
        d = data[party]
        if not d["dates"]:
            continue
        color = PARTY_COLORS[party]
        ax.plot(d["dates"], d["means"], color=color, linewidth=2, label=party)
        ax.fill_between(d["dates"], d["low"], d["high"], color=color, alpha=0.15)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    fig.autofmt_xdate(rotation=45)

    ax.set_ylabel("Vote share (%)")
    ax.set_title("Canadian Federal Polling Average (90-day rolling window)")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150)
    print(f"Saved → {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
