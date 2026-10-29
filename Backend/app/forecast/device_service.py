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
import os
import statistics
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.forecast import backtest as BT
from app.forecast import devices as D
from app.forecast import features as F
from app.forecast import models as M
from app.forecast import service as S

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# device id -> result dict. Survives requests, not restarts.
_forecast_cache: Dict[str, Any] = {"generated_at": None, "devices": [],
                                   "running": False, "total": 0}


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

    interval = BT.conformal_interval(bt["test_records"])
    half = (interval or {}).get("half_width_mwh")

    # The forward series comes from the PLANT's own recursion, called directly
    # rather than reimplemented here. One implementation means a device horizon
    # and the plant horizon cannot drift apart, and it inherits the same
    # property that matters: each step feeds its own output back as the next
    # lag, so nothing from the future enters at any horizon.
    forward = S._predict_forward(frame, bt, plant_rows, interval)
    nxt = forward[0]["predicted_mwh"] if forward else 0.0

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
        "horizons": _horizons(forward, n_test),
        "daily": [{"date": p["date"], "horizon": p["horizon"],
                   "predicted_mwh": round(p["predicted_mwh"], 2),
                   "lower_mwh": round(p["lower_mwh"], 2) if p["lower_mwh"] else None,
                   "upper_mwh": round(p["upper_mwh"], 2) if p["upper_mwh"] else None}
                  for p in forward],
    }


# Horizon blocks exposed per device. Day 1 is the product; the rest are
# planning volumes, and the labels below say which is which.
HORIZON_BLOCKS = {"next_day": 1, "next_7_days": 7, "next_30_days": 30}


def _horizons(forward: List[Dict[str, Any]], n_test: int) -> Dict[str, Any]:
    """
    Cumulative totals per horizon, each carrying what the backtest knows.

    Only horizon 1 is measured. Beyond it the model consumes its own output
    with no new information entering, so the series decays toward the device's
    recent average - useful as a planning volume, not a forecast, and labelled
    that way rather than left for the reader to infer.
    """
    out: Dict[str, Any] = {}
    for label, days in HORIZON_BLOCKS.items():
        take = forward[:days]
        have = len(take)
        total = sum(p["predicted_mwh"] for p in take)
        lo = [p["lower_mwh"] for p in take]
        hi = [p["upper_mwh"] for p in take]
        measured = days == 1 and n_test >= BT.MIN_CREDIBLE_TEST_DAYS
        out[label] = {
            "days_requested": days,
            "days_available": have,
            "complete": have >= days,
            "total_mwh": round(total, 2) if have else None,
            "mean_daily_mwh": round(total / have, 2) if have else None,
            # Summing the daily bands assumes the errors move together. They do
            # not, so this band is wider than the truth - erring toward
            # overstating uncertainty rather than understating it.
            "lower_mwh": round(sum(x for x in lo if x is not None), 2)
                         if have and all(x is not None for x in lo) else None,
            "upper_mwh": round(sum(x for x in hi if x is not None), 2)
                         if have and all(x is not None for x in hi) else None,
            "start": take[0]["date"] if have else None,
            "end": take[-1]["date"] if have else None,
            "validated": measured,
            "label": ("MEASURED" if measured
                      else "PROVISIONAL" if days == 1 else "PLANNING ESTIMATE"),
            "basis": (
                f"Measured by the day-ahead backtest on {n_test} held-out "
                f"day{'' if n_test == 1 else 's'}."
                + ("" if measured else " Below the "
                   f"{BT.MIN_CREDIBLE_TEST_DAYS} needed to be stable.")
                if days == 1 else
                f"Not evaluated at this range. Each day after the first is "
                f"predicted from the previous day's prediction with no new "
                f"information entering, so the series settles toward this "
                f"device's recent average. Use as an expected volume."),
        }
    return out


def _worker_init() -> None:
    """
    Pin each worker to one thread.

    The forest and the booster are built with n_jobs=-1, which is right when one
    device is fitted at a time but wrong once twelve are fitted at once: every
    worker would start a thread per core and the machine would spend its time
    context-switching between 144 of them. One thread per worker, many workers,
    is faster than the reverse and gives identical numbers either way.
    """
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS"):
        os.environ[var] = "1"


def _worker_count(n_devices: int) -> int:
    """
    How many devices to fit at once.

    Unset or 0 means automatic. 1 forces the original serial path, which is the
    escape hatch if a machine ever misbehaves under the pool.
    """
    want = 0
    raw = os.getenv("DEVICE_FORECAST_WORKERS", "").strip()
    if raw:
        try:
            want = int(raw)
        except ValueError:
            logger.warning("DEVICE_FORECAST_WORKERS=%r is not a number; "
                           "using automatic instead.", raw)
            want = 0
        if want == 1:
            return 1
    if want <= 0:
        # Leave one core for the server itself, so the API keeps answering
        # while a run is in progress.
        want = max(1, (os.cpu_count() or 2) - 1)
    return max(1, min(want, n_devices))


def run_device_forecasts(db: Session) -> Dict[str, Any]:
    """
    Forecast every discovered inverter.

    The SQL is cheap (about a fifth of a second per device) and the model
    fitting is not: each device fits all five candidates across every fold, so
    even a device that ends up choosing Persistence pays for the forest and the
    booster first. Those fits share nothing - no session, no state, one device's
    history each - so they are run in parallel across processes.

    The arithmetic is untouched. Each device sees exactly the data it saw
    before, the models carry fixed seeds, and the rows are re-ordered back into
    discovery order before they are published, so the output is identical to the
    serial run; only the waiting is shorter.
    """
    with _lock:
        if _forecast_cache.get("running"):
            return {"ok": False, "reason": "A device run is already in progress."}
        _forecast_cache["running"] = True
    try:
        inv = D.discover(db)["inverters"]
        fresh = D.last_seen(db, [d["table"] for d in inv])
        with _lock:
            # Clear previous results at the start. A run takes a while, and
            # leaving the old rows in place while it works makes the progress
            # count read as "11 of 12 done" from the first second.
            _forecast_cache["devices"] = []
            _forecast_cache["total"] = len(inv)

        # Read every device's history up front, on this thread. The database
        # work is serial either way and a worker process has no session.
        work = [(device, D.daily_inverter(db, device["table"])) for device in inv]

        done: Dict[str, Dict[str, Any]] = {}

        def _publish(device: Dict[str, Any], row: Dict[str, Any]) -> None:
            """Record one finished device and republish in discovery order."""
            row["last_seen"] = fresh.get(device["table"])
            done[device["id"]] = row
            ordered = [done[d["id"]] for d, _ in work if d["id"] in done]
            with _lock:
                _forecast_cache["devices"] = ordered
            logger.info("device forecast %s: %s (%s complete days)",
                        device["id"], row["status"], row["complete_days"])

        workers = _worker_count(len(work))
        ran_parallel = False
        if workers > 1:
            try:
                with ProcessPoolExecutor(max_workers=workers,
                                         initializer=_worker_init) as pool:
                    futures = {pool.submit(_forecast_one, rows, device): device
                               for device, rows in work}
                    for future in as_completed(futures):
                        device = futures[future]
                        _publish(device, future.result())
                ran_parallel = True
                logger.info("device forecasts fitted across %d workers", workers)
            except Exception as exc:  # noqa: BLE001
                # A machine that cannot spawn processes - a locked-down host, a
                # frozen build - must still produce the forecast, just slower.
                # Anything already published is discarded so the serial pass is
                # the only thing that can reach the cache.
                logger.warning("Parallel device run unavailable (%s). "
                               "Falling back to one device at a time.", exc)
                done.clear()

        if not ran_parallel:
            for device, rows in work:
                _publish(device, _forecast_one(rows, device))

        out = [done[device["id"]] for device, _ in work]
        result = {"ok": True, "generated_at": datetime.now(),
                  "devices": out, "n_devices": len(out)}
        with _lock:
            _forecast_cache.update({"generated_at": result["generated_at"],
                                    "devices": out, "running": False,
                                    "total": len(inv)})
        return result
    except Exception:
        with _lock:
            _forecast_cache["running"] = False
        raise


def cached_forecasts() -> Dict[str, Any]:
    with _lock:
        devices = list(_forecast_cache["devices"])
        return {"generated_at": _forecast_cache["generated_at"],
                "devices": devices,
                "n_devices": len(devices),
                # How many the run will produce in total, so a caller polling
                # mid-run can show "4 of 12" rather than a bare count.
                "total": int(_forecast_cache.get("total") or len(devices)),
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
