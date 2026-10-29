"""
Device-level forecasting and combiner peer analysis.

Two jobs, deliberately different in kind:

**Inverters get a forecast.** Each one is run through the same backtest the
plant uses, on its own history alone, with its own winner chosen from the same
five candidates. The plant's winning model is NOT inherited: a model that suits
the summed series need not suit a single inverter, and assuming otherwise would
be picking a model without comparing it.

**Combiners get an analysis, not a forecast.** 34 of the 170 report essentially
nothing and the rest carry 34 complete days - too few to forecast honestly, and
in any case the operationally useful question about a combiner is not "how much
tomorrow" but "is this one keeping up with its neighbours". So they are ranked
against their own group, and what the comparison shows is stated as evidence
with the cause left open.

Results live in an in-process cache rather than a table, because adding tables
was out of scope. They are lost on restart and recomputed on demand.
"""

from __future__ import annotations

import logging
import statistics
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.forecast import backtest as BT
from app.forecast import devices as D
from app.forecast import features as F
from app.forecast import models as M

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# device id -> result dict. Survives requests, not restarts.
_forecast_cache: Dict[str, Any] = {"generated_at": None, "devices": [], "running": False}


# ── per-inverter forecasting ─────────────────────────────────────────────────
def _forecast_one(plant_rows: List[Dict[str, Any]], device: Dict[str, Any]
                  ) -> Dict[str, Any]:
    """
    Backtest one device on its own series.

    Returns a row whether it succeeds or not: a device that cannot be evaluated
    is reported as such, with the reason, rather than dropped from the table.
    """
    base = {
        "device": device["id"],
        "type": device["type"],
        "group": device["group"],
        "complete_days": len(plant_rows),
        "latest_actual_mwh": round(plant_rows[-1]["energy_net_mwh"], 2) if plant_rows else None,
        "latest_actual_date": plant_rows[-1]["d"] if plant_rows else None,
        "mean_actual_mwh": round(
            statistics.fmean(r["energy_net_mwh"] for r in plant_rows), 2
        ) if plant_rows else None,
    }

    if len(plant_rows) < D.MIN_DEVICE_DAYS:
        return {**base, "status": "INSUFFICIENT DATA", "validation": "INSUFFICIENT DATA",
                "reason": (f"{len(plant_rows)} complete days; at least "
                           f"{D.MIN_DEVICE_DAYS} are needed before a backtest can "
                           f"train and still hold days back."),
                "predicted_mwh": None, "forecast_date": None,
                "score_pct": None, "wape_pct": None, "mae_mwh": None,
                "rmse_mwh": None, "bias_mwh": None, "r2": None, "n_test": 0,
                "model": None, "leaderboard": []}

    frame = F.build_frame(plant_rows, None, "energy_net_mwh", weather_forecast=None)
    if len(frame["y"]) < 10:
        return {**base, "status": "INSUFFICIENT DATA", "validation": "INSUFFICIENT DATA",
                "reason": f"only {len(frame['y'])} supervised rows after lag warm-up.",
                "predicted_mwh": None, "forecast_date": None,
                "score_pct": None, "wape_pct": None, "mae_mwh": None,
                "rmse_mwh": None, "bias_mwh": None, "r2": None, "n_test": 0,
                "model": None, "leaderboard": []}

    bt = BT.walk_forward_one_day(frame)
    if not bt.get("ok"):
        return {**base, "status": "INSUFFICIENT DATA", "validation": "INSUFFICIENT DATA",
                "reason": bt.get("reason", "backtest could not run."),
                "predicted_mwh": None, "forecast_date": None,
                "score_pct": None, "wape_pct": None, "mae_mwh": None,
                "rmse_mwh": None, "bias_mwh": None, "r2": None, "n_test": 0,
                "model": None, "leaderboard": []}

    test = bt["test"]
    n_test = int(test.get("n") or 0)

    # One step past the last observed day, from a model refit on everything.
    # Tomorrow's feature vector has to be BUILT: the last row of X predicts the
    # last observed day, not the day after it.
    model = M.fit_model(bt["selected_model"], frame["X"], frame["y"])
    nxt = _next_day_value(frame, plant_rows, model)

    interval = BT.conformal_interval(bt["test_records"])
    half = (interval or {}).get("half_width_mwh")

    # Sample size decides the badge, never the score. A high number on a
    # handful of days is not a measurement.
    enough = n_test >= BT.MIN_CREDIBLE_TEST_DAYS
    validation = "MEASURED" if enough else "PROVISIONAL"

    from datetime import timedelta
    return {
        **base,
        "status": "OK",
        "validation": validation,
        "reason": (f"Scored on {n_test} held-out day{'' if n_test == 1 else 's'}"
                   + ("." if enough else
                      f", below the {BT.MIN_CREDIBLE_TEST_DAYS} needed for the "
                      f"figure to be stable. Provisional.")),
        "predicted_mwh": round(nxt, 2),
        "forecast_date": plant_rows[-1]["d"] + timedelta(days=1),
        "lower_mwh": round(max(0.0, nxt - half), 2) if half else None,
        "upper_mwh": round(nxt + half, 2) if half else None,
        "model": bt["selected_model"],
        "score_pct": round(test["score_pct"], 1) if test.get("score_pct") is not None else None,
        "wape_pct": round(test["wape_pct"], 1) if test.get("wape_pct") is not None else None,
        "mae_mwh": round(test["mae_mwh"], 2) if test.get("mae_mwh") is not None else None,
        "rmse_mwh": round(test["rmse_mwh"], 2) if test.get("rmse_mwh") is not None else None,
        "bias_mwh": round(test["bias_mwh"], 2) if test.get("bias_mwh") is not None else None,
        "r2": round(test["r2"], 2) if test.get("r2") is not None else None,
        "n_test": n_test,
        "test_start": bt["test_start"], "test_end": bt["test_end"],
        "leaderboard": bt["leaderboard"],
    }


def _next_day_value(frame, rows, model) -> float:
    """Tomorrow's features, built from the series the way build_frame builds them."""
    import numpy as np
    from datetime import timedelta

    y = [float(r["energy_net_mwh"]) for r in rows]
    last = rows[-1]
    d = last["d"] + timedelta(days=1)
    doy = d.timetuple().tm_yday
    hist = np.array(y[-7:], dtype=float)
    feats = [
        y[-1], y[-2], y[-3], y[-7],
        float(np.mean(y[-3:])), float(np.mean(hist)), float(np.std(hist)),
        float(np.sin(2 * np.pi * doy / 365.25)),
        float(np.cos(2 * np.pi * doy / 365.25)),
        float(d.weekday()),
        float(last["curtailed_minutes"]), float(last["avg_inverters_running"]),
    ]
    width = frame["X"].shape[1]
    while len(feats) < width:
        feats.append(float(frame["X"][-1][len(feats)]))
    return float(M.predict(model, np.array([feats[:width]], dtype=float))[0])


def run_device_forecasts(db: Session) -> Dict[str, Any]:
    """Forecast every discovered inverter. Slow enough to be button-driven."""
    with _lock:
        if _forecast_cache.get("running"):
            return {"ok": False, "reason": "A device run is already in progress."}
        _forecast_cache["running"] = True
    try:
        inv = D.discover(db)["inverters"]
        fresh = D.last_seen(db, [d["table"] for d in inv])
        out = []
        for device in inv:
            rows = D.daily_inverter(db, device["table"])
            row = _forecast_one(rows, device)
            row["last_seen"] = fresh.get(device["table"])
            out.append(row)
            logger.info("device forecast %s: %s (%s complete days)",
                        device["id"], row["status"], row["complete_days"])
        result = {"ok": True, "generated_at": datetime.now(),
                  "devices": out, "n_devices": len(out)}
        with _lock:
            _forecast_cache.update({"generated_at": result["generated_at"],
                                    "devices": out, "running": False})
        return result
    except Exception:
        with _lock:
            _forecast_cache["running"] = False
        raise


def cached_forecasts() -> Dict[str, Any]:
    with _lock:
        return {"generated_at": _forecast_cache["generated_at"],
                "devices": list(_forecast_cache["devices"]),
                "n_devices": len(_forecast_cache["devices"]),
                "running": bool(_forecast_cache.get("running"))}


# ── combiner peer analysis ───────────────────────────────────────────────────
def combiner_analysis(db: Session) -> Dict[str, Any]:
    """
    Rank every combiner against its own group.

    Comparison is within ICR1 / ICR2 / MCR rather than across all 170, because
    the groups sit on different parts of the plant and a cross-group ranking
    would read orientation and layout as if they were faults.

    Every flag is evidence, not a diagnosis. A box reading zero may be
    disconnected, may be uninstrumented, or may have a dead sensor on a working
    string; this says which boxes differ from their peers and by how much, and
    leaves the cause to someone who can walk the site.
    """
    disc = D.discover(db)
    combiners = disc["combiners"]
    by_group: Dict[str, List[Dict[str, Any]]] = {}
    for c in combiners:
        by_group.setdefault(c["group"], []).append(c)

    rows: List[Dict[str, Any]] = []
    group_stats: Dict[str, Any] = {}

    for group, members in sorted(by_group.items()):
        tables = [m["table"] for m in members]
        series = D.daily_combiners(db, tables)
        fresh = D.last_seen(db, tables)

        # Each box's own average over its complete days.
        means: Dict[str, Optional[float]] = {}
        for t in tables:
            days = series.get(t) or []
            means[t] = (statistics.fmean(x["energy_kwh"] for x in days)
                        if days else None)

        producing = [v for v in means.values()
                     if v is not None and v >= D.COMBINER_ZERO_KWH]
        median = statistics.median(producing) if producing else None
        group_stats[group] = {
            "devices": len(tables),
            "producing": len(producing),
            "median_kwh": round(median, 1) if median else None,
            "group_total_mwh": round(sum(producing) / 1000.0, 2) if producing else None,
        }

        # Rank only the producing boxes; a zero box has no meaningful rank.
        ranked = sorted([t for t in tables
                         if means[t] is not None and means[t] >= D.COMBINER_ZERO_KWH],
                        key=lambda t: means[t], reverse=True)
        rank_of = {t: i + 1 for i, t in enumerate(ranked)}

        for m in members:
            t = m["table"]
            days = series.get(t) or []
            mean = means[t]
            latest = days[-1] if days else None
            dev_pct = (100.0 * (mean - median) / median
                       if (mean is not None and median) else None)

            status, note = _combiner_status(mean, len(days), median)
            rows.append({
                "device": m["id"],
                "type": "combiner",
                "group": group,
                "complete_days": len(days),
                "latest_actual_kwh": round(latest["energy_kwh"], 1) if latest else None,
                "latest_actual_date": latest["d"] if latest else None,
                "mean_actual_kwh": round(mean, 1) if mean is not None else None,
                "group_median_kwh": round(median, 1) if median else None,
                "deviation_pct": round(dev_pct, 1) if dev_pct is not None else None,
                "peer_rank": rank_of.get(t),
                "peer_total": len(ranked),
                "last_seen": fresh.get(t),
                "status": status,
                "validation": "NOT FORECAST",
                "reason": note,
                # Forecasting is deliberately absent for this device type.
                "predicted_kwh": None, "forecast_date": None,
                "score_pct": None, "n_test": 0,
            })

    return {"ok": True, "generated_at": datetime.now(),
            "groups": group_stats, "devices": rows, "n_devices": len(rows),
            "basis": (
                "Each box is compared with the median of the producing boxes in "
                "its own group. Flags are measured differences, not diagnoses: a "
                "box below its peers may be shaded, soiled, partly disconnected "
                "or reporting badly, and this cannot tell them apart. Night-time "
                "zeros are not faults - a day counts only when at least "
                f"{F.COMPLETE_DAY_MINUTES} of its minutes were recorded.")}


def _combiner_status(mean: Optional[float], days: int,
                     median: Optional[float]) -> tuple:
    """Status and the evidence behind it. Never asserts a hardware failure."""
    if days == 0:
        return "NO DATA", "No complete day of readings."
    if days < 20:
        return "INSUFFICIENT DATA", f"Only {days} complete days of history."
    if mean is None or mean < D.COMBINER_ZERO_KWH:
        measured = (f"Averages {mean:.1f} kWh on a complete day against a group "
                    f"median of {median:.0f}."
                    if (mean is not None and median) else
                    "Reports effectively nothing on a complete day.")
        return "ZERO OUTPUT", (
            measured + " The reading is the measurement; the cause is not. It "
            "could be disconnected, never instrumented, or a failed sensor on "
            "strings that are working. Worth a site check.")
    if median and mean < 0.75 * median:
        return "BELOW PEERS", (
            f"Averages {mean:.0f} kWh against a group median of {median:.0f}, "
            f"{100.0 * (median - mean) / median:.0f}% lower. Worth inspecting.")
    return "OK", (f"Within range of its group median of {median:.0f} kWh."
                  if median else "Producing.")
