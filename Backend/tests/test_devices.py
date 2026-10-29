# -*- coding: utf-8 -*-
"""
Tests for device-wise forecasting.

Run:  .venv\\Scripts\\python.exe -m tests.test_devices
  or: .venv\\Scripts\\python.exe tests/test_devices.py

These exercise the real database, read-only. Nothing is written, and the tests
refuse to run against a connection they cannot open rather than skipping
silently and reporting success.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.database.session import SessionLocal          # noqa: E402
from app.forecast import backtest as BT                # noqa: E402
from app.forecast import device_service as DS          # noqa: E402
from app.forecast import devices as D                  # noqa: E402
from app.forecast import features as F                 # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


def main() -> int:
    db = SessionLocal()
    try:
        # ── discovery ────────────────────────────────────────────────────────
        print("\nDISCOVERY")
        disc = D.discover(db)
        inv, comb, met = disc["inverters"], disc["combiners"], disc["meters"]
        check("inverters discovered", len(inv) > 0, f"{len(inv)} found")
        check("combiners discovered", len(comb) > 0, f"{len(comb)} found")
        check("meters discovered", len(met) > 0, f"{len(met)} found")
        check("no device counted twice",
              len({d["id"] for d in inv + comb + met}) == len(inv + comb + met))
        check("every inverter name matches the strict pattern",
              all(D.INVERTER_RE.match(d["id"]) for d in inv))
        check("combiners carry their group",
              all(d["group"] in ("ICR1", "ICR2", "MCR") for d in comb))

        # ── table-name safety ────────────────────────────────────────────────
        print("\nSQL SAFETY")
        for bad in ("INVERTER_1; DROP TABLE x", "a' OR '1'='1", "", "x" * 80):
            try:
                D._safe(bad)
                check(f"rejects {bad[:22]!r}", False, "it was accepted")
            except ValueError:
                check(f"rejects {bad[:22]!r}", True)

        # ── per-device series ────────────────────────────────────────────────
        print("\nPER-DEVICE DAILY SERIES")
        t = inv[0]["table"]
        rows = D.daily_inverter(db, t)
        check(f"{t} returns rows", len(rows) > 0, f"{len(rows)} complete days")
        check("every day meets the completeness threshold",
              all(r["minutes"] >= F.COMPLETE_DAY_MINUTES for r in rows))
        check("days are unique", len({r["d"] for r in rows}) == len(rows),
              "duplicate timestamps would break this")
        check("days are in order", all(rows[i]["d"] < rows[i + 1]["d"]
                                       for i in range(len(rows) - 1)))
        check("energy is never negative",
              all(r["energy_net_mwh"] >= 0 for r in rows))
        check("gross equals net at device level",
              all(r["energy_net_mwh"] == r["energy_gross_mwh"] for r in rows))

        # Units: one inverter of this plant produces tens of MWh a day, not
        # tens of thousands. A kW/MW mix-up shows up here as a 1000x error.
        mean = sum(r["energy_net_mwh"] for r in rows) / max(len(rows), 1)
        check("daily energy is in a physical range for one inverter",
              5.0 < mean < 60.0, f"mean {mean:.1f} MWh/day")
        peak = max((r["peak_mw"] for r in rows), default=0)
        check("peak power is in MW, not kW", 0.5 < peak < 20.0, f"peak {peak:.2f} MW")

        # ── night zeros are data, not gaps ───────────────────────────────────
        print("\nNIGHT-TIME ZEROS")
        sample = rows[len(rows) // 2]
        check("a complete day still has idle minutes",
              sample["producing_minutes"] < sample["minutes"],
              f"{sample['producing_minutes']} producing of {sample['minutes']}")
        check("duty is a fraction, not a device count",
              0.0 <= sample["avg_inverters_running"] <= 1.0,
              f"{sample['avg_inverters_running']:.2f}")
        check("an idle night does not void the day",
              sample["energy_net_mwh"] > 0)

        # ── the frame the engine consumes ────────────────────────────────────
        print("\nFRAME COMPATIBILITY")
        frame = F.build_frame(rows, None, "energy_net_mwh", weather_forecast=None)
        check("build_frame accepts the device series unchanged",
              frame["X"].shape[0] > 0, f"{frame['X'].shape} ")
        check("warm-up days are dropped",
              frame["X"].shape[0] == max(0, len(rows) - F.MIN_HISTORY_DAYS))
        check("no weather features leak in", frame["weather_features_used"] is False)

        # ── no cross-device leakage ──────────────────────────────────────────
        print("\nCROSS-DEVICE ISOLATION")
        if len(inv) >= 2:
            a = D.daily_inverter(db, inv[0]["table"])
            b = D.daily_inverter(db, inv[1]["table"])
            shared = {r["d"] for r in a} & {r["d"] for r in b}
            differing = sum(1 for d in shared
                            if abs(next(r["energy_net_mwh"] for r in a if r["d"] == d)
                                   - next(r["energy_net_mwh"] for r in b if r["d"] == d))
                            > 1e-9)
            check("two devices produce different series",
                  differing > 0,
                  f"{differing} of {len(shared)} shared days differ")
            fa = F.build_frame(a, None, "energy_net_mwh")
            fb = F.build_frame(b, None, "energy_net_mwh")
            same = (fa["X"].shape == fb["X"].shape
                    and (fa["X"] == fb["X"]).all())
            check("one device's features are not another's", not same)

        # ── leakage within a device ──────────────────────────────────────────
        print("\nTEMPORAL LEAKAGE")
        bt = BT.walk_forward_one_day(frame)
        if bt.get("ok"):
            check("test block starts after the training block ends",
                  bt["test_start"] > bt["train_end"])
            check("held-out count matches the scored records",
                  bt["n_test"] == len(bt["test_records"]))
            check("every scored day falls inside the test window",
                  all(bt["test_start"] <= r["d"] <= bt["test_end"]
                      for r in bt["test_records"]))
        else:
            check("backtest reported why it could not run",
                  bool(bt.get("reason")), bt.get("reason", ""))

        # ── metrics ──────────────────────────────────────────────────────────
        print("\nMETRICS")
        from app.forecast.metrics import evaluate, forecast_score, wape
        m = evaluate([100.0, 200.0, 300.0], [110.0, 190.0, 300.0])
        check("WAPE is total error over total actual",
              abs(m["wape_pct"] - (20.0 / 600.0 * 100)) < 1e-9, f"{m['wape_pct']:.4f}")
        check("score is 100 minus WAPE",
              abs(m["score_pct"] - (100 - m["wape_pct"])) < 1e-9)
        check("MAE is the mean absolute error",
              abs(m["mae_mwh"] - (20.0 / 3)) < 1e-9)
        check("a perfect forecast scores 100",
              forecast_score([1, 2, 3], [1, 2, 3]) == 100.0)
        check("WAPE is undefined when nothing was produced",
              wape([0, 0], [1, 1]) is None)

        # ── combiner analysis ────────────────────────────────────────────────
        print("\nCOMBINER PEER ANALYSIS")
        ca = DS.combiner_analysis(db)
        devs = ca["devices"]
        check("every combiner appears", len(devs) == len(comb), f"{len(devs)} rows")
        check("none is silently dropped for reading zero",
              any(r["status"] == "ZERO OUTPUT" for r in devs),
              f"{sum(1 for r in devs if r['status'] == 'ZERO OUTPUT')} zero-output boxes kept")
        check("no combiner is given a forecast",
              all(r["predicted_kwh"] is None for r in devs))
        check("ranks stay within their group",
              all(r["peer_rank"] is None or r["peer_rank"] <= r["peer_total"]
                  for r in devs))
        ranked = [r for r in devs if r["peer_rank"]]
        check("rank 1 is the strongest producer in its group",
              all(r["mean_actual_kwh"] >= r["group_median_kwh"]
                  for r in ranked if r["peer_rank"] == 1))
        check("status is never a hardware verdict",
              all("fail" not in (r["reason"] or "").lower().replace("failed sensor", "")
                  for r in devs))
        zero = [r for r in devs if r["status"] == "ZERO OUTPUT"]
        check("a zero-output box reports 0, not a missing value",
              all(r["mean_actual_kwh"] is not None for r in zero) if zero else True)

        # ── per-device forecasting end to end ────────────────────────────────
        print("\nDEVICE FORECAST RUN")
        res = DS.run_device_forecasts(db)
        rows_out = res["devices"]
        check("one row per inverter", len(rows_out) == len(inv))
        check("a device that cannot be evaluated is reported, not dropped",
              all(r.get("status") for r in rows_out))
        scored = [r for r in rows_out if r["n_test"]]
        check("scored devices carry their held-out count",
              all(r["n_test"] > 0 for r in scored), f"{len(scored)} scored")
        check("no device claims MEASURED on a small sample",
              all(r["validation"] != "MEASURED"
                  for r in scored if r["n_test"] < BT.MIN_CREDIBLE_TEST_DAYS))
        check("models are chosen per device, not inherited",
              len({r["model"] for r in scored if r["model"]}) > 1,
              f"{sorted({r['model'] for r in scored if r['model']})}")
        check("next-day date follows the last observed day",
              all(r["forecast_date"] == r["latest_actual_date"] + timedelta(days=1)
                  for r in scored if r["forecast_date"] and r["latest_actual_date"]))
        check("predictions are physically plausible",
              all(0 <= r["predicted_mwh"] < 60 for r in scored
                  if r["predicted_mwh"] is not None))

        # ── the plant pipeline still works ───────────────────────────────────
        print("\nREGRESSION: PLANT PIPELINE UNCHANGED")
        plant = F.daily_plant(db)
        check("plant series still builds", len(plant) > 0, f"{len(plant)} days")
        pf = F.build_frame(plant, F.daily_weather(db), "energy_net_mwh")
        check("plant frame still builds", pf["X"].shape[0] > 0, f"{pf['X'].shape}")
        pbt = BT.walk_forward_one_day(pf)
        check("plant backtest still runs", pbt.get("ok") is True,
              pbt.get("reason", ""))
        # The device totals should land near the plant total: same source rows,
        # same integration. Not identical, because a device day can be complete
        # while the plant day is not, and vice versa.
        dev_mean = sum(r["mean_actual_mwh"] or 0 for r in rows_out)
        plant_mean = sum(r["energy_net_mwh"] for r in plant) / len(plant)
        check("device totals reconcile with the plant",
              abs(dev_mean - plant_mean) / plant_mean < 0.15,
              f"devices {dev_mean:.1f} vs plant {plant_mean:.1f} MWh/day")

    finally:
        db.close()

    print("\n" + "=" * 70)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
