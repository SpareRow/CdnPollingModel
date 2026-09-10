#!/usr/bin/env python3
"""Plot Canadian federal polling averages with 95% CI bands over time."""

import csv
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.dates import datestr2num

from jurisdictions import FEDERAL, Jurisdiction

PARTY_COLORS = FEDERAL.party_colors
PARTIES = list(FEDERAL.parties)


def load_rolling(path: Path, cfg: Jurisdiction = FEDERAL) -> dict:
    data = {p: {"dates": [], "means": [], "low": [], "high": []} for p in cfg.parties}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = datestr2num(row["date"])
            for p in cfg.parties:
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


def main(cfg: Jurisdiction = FEDERAL):
    output_png = cfg.p("polling_averages.png")
    data = load_rolling(cfg.p("polling_average.csv"), cfg)

    fig, ax = plt.subplots(figsize=(12, 6))

    for party in cfg.parties:
        d = data[party]
        if not d["dates"]:
            continue
        color = cfg.party_colors[party]
        ax.plot(d["dates"], d["means"], color=color, linewidth=2, label=party)
        ax.fill_between(d["dates"], d["low"], d["high"], color=color, alpha=0.15)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    fig.autofmt_xdate(rotation=45)

    ax.set_ylabel("Vote share (%)")
    ax.set_title(f"{cfg.title} — Polling Average (90-day rolling window)")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    print(f"Saved → {output_png}")


if __name__ == "__main__":
    import argparse
    import jurisdictions

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", default="federal", choices=list(jurisdictions.ALL))
    main(jurisdictions.get(ap.parse_args().jurisdiction))
