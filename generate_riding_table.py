#!/usr/bin/env python3
"""
Generate riding_projections.html — a sortable, searchable riding-by-riding
projection table from riding_projections.csv and seat_projection.json.
"""

import csv
import json
from pathlib import Path

RIDING_CSV    = Path("riding_projections.csv")
SEAT_JSON     = Path("seat_projection.json")
OUTPUT_HTML   = Path("riding_projections.html")

PARTIES = ["LPC", "CPC", "NDP", "BQ", "GPC", "PPC"]

PARTY_COLORS = {
    "LPC": "#D71920",
    "CPC": "#1A4782",
    "NDP": "#F4831F",
    "BQ":  "#00A0C6",
    "GPC": "#3D9B35",
    "PPC": "#4B0082",
}
PARTY_TEXT_COLORS = {
    "LPC": "#fff", "CPC": "#fff", "NDP": "#fff",
    "BQ":  "#fff", "GPC": "#fff", "PPC": "#fff",
}
PROVINCE_ORDER = [
    "Newfoundland and Labrador", "Prince Edward Island", "Nova Scotia",
    "New Brunswick", "Quebec", "Ontario", "Manitoba", "Saskatchewan",
    "Alberta", "British Columbia", "Northwest Territories", "Nunavut", "Yukon",
]
PROVINCE_SHORT = {
    "Newfoundland and Labrador": "NL", "Prince Edward Island": "PE",
    "Nova Scotia": "NS", "New Brunswick": "NB", "Quebec": "QC",
    "Ontario": "ON", "Manitoba": "MB", "Saskatchewan": "SK",
    "Alberta": "AB", "British Columbia": "BC",
    "Northwest Territories": "NT", "Nunavut": "NU", "Yukon": "YT",
}


def tossup_class(top_prob: float) -> str:
    if top_prob < 0.60:
        return "tossup"
    if top_prob < 0.80:
        return "likely"
    return "safe"


def tossup_label(top_prob: float) -> str:
    if top_prob < 0.60:
        return "Toss-up"
    if top_prob < 0.80:
        return "Likely"
    return "Safe"


def prob_bar_html(party: str, prob: float) -> str:
    if prob < 0.005:
        return ""
    pct = round(prob * 100, 1)
    color = PARTY_COLORS.get(party, "#aaa")
    return (
        f'<div class="prob-cell">'
        f'<div class="bar-wrap">'
        f'<div class="bar" style="width:{min(pct, 100):.1f}%;background:{color}"></div>'
        f'</div>'
        f'<span class="prob-label">{pct:.0f}%</span>'
        f'</div>'
    )


def build_html(ridings: list[dict], seat_data: dict) -> str:
    as_of   = seat_data.get("as_of", "")
    n_sims  = seat_data.get("simulations", 10_000)
    parties = seat_data.get("parties", {})

    # Summary banner
    summary_cells = ""
    for p in sorted(parties, key=lambda x: -parties[x]["mean_seats"]):
        s = parties[p]
        color = PARTY_COLORS.get(p, "#888")
        summary_cells += (
            f'<div class="summary-party">'
            f'<div class="summary-badge" style="background:{color}">{p}</div>'
            f'<div class="summary-seats">{s["mean_seats"]:.0f}</div>'
            f'<div class="summary-ci">[{s["low95"]}–{s["high95"]}]</div>'
            f'</div>'
        )

    # Group ridings by province
    by_province: dict[str, list[dict]] = {p: [] for p in PROVINCE_ORDER}
    for r in ridings:
        prov = r["province"]
        if prov in by_province:
            by_province[prov].append(r)

    # Build table rows
    table_rows = ""
    for prov in PROVINCE_ORDER:
        prov_ridings = by_province.get(prov, [])
        if not prov_ridings:
            continue
        prov_ridings.sort(key=lambda r: r["riding_name"])
        short = PROVINCE_SHORT.get(prov, prov[:2])

        # Province header row
        # Count seats per party in this province
        prov_seats: dict[str, int] = {}
        for r in prov_ridings:
            w = r["projected_winner"]
            prov_seats[w] = prov_seats.get(w, 0) + 1
        seat_summary = "  ".join(
            f'<span class="pseat" style="color:{PARTY_COLORS.get(p,"#888")}">'
            f'{p} {n}</span>'
            for p, n in sorted(prov_seats.items(), key=lambda x: -x[1])
        )
        table_rows += (
            f'<tr class="province-header" data-province="{prov}">'
            f'<td colspan="10">'
            f'<span class="prov-name">{prov}</span>'
            f'<span class="prov-seats">{seat_summary}</span>'
            f'</td></tr>\n'
        )

        for r in prov_ridings:
            winner = r["projected_winner"]
            probs  = {p: float(r.get(f"P_{p}", 0)) for p in PARTIES}
            top_p  = probs.get(winner, 0)
            tc     = tossup_class(top_p)
            tl     = tossup_label(top_p)
            w_color = PARTY_COLORS.get(winner, "#888")

            badge = (
                f'<span class="winner-badge" '
                f'style="background:{w_color};color:{PARTY_TEXT_COLORS.get(winner,"#fff")}">'
                f'{winner}</span>'
            )
            comp_cls = f'comp-{tc}'
            comp_span = f'<span class="comp-label {comp_cls}">{tl}</span>'

            row_data_attrs = (
                f'data-riding="{r["riding_name"].lower()}" '
                f'data-province="{prov.lower()}" '
                f'data-winner="{winner}" '
                f'data-top="{top_p:.3f}"'
            )

            # Only show parties with non-zero probability
            party_cells = ""
            for p in PARTIES:
                prob = probs.get(p, 0)
                party_cells += f'<td class="prob-td">{prob_bar_html(p, prob)}</td>\n'

            table_rows += (
                f'<tr class="riding-row" {row_data_attrs}>\n'
                f'  <td class="riding-name">{r["riding_name"]}</td>\n'
                f'  <td class="prov-code">{short}</td>\n'
                f'  <td class="winner-td">{badge}</td>\n'
                f'  <td class="comp-td">{comp_span}</td>\n'
                + party_cells +
                f'</tr>\n'
            )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Riding Projections — 46th Canadian Federal Election</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         font-size: 13px; background: #f5f6f8; color: #222; }}
  .page {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px; }}

  /* ── Header ── */
  h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }}
  .subtitle {{ color: #555; font-size: 0.85rem; margin-bottom: 20px; }}

  /* ── Summary banner ── */
  .summary {{ display: flex; gap: 16px; flex-wrap: wrap;
              background: #fff; border-radius: 8px;
              padding: 16px 20px; margin-bottom: 20px;
              box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .summary-party {{ text-align: center; min-width: 64px; }}
  .summary-badge {{ display: inline-block; border-radius: 4px; padding: 2px 8px;
                    font-weight: 700; font-size: 0.85rem; color: #fff; }}
  .summary-seats {{ font-size: 1.6rem; font-weight: 700; line-height: 1.2; margin-top: 4px; }}
  .summary-ci {{ font-size: 0.72rem; color: #777; }}
  .majority-note {{ align-self: center; color: #555; font-size: 0.8rem;
                    border-left: 2px solid #ddd; padding-left: 12px; margin-left: auto; }}

  /* ── Controls ── */
  .controls {{ display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }}
  .controls input {{ padding: 6px 12px; border: 1px solid #ccc; border-radius: 6px;
                     font-size: 13px; width: 220px; }}
  .controls select {{ padding: 6px 10px; border: 1px solid #ccc; border-radius: 6px;
                      font-size: 13px; background: #fff; }}
  .controls label {{ align-self: center; color: #555; font-size: 0.8rem; }}

  /* ── Table ── */
  .table-wrap {{ background: #fff; border-radius: 8px; overflow: hidden;
                 box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  table {{ width: 100%; border-collapse: collapse; }}
  thead th {{ background: #1e2432; color: #fff; padding: 8px 10px;
              text-align: left; font-size: 0.78rem; font-weight: 600;
              letter-spacing: .03em; white-space: nowrap; cursor: pointer;
              user-select: none; }}
  thead th:hover {{ background: #2d3450; }}
  thead th.sorted-asc::after  {{ content: " ↑"; opacity: .7; }}
  thead th.sorted-desc::after {{ content: " ↓"; opacity: .7; }}

  tbody tr {{ border-bottom: 1px solid #eef0f3; }}
  tbody tr:last-child {{ border-bottom: none; }}
  tbody tr.riding-row:hover {{ background: #f0f4ff; }}

  /* Province header rows */
  tr.province-header td {{ background: #f0f2f6; padding: 6px 10px;
                           font-weight: 600; font-size: 0.8rem; }}
  .prov-name {{ margin-right: 14px; }}
  .pseat {{ font-size: 0.75rem; font-weight: 700; margin-right: 8px; }}

  td {{ padding: 6px 10px; vertical-align: middle; }}
  .riding-name {{ font-weight: 500; min-width: 180px; }}
  .prov-code {{ color: #777; font-size: 0.75rem; font-weight: 600;
                text-align: center; white-space: nowrap; }}

  /* Winner badge */
  .winner-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
                   font-weight: 700; font-size: 0.8rem; }}

  /* Competitiveness */
  .comp-label {{ font-size: 0.72rem; padding: 1px 6px; border-radius: 10px;
                 font-weight: 600; white-space: nowrap; }}
  .comp-safe   {{ background: #e8f5e9; color: #2e7d32; }}
  .comp-likely {{ background: #fff8e1; color: #f57f17; }}
  .comp-tossup {{ background: #fce4ec; color: #c62828; }}

  /* Probability bars */
  .prob-td {{ min-width: 80px; padding: 4px 8px; }}
  .prob-cell {{ display: flex; align-items: center; gap: 5px; }}
  .bar-wrap {{ flex: 1; height: 8px; background: #eee; border-radius: 4px;
               overflow: hidden; min-width: 40px; }}
  .bar {{ height: 100%; border-radius: 4px; }}
  .prob-label {{ font-size: 0.72rem; color: #444; width: 28px; text-align: right;
                 flex-shrink: 0; }}

  /* Hidden rows */
  tr.hidden {{ display: none; }}

  /* Result count */
  .result-count {{ font-size: 0.8rem; color: #777; margin-bottom: 6px; }}

  @media (max-width: 700px) {{
    .prob-td {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="page">

  <h1>Riding Projections — 46th Canadian Federal Election</h1>
  <p class="subtitle">
    Based on weighted polling average as of {as_of} &nbsp;·&nbsp;
    {n_sims:,} Monte Carlo simulations &nbsp;·&nbsp;
    Win probability shown per party
  </p>

  <!-- Seat summary -->
  <div class="summary">
    {summary_cells}
    <div class="majority-note">Majority: 172 seats</div>
  </div>

  <!-- Controls -->
  <div class="controls">
    <input type="text" id="search" placeholder="Search riding…" oninput="filterTable()">
    <select id="prov-filter" onchange="filterTable()">
      <option value="">All provinces</option>
      {"".join(f'<option value="{p.lower()}">{p}</option>' for p in PROVINCE_ORDER if any(r["province"] == p for r in ridings))}
    </select>
    <select id="winner-filter" onchange="filterTable()">
      <option value="">All parties</option>
      {"".join(f'<option value="{p}">{p}</option>' for p in PARTIES)}
    </select>
    <select id="comp-filter" onchange="filterTable()">
      <option value="">All races</option>
      <option value="tossup">Toss-ups only (&lt;60%)</option>
      <option value="likely">Likely (&lt;80%)</option>
      <option value="safe">Safe (≥80%)</option>
    </select>
  </div>
  <div class="result-count" id="result-count"></div>

  <!-- Table -->
  <div class="table-wrap">
    <table id="riding-table">
      <thead>
        <tr>
          <th onclick="sortTable(0)">Riding</th>
          <th onclick="sortTable(1)">Prov</th>
          <th onclick="sortTable(2)">Projected winner</th>
          <th onclick="sortTable(3)">Confidence</th>
          <th>LPC</th>
          <th>CPC</th>
          <th>NDP</th>
          <th>BQ</th>
          <th>GPC</th>
          <th>PPC</th>
        </tr>
      </thead>
      <tbody id="table-body">
        {table_rows}
      </tbody>
    </table>
  </div>
</div>

<script>
  let sortCol = -1, sortAsc = true;

  function filterTable() {{
    const search = document.getElementById("search").value.toLowerCase();
    const provF  = document.getElementById("prov-filter").value;
    const winF   = document.getElementById("winner-filter").value;
    const compF  = document.getElementById("comp-filter").value;
    const rows   = document.querySelectorAll("tr.riding-row");
    let visible  = 0;

    rows.forEach(r => {{
      const riding  = r.dataset.riding || "";
      const prov    = r.dataset.province || "";
      const winner  = r.dataset.winner || "";
      const topP    = parseFloat(r.dataset.top || "1");

      let compMatch = true;
      if (compF === "tossup") compMatch = topP < 0.60;
      else if (compF === "likely") compMatch = topP < 0.80;
      else if (compF === "safe")  compMatch = topP >= 0.80;

      const show = (
        (!search || riding.includes(search)) &&
        (!provF  || prov.includes(provF))    &&
        (!winF   || winner === winF)          &&
        compMatch
      );
      r.classList.toggle("hidden", !show);
      if (show) visible++;
    }});

    // Show/hide province headers based on visible riding rows
    document.querySelectorAll("tr.province-header").forEach(ph => {{
      const prov = ph.dataset.province;
      const hasVisible = [...rows].some(r =>
        r.dataset.province === prov && !r.classList.contains("hidden")
      );
      ph.classList.toggle("hidden", !hasVisible);
    }});

    document.getElementById("result-count").textContent =
      `Showing ${{visible}} of ${{rows.length}} ridings`;
  }}

  function sortTable(col) {{
    const ths = document.querySelectorAll("thead th");
    ths.forEach(th => th.classList.remove("sorted-asc", "sorted-desc"));

    if (sortCol === col) sortAsc = !sortAsc;
    else {{ sortCol = col; sortAsc = true; }}

    ths[col].classList.add(sortAsc ? "sorted-asc" : "sorted-desc");

    const tbody = document.getElementById("table-body");
    const rows  = [...tbody.querySelectorAll("tr.riding-row")];
    const provHeaders = [...tbody.querySelectorAll("tr.province-header")];

    rows.sort((a, b) => {{
      let va = a.cells[col]?.textContent.trim() ?? "";
      let vb = b.cells[col]?.textContent.trim() ?? "";
      // Numeric sort for probability columns
      if (col >= 4) {{
        va = parseFloat(va) || 0;
        vb = parseFloat(vb) || 0;
        return sortAsc ? va - vb : vb - va;
      }}
      return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    }});

    // When sorting by col > 0, remove province headers and just show flat list
    provHeaders.forEach(ph => {{
      if (col > 0) ph.classList.add("hidden");
      else ph.classList.remove("hidden");
    }});

    if (col === 0) {{
      // Restore province-grouped order
      const byProv = {{}};
      rows.forEach(r => {{
        const p = r.dataset.province;
        if (!byProv[p]) byProv[p] = [];
        byProv[p].push(r);
      }});
      tbody.innerHTML = "";
      provHeaders.forEach(ph => {{
        tbody.appendChild(ph);
        const prov = ph.dataset.province;
        (byProv[prov] || []).forEach(r => tbody.appendChild(r));
      }});
    }} else {{
      tbody.innerHTML = "";
      provHeaders.forEach(ph => tbody.appendChild(ph));
      rows.forEach(r => tbody.appendChild(r));
    }}

    filterTable();
  }}

  // Initial count
  filterTable();
</script>
</body>
</html>"""


def main() -> None:
    if not RIDING_CSV.exists():
        print(f"ERROR: {RIDING_CSV} not found. Run seat_projection.py first.")
        return
    if not SEAT_JSON.exists():
        print(f"ERROR: {SEAT_JSON} not found. Run seat_projection.py first.")
        return

    with open(RIDING_CSV, newline="", encoding="utf-8") as f:
        ridings = list(csv.DictReader(f))

    with open(SEAT_JSON, encoding="utf-8") as f:
        seat_data = json.load(f)

    html = build_html(ridings, seat_data)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved → {OUTPUT_HTML}  ({len(ridings)} ridings)")


if __name__ == "__main__":
    main()
