"""
Per-device daily series, in the shape the existing forecast engine already eats.

The engine is series-agnostic. `build_frame`, `walk_forward_one_day`,
`models.candidates` and `metrics.evaluate` take a list of daily dicts and never
ask where it came from, so device-level forecasting needs a new data source and
nothing else. This module is that source; not one line of the plant pipeline
changes, and the plant forecast keeps running exactly as before.

Three rules carried over from `features.py` deliberately, so a device figure and
the plant figure mean the same thing:

* **Minute-slot de-duplication before integrating.** Duplicate timestamps exist
  in every table here (263 on INVERTER_3, 273 on ICR2_SMB29, 563 on
  ICR1_HT_MFM). Summing raw rows double-counts those minutes.
* **A day needs COMPLETE_DAY_MINUTES of the 1,440.** Below that the total
  measures the logger, not the device.
* **Night zeros are not faults.** They are simply minutes with no sun, counted
  in the day's coverage and contributing nothing to its energy. Nothing here
  treats a zero as missing.

Device names are discovered from INFORMATION_SCHEMA rather than assumed, so a
plant with a different fleet needs no edit. Only names matching the strict
patterns below are accepted, and every one is validated before it reaches a
query string.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.forecast.features import COMPLETE_DAY_MINUTES, INVERTER_RUNNING_KW

logger = logging.getLogger(__name__)

# ── what counts as a device ──────────────────────────────────────────────────
# Anchored patterns, so only a name of exactly this form is ever interpolated
# into SQL. Anything else discovered in the schema is ignored rather than
# guessed at.
INVERTER_RE = re.compile(r"^INVERTER_(\d+)$")
COMBINER_RE = re.compile(r"^(ICR1|ICR2|MCR)_SMB(\d+)$")
METER_RE = re.compile(r"^(?:(?:ICR1|ICR2|INC1|INC2|INC3)_HT_MFM|OUTGOING_MFM)$")

SAFE_TABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

# A combiner reporting less than this on an average complete day is treated as
# producing nothing worth ranking. It is a reporting threshold, not a verdict:
# the box still appears in the table, flagged for investigation.
COMBINER_ZERO_KWH = 50.0

# Below this many complete days a device cannot be evaluated at all. The frame
# builder drops 7 days to lag-7 warm-up, and a backtest needs a train block and
# a test block after that.
MIN_DEVICE_DAYS = 21


def _safe(table: str) -> str:
    if not SAFE_TABLE.match(table):
        raise ValueError(f"refusing to query a table name of this form: {table!r}")
    return table


# ── discovery ────────────────────────────────────────────────────────────────
def discover(db: Session) -> Dict[str, List[Dict[str, Any]]]:
    """
    Every device this database actually holds, read from the catalogue.

    Returns three lists keyed "inverters", "combiners", "meters". Counts are
    whatever is there; nothing is hard-coded, so this reports 12 inverters here
    and a different number elsewhere without a change.
    """
    rows = db.execute(text(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_TYPE = 'BASE TABLE'")).fetchall()
    names = sorted(r[0] for r in rows)

    inverters, combiners, meters = [], [], []
    for n in names:
        m = INVERTER_RE.match(n)
        if m:
            inverters.append({"id": n, "table": n, "type": "inverter",
                              "number": int(m.group(1)), "group": "Inverters"})
            continue
        m = COMBINER_RE.match(n)
        if m:
            combiners.append({"id": n, "table": n, "type": "combiner",
                              "number": int(m.group(2)), "group": m.group(1)})
            continue
        if METER_RE.match(n):
            meters.append({"id": n, "table": n, "type": "meter",
                           "number": 0, "group": "Meters"})

    inverters.sort(key=lambda d: d["number"])
    combiners.sort(key=lambda d: (d["group"], d["number"]))
    return {"inverters": inverters, "combiners": combiners, "meters": meters}


# ── one inverter's daily series ──────────────────────────────────────────────
_INVERTER_DAILY = """
    WITH minute_slot AS (
        -- Duplicates averaged, never summed twice.
        SELECT DATEADD(minute, DATEDIFF(minute, 0, TimeCol), 0)  AS ts,
               AVG(CAST(TOTAL_ACTIVE_POWER AS float))            AS kw
        FROM [{table}]
        GROUP BY DATEADD(minute, DATEDIFF(minute, 0, TimeCol), 0)
    )
    SELECT CAST(ts AS DATE)                                      AS d,
           COUNT(*)                                              AS minutes,
           SUM(kw) / 60.0 / 1000.0                               AS energy_mwh,
           MAX(kw) / 1000.0                                      AS peak_mw,
           -- Share of the day's recorded minutes with the inverter producing.
           -- Night zeros pull this down and that is correct: it is a duty
           -- figure, not an availability claim.
           AVG(CASE WHEN kw > {running_kw} THEN 1.0 ELSE 0.0 END) AS duty,
           SUM(CASE WHEN kw > {running_kw} THEN 1 ELSE 0 END)     AS producing_minutes
    FROM minute_slot
    GROUP BY CAST(ts AS DATE)
    HAVING COUNT(*) >= {complete}
    ORDER BY d
"""


def daily_inverter(db: Session, table: str) -> List[Dict[str, Any]]:
    """
    One row per complete day for a single inverter, keyed exactly as
    `features.daily_plant` keys the plant, so `build_frame` accepts it unchanged.

    Energy is this inverter's own output only. No other device's readings enter
    this query, which is what keeps one device's data out of another's forecast.
    """
    sql = _INVERTER_DAILY.format(table=_safe(table),
                                 running_kw=INVERTER_RUNNING_KW,
                                 complete=COMPLETE_DAY_MINUTES)
    out = []
    for r in db.execute(text(sql)).fetchall():
        energy = float(r[2] or 0.0)
        out.append({
            "d": r[0],
            "minutes": int(r[1]),
            # Gross equals net at device level: an inverter's AC output cannot
            # go negative, and the losses that separate the two are downstream.
            "energy_net_mwh": energy,
            "energy_gross_mwh": energy,
            "peak_mw": float(r[3] or 0.0),
            "avg_inverters_running": float(r[4] or 0.0),
            # No set-point channel on this plant: curtailment is not measurable,
            # which is not the same as absent.
            "curtailed_minutes": 0,
            "producing_minutes": int(r[5] or 0),
            "mean_setpoint_pct": None,
        })
    return out


# ── combiner group, in one pass ──────────────────────────────────────────────
def _combiner_union(tables: List[str]) -> str:
    """One query across a whole group, rather than one query per box."""
    parts = []
    for t in tables:
        parts.append(f"""
        SELECT '{_safe(t)}' AS device,
               DATEADD(minute, DATEDIFF(minute, 0, TimeCol), 0) AS ts,
               AVG(CAST(DC_POWER AS float))                     AS w
        FROM [{_safe(t)}]
        GROUP BY DATEADD(minute, DATEDIFF(minute, 0, TimeCol), 0)""")
    union = "\n        UNION ALL".join(parts)
    # DC_POWER is WATTS on this plant, established by measurement: a box peaks
    # near 427,000, and 14 boxes per inverter at that scale reconcile with the
    # inverter's own 3.2 MW. Hence /1000 for kWh rather than the /1 that a kW
    # reading would need.
    return f"""
    WITH per_box AS ({union}
    )
    SELECT device,
           CAST(ts AS DATE)        AS d,
           COUNT(*)                AS minutes,
           SUM(w) / 60.0 / 1000.0  AS energy_kwh,
           MAX(w) / 1000.0         AS peak_kw
    FROM per_box
    GROUP BY device, CAST(ts AS DATE)
    HAVING COUNT(*) >= {COMPLETE_DAY_MINUTES}
    ORDER BY device, d
    """


def daily_combiners(db: Session, tables: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Daily kWh per combiner box, grouped by device, for one whole group."""
    if not tables:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {t: [] for t in tables}
    for r in db.execute(text(_combiner_union(tables))).fetchall():
        out.setdefault(r[0], []).append({
            "d": r[1],
            "minutes": int(r[2]),
            "energy_kwh": float(r[3] or 0.0),
            "peak_kw": float(r[4] or 0.0),
        })
    return out


# ── freshness, for every device type ─────────────────────────────────────────
def last_seen(db: Session, tables: List[str]) -> Dict[str, Any]:
    """
    The most recent timestamp each table holds.

    Reported as measured. The archive currently stops on 2025-07-19 and the UI
    says so rather than implying the feed is live.
    """
    if not tables:
        return {}
    parts = [f"SELECT '{_safe(t)}' AS device, MAX(TimeCol) AS last_ts FROM [{_safe(t)}]"
             for t in tables]
    sql = "\nUNION ALL ".join(parts)
    return {r[0]: r[1] for r in db.execute(text(sql)).fetchall()}
