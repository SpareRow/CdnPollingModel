#!/usr/bin/env python3
"""
Jurisdiction configuration.

Every federal-specific constant used to be a module-level literal, duplicated
across five files (PARTIES in 5, PARTY_COLORS in 4, the majority threshold in 5).
They all live here now, so the same model code can run a federal or a provincial
projection.

Two rules keep the federal pipeline safe:

  1. FEDERAL reproduces the previous constants *exactly*. Its `root` is Path("."),
     and Path(".") / "x" == Path("x"), so every federal path is byte-identical to
     the bare Path("raw_polls.csv") constants it replaces.

  2. Nothing here mutates module globals. update_polls.py imports each module and
     calls its main() in one process, so a global swap would leak across
     jurisdictions when both run in the same invocation.

check_regression.py pins rule 1 against frozen goldens.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Jurisdiction:
    # ── Identity ──────────────────────────────────────────────────────────────
    key: str                      # "federal" | "qc"
    title: str                    # chart / page heading
    site_title: str               # <title> and dashboard banner
    election_date: str | None     # ISO date, or None if unscheduled

    # ── Where data and output live ────────────────────────────────────────────
    root: Path                    # data directory prefix
    docs_dir: Path                # published dashboard directory
    baseline_csv: str             # previous-election riding results, under root
    # Optional JSON of previous-election shares per swing group. Set when the
    # groups can't be derived by aggregating ridings — the language poles have
    # to be estimated, since no source reports how each language group voted.
    group_baseline_json: str | None

    # ── Parties ───────────────────────────────────────────────────────────────
    parties: tuple[str, ...]      # tracked, in display order
    seat_eligible: tuple[str, ...]  # can win a seat (excludes an "Others" bucket)
    party_colors: dict[str, str]
    party_names: dict[str, str]   # full names, for legends and tooltips

    # ── Legislature ───────────────────────────────────────────────────────────
    seats_total: int
    majority: int

    # ── Poll ingestion ────────────────────────────────────────────────────────
    # A list, not a str: canadianpolling.ca renames pages between cycles
    # (the QC provincial page is still slugged "QC-2022"), so we try in order.
    national_urls: tuple[str, ...]
    regional_urls: dict[str, str]
    party_label_map: dict[str, str]   # site's label -> our canonical code
    others_from_residual: bool        # derive the Others bucket as 100 - sum(tracked)

    # ── Riding table display grouping ─────────────────────────────────────────
    group_field: str              # riding dict key to group rows by
    group_order: tuple[str, ...]  # display order of those groups
    group_short: dict[str, str]   # abbreviations for the group column

    # ── Swing model ───────────────────────────────────────────────────────────
    swing_axis: str               # "region" (one-hot) | "language" (f-weighted)
    swing_groups: tuple[str, ...]
    swing_group_labels: dict[str, str]
    # 0.0 = additive swing (federal, unchanged); 1.0 = multiplicative, which is a
    # uniform shift in log-odds space and the right choice when a party collapses
    # or doubles. Values between blend the two.
    swing_blend: float
    # Shared per-party polling error applied across all swing groups. Independent
    # per-group draws cancel out and make the seat intervals too narrow.
    systematic_error_sd: float

    incumbency_bonus_pp: float
    open_seat_discount: float

    # ── Monte Carlo ───────────────────────────────────────────────────────────
    n_simulations: int = 10_000
    rng_seed: int = 42

    # ── Poll weighting ────────────────────────────────────────────────────────
    pollster_ratings: dict[str, float] = field(default_factory=dict)
    decay_lambda: float = 0.05    # ~14-day half-life
    default_sample_n: int = 1000

    def p(self, name: str) -> Path:
        """Resolve a data filename under this jurisdiction's root."""
        return self.root / name


# ── Shared pollster ratings ───────────────────────────────────────────────────
# Firms working both federal and Quebec provincial races (Leger, Mainstreet,
# Angus Reid, Ipsos, Pallas) keep the same rating in both.

_POLLSTER_RATINGS: dict[str, float] = {
    "angus reid": 1.2,
    "angus reid institute": 1.2,
    "nanos": 1.2,
    "nanos research": 1.2,
    "leger": 1.1,
    "abacus": 1.1,
    "abacus data": 1.1,
    "mainstreet": 1.0,
    "mainstreet research": 1.0,
    "ekos": 1.0,
    "ekos research": 1.0,
    "innovative": 1.0,
    "innovative research": 1.0,
    "innovative research group": 1.0,
    "liaison": 0.9,
    "liaison strategies": 0.9,
    "research co": 0.9,
    "research co.": 0.9,
    "ipsos": 1.0,
    "forum": 0.9,
    "forum research": 0.9,
}

_QC_POLLSTER_RATINGS: dict[str, float] = {
    **_POLLSTER_RATINGS,
    "pallas": 1.0,
    "pallas data": 1.0,
    "synopsis": 0.9,
    "pollara": 1.0,
    "segma": 1.0,
    "somm": 1.0,
    "somm/le devoir": 1.0,
}


# ── Federal ───────────────────────────────────────────────────────────────────

FEDERAL = Jurisdiction(
    key="federal",
    title="46th Canadian Federal Election",
    site_title="🍁 Canadian Federal Polling Tracker",
    election_date=None,

    root=Path("."),
    docs_dir=Path("docs/federal"),
    baseline_csv="riding_results_2025.csv",
    group_baseline_json=None,   # regions aggregate straight from the ridings

    parties=("LPC", "CPC", "NDP", "BQ", "GPC", "PPC"),
    seat_eligible=("LPC", "CPC", "NDP", "BQ", "GPC", "PPC"),
    party_colors={
        "LPC": "#D71920", "CPC": "#1A4782", "NDP": "#F4831F",
        "BQ": "#00A0C6", "GPC": "#3D9B35", "PPC": "#4B0082",
    },
    party_names={
        "LPC": "Liberal", "CPC": "Conservative", "NDP": "New Democratic",
        "BQ": "Bloc Québécois", "GPC": "Green", "PPC": "People's Party",
    },

    seats_total=343,
    majority=172,

    national_urls=("https://canadianpolling.ca/canada-2025/",),
    regional_urls={
        "ON": "https://canadianpolling.ca/Canada-ON-2025/",
        "QC": "https://canadianpolling.ca/Canada-QC-2025/",
        "BC": "https://canadianpolling.ca/Canada-BC-2025/",
        "AB": "https://canadianpolling.ca/Canada-AB-2025/",
        "MB_SK": "https://canadianpolling.ca/Canada-SKMB-2025",
        "Atlantic": "https://canadianpolling.ca/Canada-ATL-2025",
    },
    party_label_map={
        "LPC": "LPC", "CPC": "CPC", "NDP": "NDP",
        "BQ": "BQ", "GPC": "GPC", "PPC": "PPC",
        "LIB": "LPC", "CON": "CPC", "GRN": "GPC", "BLQ": "BQ",
    },
    others_from_residual=False,

    group_field="province",
    group_order=(
        "Newfoundland and Labrador", "Prince Edward Island", "Nova Scotia",
        "New Brunswick", "Quebec", "Ontario", "Manitoba", "Saskatchewan",
        "Alberta", "British Columbia", "Northwest Territories", "Nunavut", "Yukon",
    ),
    group_short={
        "Newfoundland and Labrador": "NL", "Prince Edward Island": "PE",
        "Nova Scotia": "NS", "New Brunswick": "NB", "Quebec": "QC",
        "Ontario": "ON", "Manitoba": "MB", "Saskatchewan": "SK",
        "Alberta": "AB", "British Columbia": "BC",
        "Northwest Territories": "NT", "Nunavut": "NU", "Yukon": "YT",
    },

    swing_axis="region",
    swing_groups=("ON", "QC", "BC", "AB", "MB_SK", "Atlantic", "North"),
    swing_group_labels={
        "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia",
        "AB": "Alberta", "MB_SK": "Man./Sask.", "Atlantic": "Atlantic",
    },
    swing_blend=0.0,          # additive — the original federal behaviour
    systematic_error_sd=0.0,  # off; see the guard in seat_projection.sample_swing

    incumbency_bonus_pp=4.0,
    open_seat_discount=0.5,

    pollster_ratings=_POLLSTER_RATINGS,
)


# ── Quebec ────────────────────────────────────────────────────────────────────
#
# Modelling notes (see the plan for the full reasoning):
#   · swing_blend=1.0 — the CAQ goes 40.98% -> ~24%. Additive swing drives it
#     negative in its weak divisions and understates the collapse in its
#     strongholds; multiplicative handles collapse and surge symmetrically.
#   · swing_axis="language" — canadianpolling.ca publishes no QC regional
#     crosstabs, but Wikipedia publishes francophone/non-francophone ones. That
#     is also the axis that matters: the PLQ vote is overwhelmingly
#     non-francophone and concentrated on Montreal Island.
#   · incumbency_bonus_pp=0.0 — no sitting-member data per new division, 39 of
#     127 divisions moved boundaries, the CAQ has a new leader, and a 90-seat
#     caucus polling at 24% will see heavy retirements. A bonus here would be
#     the single largest source of error.

QUEBEC = Jurisdiction(
    key="qc",
    title="2026 Quebec General Election",
    site_title="⚜️ Quebec Provincial Polling Tracker",
    election_date="2026-10-05",

    root=Path("qc"),
    docs_dir=Path("docs/quebec"),
    baseline_csv="riding_results_2022.csv",
    group_baseline_json="language_baseline_2022.json",

    parties=("CAQ", "PQ", "PLQ", "QS", "PCQ", "OTH"),
    seat_eligible=("CAQ", "PQ", "PLQ", "QS", "PCQ"),
    party_colors={
        "CAQ": "#00AEEF", "PQ": "#004C9D", "PLQ": "#ED1C24",
        "QS": "#FF5605", "PCQ": "#6B2C91", "OTH": "#9E9E9E",
    },
    party_names={
        "CAQ": "Coalition Avenir Québec",
        "PQ": "Parti Québécois",
        "PLQ": "Parti libéral du Québec",
        "QS": "Québec solidaire",
        "PCQ": "Parti conservateur du Québec",
        "OTH": "Others",
    },

    seats_total=127,
    majority=64,

    national_urls=(
        "https://canadianpolling.ca/QC-2022/",
        "https://canadianpolling.ca/QC-2026/",
    ),
    regional_urls={},  # no QC regional crosstabs published; language is the axis
    # Keys are matched against the site's label uppercased, so they must be
    # uppercase here. PVQ and "Others" both fold into the OTH bucket and are
    # summed rather than overwriting each other.
    party_label_map={
        "CAQ": "CAQ", "PQ": "PQ", "PLQ": "PLQ", "QS": "QS", "PCQ": "PCQ",
        "PLQ/QLP": "PLQ", "LIB": "PLQ", "LIBERAL": "PLQ",
        "PVQ": "OTH", "OTHERS": "OTH", "OTHER": "OTH", "GPQ": "OTH",
    },
    others_from_residual=True,

    group_field="region",
    # Wikipedia's region scheme rather than the 17 administrative regions: it
    # splits Montreal into West/East, which is exactly the linguistic divide
    # that drives Quebec results. Ordered west-to-east, urban-to-rural.
    group_order=(
        "West Montreal", "East Montreal", "Laval", "South Shore",
        "Eastern Montérégie", "Montérégie", "Laurentides", "Lanaudière",
        "Outaouais", "Capitale-Nationale", "Chaudière-Appalaches", "Estrie",
        "Centre-du-Québec", "Mauricie", "Saguenay–Lac-Saint-Jean",
        "Bas-Saint-Laurent", "Gaspésie–Îles-de-la-Madeleine", "Côte-Nord",
        "Abitibi-Témiscamingue", "Nord-du-Québec",
    ),
    group_short={
        "West Montreal": "MTL-O", "East Montreal": "MTL-E", "Laval": "LAV",
        "South Shore": "RIVE-S", "Eastern Montérégie": "MTG-E",
        "Montérégie": "MTG", "Laurentides": "LAU", "Lanaudière": "LAN",
        "Outaouais": "OUT", "Capitale-Nationale": "CAP",
        "Chaudière-Appalaches": "CHA", "Estrie": "EST",
        "Centre-du-Québec": "CQC", "Mauricie": "MAU",
        "Saguenay–Lac-Saint-Jean": "SLSJ", "Bas-Saint-Laurent": "BSL",
        "Gaspésie–Îles-de-la-Madeleine": "GIM", "Côte-Nord": "CTN",
        "Abitibi-Témiscamingue": "ABT", "Nord-du-Québec": "NDQ",
    },

    # Language, not geography. The PLQ vote is overwhelmingly non-francophone
    # and concentrated on Montreal Island — the latest Léger has them at 15%
    # among francophones and 52% among non-francophones — so a uniform
    # provincial swing misallocates it badly. It is also the only sub-provincial
    # signal published at all: there are no Quebec regional crosstabs.
    swing_axis="language",
    swing_groups=("FR", "NONFR"),
    swing_group_labels={"FR": "Francophone", "NONFR": "Non-francophone"},
    swing_blend=1.0,
    systematic_error_sd=2.0,

    incumbency_bonus_pp=0.0,
    open_seat_discount=0.5,

    pollster_ratings=_QC_POLLSTER_RATINGS,
)


ALL: dict[str, Jurisdiction] = {j.key: j for j in (FEDERAL, QUEBEC)}


def get(key: str) -> Jurisdiction:
    try:
        return ALL[key]
    except KeyError:
        raise SystemExit(
            f"Unknown jurisdiction {key!r}. Choose from: {', '.join(ALL)}"
        ) from None
