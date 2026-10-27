"""
The daily feature frame the forecast is built from.

This is the CHAMMU build. The schema differs from the Kalyon plant in one
way that matters: **there is no PPC table.** Kalyon reads plant export
straight from the power-plant controller; CHAMMU has no equivalent, so
generation is summed from the twelve inverters instead:

    INVERTER_1 .. INVERTER_12
        .TOTAL_ACTIVE_POWER          kW   per inverter, instantaneous
    WMS.GTI_RADIATION                W/m2 instantaneous
    WMS.GHI_RADIATION                W/m2 instantaneous
    WMS.MOD_TEMP                     degC module temperature
    WMS.AMBIENT_TEMP                 degC

Three consequences follow from summing inverters rather than reading a
grid meter, and each is a real limitation rather than a detail:

* **This is GROSS generation, not net export.** Transformer losses, station
  auxiliaries and night-time import are all downstream of the inverters and
  are therefore invisible here. The true export at the point of connection is
  lower. `energy_net_mwh` and `energy_gross_mwh` are consequently the SAME
  number; the distinction Kalyon can make, this plant cannot.
* **Curtailment cannot be detected.** There is no set-point channel, so
  `curtailed_minutes` is always 0 and `mean_setpoint_pct` always None. That
  means "not measurable here", NOT "no curtailment occurred". A curtailed day
  will look to the model like a cloudy one.
* **An offline inverter is indistinguishable from a dark one.** A minute is
  counted once any inverter reports, so a day on which two inverters were
  down still reads as complete, with a lower total the model attributes to
  weather. `avg_inverters_running` is recorded so the pattern is at least
  visible downstream.

Three properties of this archive drive the SQL below, and each was measured
rather than assumed:

* **The timezone of `TimeCol` has NOT been established for this plant.** On
  Kalyon it was proven to be UTC by two independent measurements. No such
  check has been done here, so the day boundary used below is whatever
  `TimeCol`'s own date is. If the clock turns out to be local time and the
  site is far from UTC, a solar day could straddle two dates and every daily
  total would be wrong at the edges. Verify before trusting daily figures.
  `LocalCol` is not used: it was unreliable on Kalyon and is unverified here.
* **Duplicate timestamps exist** (36 on INVERTER_1 alone). Summing raw rows
  double-counts those minutes, so every query collapses to one row per minute
  slot *before* integrating.
* **The archive is small and intermittent.** 44 distinct days between
  2025-04-25 and 2025-07-19 - a span of 86 calendar days - of which 32 carry
  a full day of inverter data. That is far too few to train and validate a
  day-ahead forecast credibly, and the reliability banner will say so.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── inverter fleet ───────────────────────────────────────────────────────────
# CHAMMU names its inverter tables INVERTER_1 .. INVERTER_12 - no zero padding,
# twelve of them - where Kalyon uses INVERTER_01 .. INVERTER_24. Both the count
# and the padding are configurable so neither is baked into the SQL.
INVERTER_COUNT = int(os.getenv("INVERTER_COUNT", "12"))
INVERTER_PAD = int(os.getenv("INVERTER_NAME_PAD", "0"))
INVERTER_TABLES = [
    f"INVERTER_{str(i).zfill(INVERTER_PAD)}" for i in range(1, INVERTER_COUNT + 1)
]
# An inverter drawing less than this is treated as not running. Housekeeping
# load at night is a few hundred watts; 1 kW sits clear of it.
INVERTER_RUNNING_KW = float(os.getenv("INVERTER_RUNNING_KW", "1.0"))

# A day must carry at least this many of 1440 minutes to be trusted as complete.
# Below it the total is an artefact of the logger, not of the plant.
COMPLETE_DAY_MINUTES = 1380

# A WMS day needs the same completeness before its irradiance integral is usable.
COMPLETE_WMS_MINUTES = 1380

# ── Quality control on MEASURED irradiance (pre-registration section 5) ──────
#
# These thresholds are physical, not statistical: surface irradiance at 38 deg N
# cannot reach them. A day that trips one has a sensor or scaling fault, and a
# model trained on it learns the fault.
#
# Raw readings are NEVER modified or deleted. A failure sets a flag computed at
# read time; the day keeps its energy and its FORECAST weather, and so remains
# in training and scoring. Only its measured-weather comparison and its
# condition label are withheld.
# C3 - a single-minute peak. Set well above the cloud-enhancement range: a
# brief edge-of-cloud spike genuinely reaches 1,200-1,400 W/m2 and is NOT a
# fault, so only a reading beyond any physical mechanism is flagged.
QC_MAX_PEAK_GTI_WM2 = 1500.0
# C4 - the daily integral, against a clear-sky ceiling from solar geometry
# rather than a flat number. A day cannot collect more than this fraction of
# the extraterrestrial horizontal irradiation; 0.85 is generous (real clear-sky
# transmittance is ~0.7-0.78), so only genuinely impossible days trip it.
QC_CLEARSKY_FRACTION = 0.85
QC_MIN_DISTINCT_GTI = 50        # C5 - fewer distinct values = latched sensor
QC_LATCH_MIN_MINUTES = 600
# Latitude used ONLY to compute the clear-sky ceiling in QC rule C4.
#
# This is NOT a statement about where the plant is. The plant's coordinates are
# not settled - public sources disagree - and nothing here should be read as
# confirming them. A regional fallback is acceptable for this one purpose
# because the ceiling is insensitive to it: across the whole Nigde province the
# daily extraterrestrial irradiation varies by well under one percent, far
# smaller than the margin C4 allows. Set WEATHER_LAT to the real latitude when
# it is known, and this sharpens automatically.
# This plant's latitude is NOT known, and no fallback is invented for it. The
# Kalyon build could use a regional approximation because the province was
# known; here nothing is. An unset WEATHER_LAT therefore DISABLES quality rule
# C4 rather than silently checking irradiance against the wrong sky - a wrong
# ceiling would either pass impossible days or reject sound ones, and both are
# worse than not running the rule. Set WEATHER_LAT and C4 switches itself on.
_lat_env = os.getenv("WEATHER_LAT", "").strip()
SITE_LATITUDE_DEG = float(_lat_env) if _lat_env else None
if SITE_LATITUDE_DEG is None:
    logger.warning(
        "WEATHER_LAT is not set, so the clear-sky ceiling (QC rule C4) is "
        "DISABLED. Peak and latch checks still run. Set WEATHER_LAT to this "
        "plant's latitude to enable it.")


def extraterrestrial_daily_kwh_m2(d: date, lat_deg: float = None) -> float:
    """
    Daily solar irradiation on a horizontal plane at the top of the atmosphere.

    Standard astronomy, no site data needed beyond latitude. It gives the hard
    physical ceiling: no pyranometer on the ground can read more than this, and
    in practice not more than ~78% of it.
    """
    import math
    lat_value = SITE_LATITUDE_DEG if lat_deg is None else lat_deg
    if lat_value is None:
        # No latitude configured - the caller must skip rule C4 rather than
        # compare against a number this function would have to invent.
        return float("inf")
    lat = math.radians(lat_value)
    n = d.timetuple().tm_yday
    # Solar constant 1361 W/m2, with Earth-Sun distance correction.
    e0 = 1 + 0.033 * math.cos(math.radians(360 * n / 365.0))
    decl = math.radians(23.45 * math.sin(math.radians(360 * (284 + n) / 365.0)))
    x = -math.tan(lat) * math.tan(decl)
    x = max(-1.0, min(1.0, x))
    ws = math.acos(x)                                   # sunset hour angle
    h0 = (24 / math.pi) * 1.361 * e0 * (
        ws * math.sin(lat) * math.sin(decl)
        + math.cos(lat) * math.cos(decl) * math.sin(ws))
    return max(0.0, h0)                                 # kWh/m2/day


# ── plant generation ─────────────────────────────────────────────────────────
# Built from the twelve inverter tables, because CHAMMU has no PPC equivalent.
#
# The shape is: one row per inverter per minute slot (duplicates averaged, never
# summed twice), then summed across inverters to a plant minute, then integrated
# to a day. Power is kW per inverter; the /1000 makes it MW so every figure
# downstream keeps the same unit as the Kalyon build.
#
# INVERTER_TABLES is generated rather than written out, so a plant with a
# different fleet size needs only the config value changed. The names are built
# from a validated integer and an f-string, never from request input.
_PER_INVERTER_SQL = "\n        UNION ALL\n".join(
    f"""        SELECT DATEADD(minute, DATEDIFF(minute, 0, TimeCol), 0) AS ts,
               AVG(CAST(TOTAL_ACTIVE_POWER AS float))                 AS kw
        FROM [{t}]
        GROUP BY DATEADD(minute, DATEDIFF(minute, 0, TimeCol), 0)"""
    for t in INVERTER_TABLES
)

# The raw first/last timestamp across the whole inverter fleet, for the coverage
# panel. One cheap UNION of twelve MIN/MAX probes rather than a scan.
_PLANT_SPAN_SQL = "SELECT MIN(mn), MAX(mx) FROM (" + " UNION ALL ".join(
    f"SELECT MIN(TimeCol) AS mn, MAX(TimeCol) AS mx FROM [{t}]" for t in INVERTER_TABLES
) + ") s"

_DAILY_PLANT_SQL = text(f"""
    WITH per_inverter AS (
{_PER_INVERTER_SQL}
    ),
    minute_slot AS (
        -- One row per minute for the whole plant: inverters summed, kW -> MW.
        SELECT ts,
               SUM(kw) / 1000.0                                  AS mw,
               SUM(CASE WHEN kw > {INVERTER_RUNNING_KW} THEN 1
                        ELSE 0 END)                              AS inv_running
        FROM per_inverter
        GROUP BY ts
    )
    SELECT CAST(ts AS DATE)                                      AS d,
           COUNT(*)                                              AS minutes,
           SUM(mw) / 60.0                                        AS energy_net_mwh,
           -- Identical to net: inverter output cannot go negative, and the
           -- losses that separate gross from net are not measured here.
           SUM(CASE WHEN mw > 0 THEN mw ELSE 0 END) / 60.0       AS energy_gross_mwh,
           MAX(mw)                                               AS peak_mw,
           AVG(CAST(inv_running AS float))                       AS avg_inverters_running,
           -- No set-point channel on this plant: curtailment is NOT MEASURABLE,
           -- which is not the same as absent. Reported as 0 so the column keeps
           -- its type; `curtailment_detectable` on the status payload says why.
           CAST(0 AS int)                                        AS curtailed_minutes,
           SUM(CASE WHEN mw > 1 THEN 1 ELSE 0 END)               AS producing_minutes,
           CAST(NULL AS float)                                   AS mean_setpoint_pct
    FROM minute_slot
    GROUP BY CAST(ts AS DATE)
    HAVING COUNT(*) >= {COMPLETE_DAY_MINUTES}
    ORDER BY d
""")


# ── weather stations ─────────────────────────────────────────────────────────
# Irradiance is instantaneous W/m2 sampled once a minute, so the daily
# integral is sum(W/m2) / 60 / 1000 -> kWh/m2.
_DAILY_WMS_SQL = text("""
    WITH minute_slot AS (
        -- CHAMMU's WMS carries the same quantities under different column
        -- names; the aliases below keep everything downstream unchanged.
        SELECT DATEADD(minute, DATEDIFF(minute, 0, TimeCol), 0)   AS ts,
               AVG(GTI_RADIATION)                                  AS gti,
               AVG(GHI_RADIATION)                                  AS ghi,
               AVG(MOD_TEMP)                                       AS module_temp,
               AVG(AMBIENT_TEMP)                                   AS air_temp,
               AVG(WIND_SPEED)                                     AS wind,
               AVG(HUMIDITY)                                       AS humidity,
               AVG(SOILING_RATIO_SENSOR_1)                         AS soiling
        FROM WMS
        GROUP BY DATEADD(minute, DATEDIFF(minute, 0, TimeCol), 0)
    )
    SELECT CAST(ts AS DATE)              AS d,
           COUNT(*)                      AS minutes,
           SUM(gti) / 60000.0            AS gti_kwh_m2,
           SUM(ghi) / 60000.0            AS ghi_kwh_m2,
           AVG(module_temp)              AS module_temp_c,
           AVG(air_temp)                 AS air_temp_c,
           AVG(wind)                     AS wind_ms,
           AVG(humidity)                 AS humidity_pct,
           AVG(soiling)                  AS soiling_ratio,
           MAX(gti)                      AS peak_gti_wm2,
           COUNT(DISTINCT CAST(gti AS decimal(18,4))) AS distinct_gti
    FROM minute_slot
    GROUP BY CAST(ts AS DATE)
    ORDER BY d
""")


def daily_plant(db: Session) -> List[Dict[str, Any]]:
    """One row per COMPLETE day of plant generation."""
    rows = db.execute(_DAILY_PLANT_SQL).fetchall()
    return [
        {
            "d": r[0],
            "minutes": int(r[1]),
            "energy_net_mwh": float(r[2] or 0.0),
            "energy_gross_mwh": float(r[3] or 0.0),
            "peak_mw": float(r[4] or 0.0),
            "avg_inverters_running": float(r[5] or 0.0),
            "curtailed_minutes": int(r[6] or 0),
            "producing_minutes": int(r[7] or 0),
            "mean_setpoint_pct": float(r[8]) if r[8] is not None else None,
        }
        for r in rows
    ]


def daily_weather(db: Session) -> Dict[date, Dict[str, Any]]:
    """
    Measured weather per day, keyed by date.

    `frozen` marks a day whose irradiance channel barely moved. A pyranometer
    stuck at one value produces a plausible-looking daily total that is pure
    fiction, and a model trained on it learns the fault. Such days keep their
    plant data and lose their weather.
    """
    out: Dict[date, Dict[str, Any]] = {}
    for r in db.execute(_DAILY_WMS_SQL).fetchall():
        minutes = int(r[1])
        distinct_gti = int(r[10] or 0)
        ghi = float(r[3] or 0.0)
        peak = float(r[9] or 0.0)
        # C5 - a latched sensor, not a uniform sky.
        frozen = minutes >= QC_LATCH_MIN_MINUTES and distinct_gti < QC_MIN_DISTINCT_GTI
        # C3 / C4 - physically impossible readings.
        qc_fail = []
        if peak > QC_MAX_PEAK_GTI_WM2:
            qc_fail.append(f"C3 peak GTI {peak:.0f} W/m2 > {QC_MAX_PEAK_GTI_WM2:.0f}")
        ceiling = QC_CLEARSKY_FRACTION * extraterrestrial_daily_kwh_m2(r[0])
        if ghi > ceiling:
            qc_fail.append(f"C4 daily GHI {ghi:.2f} > clear-sky ceiling "
                           f"{ceiling:.2f} kWh/m2")
        if frozen:
            qc_fail.append(f"C5 only {distinct_gti} distinct GTI values")
        out[r[0]] = {
            "d": r[0],
            "minutes": minutes,
            "complete": minutes >= COMPLETE_WMS_MINUTES,
            "frozen": frozen,
            "qc_failures": qc_fail,
            "qc_ok": not qc_fail,
            "gti_kwh_m2": float(r[2] or 0.0),
            "ghi_kwh_m2": float(r[3] or 0.0),
            "module_temp_c": float(r[4]) if r[4] is not None else None,
            "air_temp_c": float(r[5]) if r[5] is not None else None,
            "wind_ms": float(r[6]) if r[6] is not None else None,
            "humidity_pct": float(r[7]) if r[7] is not None else None,
            "soiling_ratio": float(r[8]) if r[8] is not None else None,
            "peak_gti_wm2": float(r[9] or 0.0),
        }
    return out


# ── the supervised frame ─────────────────────────────────────────────────────
#
# Only values knowable BEFORE the target day may appear as a feature. Every
# plant feature is therefore lagged by at least one day, and the only same-day
# inputs permitted are weather FORECAST values, which are supplied separately by
# the caller and are by definition available at prediction time.
LAG_FEATURES = [
    "lag1", "lag2", "lag3", "lag7",
    "roll3_mean", "roll7_mean", "roll7_std",
    "doy_sin", "doy_cos", "dow",
    "curtailed_min_lag1", "inv_running_lag1",
]
WEATHER_FEATURES = ["fc_gti_kwh_m2", "fc_air_temp_c", "fc_cloud_pct"]

MIN_HISTORY_DAYS = 7     # lag-7 needs a week behind every row


def build_frame(
    plant: List[Dict[str, Any]],
    weather: Optional[Dict[date, Dict[str, Any]]] = None,
    target: str = "energy_net_mwh",
    weather_forecast: Optional[Dict[date, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """
    Turn the daily series into X, y and the metadata the backtest needs.

    `weather_forecast` maps a date to the weather that WOULD have been forecast
    for it. It is deliberately a separate argument from `weather` (the measured
    record) so that observed weather can never leak in by accident: measured
    values are used for reporting and for condition labels, never as features.
    """
    import numpy as np

    rows = sorted(plant, key=lambda r: r["d"])
    use_weather = bool(weather_forecast)
    names = list(LAG_FEATURES) + (list(WEATHER_FEATURES) if use_weather else [])

    y_all = np.array([r[target] for r in rows], dtype=float)

    X: List[List[float]] = []
    y: List[float] = []
    dates: List[date] = []
    meta: List[Dict[str, Any]] = []

    for i, r in enumerate(rows):
        if i < MIN_HISTORY_DAYS:
            continue
        prev = rows[i - 1]
        hist = y_all[i - 7:i]
        doy = r["d"].timetuple().tm_yday
        feats = [
            float(y_all[i - 1]), float(y_all[i - 2]), float(y_all[i - 3]), float(y_all[i - 7]),
            float(np.mean(y_all[i - 3:i])),
            float(np.mean(hist)), float(np.std(hist)),
            float(np.sin(2 * np.pi * doy / 365.25)), float(np.cos(2 * np.pi * doy / 365.25)),
            float(r["d"].weekday()),
            float(prev["curtailed_minutes"]), float(prev["avg_inverters_running"]),
        ]
        if use_weather:
            fc = (weather_forecast or {}).get(r["d"])
            if not fc:
                continue                      # no forecast for that day -> no row
            feats += [float(fc.get("gti_kwh_m2", 0.0)),
                      float(fc.get("air_temp_c", 0.0)),
                      float(fc.get("cloud_pct", 0.0))]

        obs = (weather or {}).get(r["d"])
        gti = obs["gti_kwh_m2"] if obs and obs["complete"] and not obs["frozen"] else None

        X.append(feats)
        y.append(float(r[target]))
        dates.append(r["d"])
        meta.append({
            "d": r["d"],
            "curtailed_minutes": r["curtailed_minutes"],
            "gti_kwh_m2": gti,
            "peak_mw": r["peak_mw"],
            "avg_inverters_running": r["avg_inverters_running"],
        })

    return {
        "X": np.array(X, dtype=float) if X else np.empty((0, len(names))),
        "y": np.array(y, dtype=float),
        "dates": dates,
        "meta": meta,
        "feature_names": names,
        "target": target,
        "weather_features_used": use_weather,
    }


def coverage_summary(db: Session) -> Dict[str, Any]:
    """
    What the archive can actually support, computed live.

    Served by the API before anything is trained, so the limits are visible in
    the UI rather than buried in a report.
    """
    plant = daily_plant(db)
    wx = daily_weather(db)
    paired = [p for p in plant
              if (w := wx.get(p["d"])) and w["complete"] and not w["frozen"]]
    # The RAW extent of the generation record, across ALL twelve inverters.
    #
    # Two things this is deliberately not. It is not one table's range: reading
    # INVERTER_1 alone understated the start by nine days, because the inverters
    # did not all begin logging on the same date. And it is not the range of
    # COMPLETE days either - that would report the record ending on 4 June when
    # data in fact runs to 19 July, just too sparsely to integrate. The number of
    # usable days is reported separately, as `complete_days`.
    span = db.execute(text(_PLANT_SPAN_SQL)).first()
    wspan = db.execute(text(
        "SELECT MIN(TimeCol), MAX(TimeCol) FROM WMS")).first()
    return {
        "plant_first": span[0], "plant_last": span[1],
        "weather_first": wspan[0], "weather_last": wspan[1],
        "complete_days": len(plant),
        "days_with_weather": len(paired),
        "supervised_rows": max(0, len(plant) - MIN_HISTORY_DAYS),
        "frozen_weather_days": sum(1 for w in wx.values() if w["frozen"]),
        "curtailed_days": sum(1 for p in plant if p["curtailed_minutes"] > 0),
        "heavily_curtailed_days": sum(1 for p in plant if p["curtailed_minutes"] >= 300),
        "complete_day_threshold_minutes": COMPLETE_DAY_MINUTES,
        # Kalyon's note asserted a verified fact. Nothing equivalent has been
        # measured for this plant, so the note states the uncertainty instead of
        # inheriting a claim that happens to be about a different site.
        "timezone_note": (
            "The timezone of TimeCol has NOT been verified for this plant. Days "
            "are grouped by TimeCol's own date. If the clock is local time and "
            "the site is far from UTC, a solar day may straddle two dates and "
            "daily totals will be wrong at the boundaries. LocalCol is not used: "
            "it proved unreliable on the reference plant and is unverified here."
        ),
        # Summed from inverters, not read from a grid meter, so transformer and
        # auxiliary losses are excluded and curtailment cannot be seen at all.
        "generation_source": (
            f"Gross AC output summed across {INVERTER_COUNT} inverters "
            f"({INVERTER_TABLES[0]}..{INVERTER_TABLES[-1]}.TOTAL_ACTIVE_POWER). "
            "This plant has no plant-controller or export-meter table, so net "
            "export at the point of connection is not measured and is lower than "
            "the figures shown."
        ),
        "curtailment_detectable": False,
    }
