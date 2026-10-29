"""
Device-wise forecasting and combiner analysis endpoints.

Separate from the plant forecast router on purpose: the plant endpoints keep
their shapes and behaviour exactly as they were, and nothing here can affect
them.

    GET  /api/v1/devices/inventory   what the database holds, fast
    POST /api/v1/devices/forecast    run every inverter (seconds, button-driven)
    GET  /api/v1/devices/forecast    the last run's results
    GET  /api/v1/devices/combiners   peer analysis across the three groups
    GET  /api/v1/devices/meters      inventory and the open reconciliation issue
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.forecast import device_service as DS
from app.forecast import devices as D
from app.forecast import features as F

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["Device forecasting"])


@router.get("/inventory")
def inventory(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Every discovered device, with freshness. Counts come from the database."""
    disc = D.discover(db)
    tables = [d["table"] for group in disc.values() for d in group]
    fresh = D.last_seen(db, tables)
    for group in disc.values():
        for d in group:
            d["last_seen"] = fresh.get(d["table"])
    return {
        "inverters": disc["inverters"],
        "combiners": disc["combiners"],
        "meters": disc["meters"],
        "counts": {k: len(v) for k, v in disc.items()},
        "complete_day_threshold_minutes": F.COMPLETE_DAY_MINUTES,
        "note": ("Device names are read from the database catalogue, not "
                 "hard-coded. Freshness is the newest row each table holds."),
    }


@router.post("/forecast")
def run_forecasts(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Backtest and forecast every inverter on its own history.

    Each device picks its own model from the same five candidates on identical
    chronological folds. The plant's winner is not inherited.
    """
    try:
        res = DS.run_device_forecasts(db)
    except Exception as exc:                      # noqa: BLE001
        logger.error("Device forecast run failed: %s", exc, exc_info=True)
        raise HTTPException(500, detail="Device forecast run failed.") from exc
    if not res.get("ok"):
        raise HTTPException(409, detail=res.get("reason", "Run not possible."))
    return res


@router.get("/forecast")
def last_forecasts() -> Dict[str, Any]:
    """
    The last run's results.

    Held in memory, so an empty answer after a backend restart means "not run
    yet", not "no devices". The UI says which.
    """
    out = DS.cached_forecasts()
    out["ok"] = True
    out["note"] = ("Results are held in memory and cleared when the backend "
                   "restarts. Run again to repopulate.")
    return out


@router.get("/combiners")
def combiners(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Peer comparison for every combiner box, by group."""
    try:
        return DS.combiner_analysis(db)
    except Exception as exc:                      # noqa: BLE001
        logger.error("Combiner analysis failed: %s", exc, exc_info=True)
        raise HTTPException(500, detail="Combiner analysis failed.") from exc


@router.get("/meters")
def meters(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Meter inventory and freshness only.

    Forecasting is deliberately not offered here. Measured over the 39 days
    both sources cover, the outgoing meter's daily export register reads a
    median 109.3% of the summed inverter output. Export cannot exceed gross
    generation, so one of the two is mis-scaled and nothing is built on either
    until that is settled. The readings are reported untouched: no correction
    factor is applied to force them to agree.
    """
    disc = D.discover(db)
    tables = [m["table"] for m in disc["meters"]]
    fresh = D.last_seen(db, tables)
    rows = []
    for m in disc["meters"]:
        t = m["table"]
        try:
            n, days, bad = db.execute(text(
                f"SELECT COUNT(*), COUNT(DISTINCT CAST(TimeCol AS DATE)), "
                f"SUM(CASE WHEN TODAY_EXPORT_ENERGY >= 1000000 THEN 1 ELSE 0 END) "
                f"FROM [{t}]")).first()
        except Exception:                          # noqa: BLE001
            n = days = bad = None
        rows.append({**m, "rows": n, "days": days,
                     "implausible_register_rows": bad,
                     "last_seen": fresh.get(t),
                     "status": "FORECAST BLOCKED",
                     "validation": "NOT FORECAST"})
    return {
        "ok": True,
        "devices": rows,
        "n_devices": len(rows),
        "blocked": True,
        "reconciliation": {
            "median_meter_over_inverter_pct": 109.3,
            "paired_days": 39,
            "explained": False,
            "detail": (
                "On the 39 days both sources cover, the outgoing meter's daily "
                "export register reads a median 109.3% of the twelve inverters "
                "summed. Export cannot exceed gross generation. About 3 points "
                "are explained by minute-sampling under-integration - each "
                "inverter's own DAILY_ENERGY register runs 2-4% above the "
                "integral - leaving roughly 6 points unexplained. A CT or PT "
                "ratio is the likeliest cause but has NOT been confirmed, and "
                "no correction has been applied to the stored readings."),
        },
    }
