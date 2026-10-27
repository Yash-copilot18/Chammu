"""
Forecasting & deviation-detection API.

  GET  /forecast/status        what the archive supports, before anything runs
  POST /forecast/run           train, validate, select, predict, persist
  GET  /forecast/latest        the most recent stored run and its metrics
  GET  /forecast/next-day      tomorrow's prediction with its interval
  GET  /forecast/history       actual vs predicted, for the chart
  GET  /forecast/deviations    deviations with measured evidence + hypotheses
  POST /forecast/reconcile     fill in actuals for days that have completed

Training is synchronous: on this archive a full pass is a few seconds, so the
background-job machinery used by the heavier exports would add moving parts
without buying anything. If the archive grows by an order of magnitude this is
the one endpoint to move onto `export_jobs`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.forecast import features as F
from app.forecast import cache as C
from app.forecast import service as S
from app.forecast import weather as W
from app.forecast import backtest as B
from app.models.forecasts import ForecastPoint, ForecastRun

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/forecast", tags=["Forecasting"])


class RunRequest(BaseModel):
    target: str = Field(default="energy_net_mwh",
                        description="energy_net_mwh or energy_gross_mwh")
    ceiling_test: bool = Field(
        default=False,
        description=("Substitute MEASURED weather for a forecast to establish the "
                     "best score a perfect weather feed could reach. Leaks future "
                     "information by design; the result is never saved."))


@router.get("/status")
def forecast_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Data availability, weather configuration and the last run — no training."""
    cov = F.coverage_summary(db)
    last = (db.query(ForecastRun).order_by(ForecastRun.id.desc()).first())
    return {
        "coverage": cov,
        "weather": W.status(),
        "target_score_pct": S.TARGET_SCORE_PCT,
        "deviation_threshold_pct": S.DEVIATION_PCT_THRESHOLD,
        "deviation_min_mwh": S.DEVIATION_MIN_MWH,
        "last_run": _run_summary(last) if last else None,
        "data_freshness": {
            "plant_last_record": cov["plant_last"],
            "weather_last_record": cov["weather_last"],
            "note": ("The dashboard reports what the database holds. If the last "
                     "record is not recent, the feed has stopped, not the model."),
        },
    }


@router.post("/run")
def forecast_run(payload: RunRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Train, validate chronologically, select, predict forward and persist."""
    if payload.target not in ("energy_net_mwh", "energy_gross_mwh"):
        raise HTTPException(status_code=400, detail="Unknown target.")
    try:
        res = S.run_training(db, target=payload.target, ceiling_test=payload.ceiling_test)
    except Exception as exc:                            # noqa: BLE001
        logger.error("Forecast run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Forecast run failed.")
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("reason", "Run not possible."))
    return res


@router.get("/latest")
def forecast_latest(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The most recent stored run, with its full leaderboard and breakdowns."""
    run = db.query(ForecastRun).order_by(ForecastRun.id.desc()).first()
    if not run:
        return {"available": False,
                "reason": "No forecast has been run yet. Use Run forecast."}
    out = _run_summary(run)
    out["available"] = True
    out["detail"] = json.loads(run.detail_json) if run.detail_json else {}
    return out


@router.get("/next-day")
def forecast_next_day(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    The next predicted day that has not yet happened.

    "Tomorrow" is relative to the data, not the wall clock: this archive ends in
    May 2024, so the honest answer is the first unreconciled predicted day, with
    its date stated.
    """
    run = db.query(ForecastRun).order_by(ForecastRun.id.desc()).first()
    if not run:
        return {"available": False, "reason": "No forecast has been run yet."}
    forward = (db.query(ForecastPoint)
                 .filter(ForecastPoint.run_id == run.id)
                 # `== False` not `.is_(False)`: SQL Server rejects "IS 0".
                 .filter(ForecastPoint.is_backtest == False)   # noqa: E712
                 .order_by(ForecastPoint.target_date.asc())
                 .all())
    if not forward:
        return {"available": False, "reason": "The last run stored no points."}
    pt = forward[0]
    return {
        "available": True,
        "date": pt.target_date,
        "predicted_mwh": pt.predicted_mwh,
        "lower_mwh": pt.lower_mwh,
        "upper_mwh": pt.upper_mwh,
        "model": run.model_name,
        "made_on": pt.made_on,
        "score_pct": run.score_pct,
        "wape_pct": run.wape_pct,
        "target_score_pct": run.target_score_pct,
        "target_met": bool(run.target_met),
        "weather_forecast_used": bool(run.weather_forecast_used),
        "reliability": _reliability(run),
        "horizons": _horizon_totals(forward),
    }


def _horizon_totals(forward: List[ForecastPoint]) -> Dict[str, Any]:
    """
    Weekly and monthly totals, summed from the daily forward predictions.

    Each block states how many days it actually covers. A run that produced
    only twelve forward days cannot report a thirty-day total, and says so
    rather than quietly summing twelve and calling it a month.

    `validated` is the important field. Only horizon 1 is measured by the
    backtest. Day 2 onward is recursive - the model feeds its own output back
    in as the next lag - so beyond roughly a week the series carries no new
    information and settles toward the recent average. Useful for planning
    volumes; not a forecast in the sense the next-day figure is.
    """
    rows = sorted(forward, key=lambda p: p.target_date)

    def block(days: int) -> Dict[str, Any]:
        take = rows[:days]
        have = len(take)
        total = sum(float(p.predicted_mwh or 0.0) for p in take)
        lowers = [p.lower_mwh for p in take]
        uppers = [p.upper_mwh for p in take]
        return {
            "days_requested": days,
            "days_available": have,
            "complete": have >= days,
            "total_mwh": round(total, 1) if have else None,
            "mean_daily_mwh": round(total / have, 1) if have else None,
            # Interval bounds are summed, which assumes the daily errors move
            # together. They do not, so this band is WIDER than the truth -
            # erring toward overstating uncertainty rather than understating it.
            "lower_mwh": round(sum(l for l in lowers if l is not None), 1)
                         if have and all(l is not None for l in lowers) else None,
            "upper_mwh": round(sum(u for u in uppers if u is not None), 1)
                         if have and all(u is not None for u in uppers) else None,
            "start": take[0].target_date if have else None,
            "end": take[-1].target_date if have else None,
            "validated": days == 1,
            "basis": ("Measured by the held-out backtest." if days == 1 else
                      "Recursive extrapolation. Horizon 1 is the only one the "
                      "backtest scores; these days are not independently "
                      "validated and decay toward the recent average."),
        }

    out = {"next_day": block(1)}
    for label, days in S.HORIZON_AGGREGATES.items():
        out[label] = block(days)
    return out


@router.get("/history")
def forecast_history(limit: int = Query(120, ge=1, le=1000),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Actual vs predicted, newest last — the series behind the chart."""
    run = db.query(ForecastRun).order_by(ForecastRun.id.desc()).first()
    if not run:
        # No model has been trained, but the plant's own record exists and is
        # worth showing: an empty chart looks like a broken feed, when in fact
        # the generation history is right there. These are MEASURED daily totals
        # from the inverter tables, with no predicted series beside them -
        # `predicted_mwh` is null on every point, so nothing here can be read as
        # a forecast.
        measured = F.daily_plant(db)[-limit:]
        rows = [{
            "date": r["d"],
            "predicted_mwh": None,
            "lower_mwh": None,
            "upper_mwh": None,
            "actual_mwh": round(r["energy_net_mwh"], 2),
            "deviation_mwh": None,
            "deviation_pct": None,
            "status": None,
            "is_backtest": False,
        } for r in measured]
        return {
            "points": rows,
            "n_points": len(rows),
            "n_reconciled": 0,
            "measured_only": True,
            "note": ("No forecast has been run, so no predicted series exists. "
                     "The line shown is MEASURED daily generation, summed from "
                     "the inverters. Nothing on this chart is a prediction."),
        }
    pts = (db.query(ForecastPoint)
             .filter(ForecastPoint.run_id == run.id)
             .order_by(ForecastPoint.target_date.desc())
             .limit(limit).all())
    rows = [{
        "date": p.target_date,
        "predicted_mwh": p.predicted_mwh,
        "lower_mwh": p.lower_mwh,
        "upper_mwh": p.upper_mwh,
        "actual_mwh": p.actual_mwh,
        "deviation_mwh": p.deviation_mwh,
        "deviation_pct": p.deviation_pct,
        "status": p.status,
        "is_backtest": bool(p.is_backtest),
    } for p in reversed(pts)]
    scored = [r for r in rows if r["actual_mwh"] is not None]
    return {
        "points": rows,
        "n_points": len(rows),
        "n_reconciled": len(scored),
        "run_id": run.id,
        "note": ("Points marked is_backtest were predicted by a model that had not "
                 "seen that day - the held-out test block. Points without an actual "
                 "are days that have not completed yet."),
    }


@router.get("/deviations")
def forecast_deviations(limit: int = Query(50, ge=1, le=500),
                        only_alerts: bool = Query(True),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Deviations with the measured evidence beside them.

    `hypotheses` are candidate explanations, each tied to a measured value.
    They are not conclusions: this database holds no dispatch log, work orders
    or outage records, so a cause cannot be confirmed from it.
    """
    run = db.query(ForecastRun).order_by(ForecastRun.id.desc()).first()
    q = db.query(ForecastPoint).filter(ForecastPoint.actual_mwh.isnot(None))
    if run:
        q = q.filter(ForecastPoint.run_id == run.id)
    if only_alerts:
        q = q.filter(ForecastPoint.status.in_(("UNDER", "OVER")))
    pts = q.order_by(ForecastPoint.target_date.desc()).limit(limit).all()

    out: List[Dict[str, Any]] = []
    for p in pts:
        ev = json.loads(p.evidence_json) if p.evidence_json else {}
        out.append({
            "date": p.target_date,
            "predicted_mwh": p.predicted_mwh,
            "actual_mwh": p.actual_mwh,
            "deviation_mwh": p.deviation_mwh,
            "deviation_pct": p.deviation_pct,
            "status": p.status,
            "is_backtest": bool(p.is_backtest),
            "evidence": ev,
            "hypotheses": S.hypotheses(ev, p.status or ""),
        })
    return {
        "deviations": out,
        "threshold_pct": S.DEVIATION_PCT_THRESHOLD,
        "threshold_min_mwh": S.DEVIATION_MIN_MWH,
        "disclaimer": ("Evidence is measured. Causes are hypotheses until "
                       "confirmed against records this database does not hold."),
    }


@router.post("/reconcile")
def forecast_reconcile(lookback_days: int = Query(60, ge=1, le=400),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Fill in actuals and deviations for predicted days that have completed."""
    try:
        return S.reconcile(db, lookback_days=lookback_days)
    except Exception as exc:                            # noqa: BLE001
        logger.error("Reconciliation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Reconciliation failed.")


@router.post("/weather/ingest")
def forecast_weather_ingest(start: str = Query(...), end: str = Query(...),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Backfill the archived day-ahead forecast cache.

    Gated twice over: a provider must be configured AND WEATHER_ARCHIVE_ENABLED=1.
    Coordinates come from WEATHER_LAT / WEATHER_LON; none is assumed.
    """
    from datetime import date as _date
    try:
        s_, e_ = _date.fromisoformat(start), _date.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD.")
    res = S.ingest_weather_archive(db, s_, e_)
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("reason"))
    return res


@router.get("/weather/cache")
def forecast_weather_cache(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """What the forecast cache holds, and whether anything in it could leak."""
    ok, msg = C.verify_no_leakage(db)
    return {**C.coverage(db), "leakage_check_passed": ok, "leakage_check": msg}


@router.post("/acceptance")
def forecast_acceptance(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Score the PRE-REGISTERED model over every day the archive allows.

    No selection happens here: the model and every choice around it were fixed
    in docs/forecast-prereg.md before this was first run, so each scored day is
    genuinely out of sample. Baselines are reported for comparison only.
    """
    res = S.run_acceptance(db)
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("reason"))
    return res


# ── helpers ──────────────────────────────────────────────────────────────────
def _reliability(run: ForecastRun) -> Dict[str, Any]:
    """
    A plain verdict on whether the forecast should be acted upon.

    Driven entirely by the measured held-out score against the agreed target.
    It never reports the target as met when it is not.
    """
    score = run.score_pct
    if score is None:
        return {"level": "UNKNOWN", "reason": "No score was measured."}

    # A score from a handful of days is dominated by which days happened to
    # land in the test block, and must never be presented as a validated
    # result - not even when the number clears the target. Sample size is
    # therefore checked BEFORE the target, so a lucky small-sample pass cannot
    # be reported as VALIDATED.
    n_test = int(run.n_test or 0)
    if n_test < B.MIN_CREDIBLE_TEST_DAYS:
        return {
            "level": "INSUFFICIENT DATA",
            "reason": (f"Scored on only {n_test} held-out day"
                       f"{'' if n_test == 1 else 's'} — fewer than the "
                       f"{B.MIN_CREDIBLE_TEST_DAYS} needed for the figure to be "
                       f"meaningful. The measured score is {score:.1f}%, but on "
                       f"this few days it reflects which days fell in the test "
                       f"block as much as the model. Treat as a provisional "
                       f"indication, not an accuracy claim."),
            "measured_score_pct": round(score, 1),
            "n_test": n_test,
        }

    if run.target_met:
        return {"level": "VALIDATED",
                "reason": f"Held-out score {score:.1f}% meets the "
                          f"{run.target_score_pct:.0f}% target, measured on "
                          f"{n_test} held-out days."}
    gap = (run.target_score_pct or 0) - score
    return {
        "level": "LIMITED",
        "reason": (f"Held-out score {score:.1f}% is {gap:.1f} points below the "
                   f"{run.target_score_pct:.0f}% target, measured on {n_test} "
                   f"held-out days. Use for direction, not for commitment."),
    }


def _run_summary(run: ForecastRun) -> Dict[str, Any]:
    return {
        "run_id": run.id,
        "created_at": run.created_at,
        "target": run.target,
        "model": run.model_name,
        "selection_basis": run.selection_basis,
        "train_start": run.train_start, "train_end": run.train_end,
        "test_start": run.test_start, "test_end": run.test_end,
        "n_train": run.n_train, "n_test": run.n_test, "folds": run.folds,
        "score_pct": run.score_pct, "wape_pct": run.wape_pct,
        "mae_mwh": run.mae_mwh, "rmse_mwh": run.rmse_mwh,
        "bias_mwh": run.bias_mwh, "r2": run.r2,
        "target_score_pct": run.target_score_pct,
        "target_met": bool(run.target_met),
        "weather_forecast_used": bool(run.weather_forecast_used),
        "reliability": _reliability(run),
    }
