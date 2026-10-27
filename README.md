# Chammu Solar Plant — Monitoring

Forecasting dashboard for the Chammu plant. Two halves, one repository:

```
Layout/     React frontend (Vite)       — http://localhost:5173
Backend/    FastAPI API                 — http://localhost:8001
```

Independent of the Kalyon Niğde project: separate code, separate database,
separate repository. Nothing here affects it.

---

## Running it

Backend, in its own terminal:

```powershell
cd Backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Frontend, in another:

```powershell
cd Layout; npm run dev
```

First time on a new machine:

```powershell
cd Backend; python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..\Layout; npm install
```

Neither `.env` is committed. Copy `.env.example` in each folder and fill it in.

### Connecting to SQL Server

`Backend/.env` needs `DB_SERVER` and `DB_DATABASE`. Authentication follows
from whether you set a username:

| | |
|---|---|
| Windows Authentication | leave `DB_USERNAME` and `DB_PASSWORD` blank |
| SQL Server Authentication | set both |

Windows auth is preferable wherever it works, because nothing has to be
stored. An instance in mixed mode reached with a SQL login cannot be opened
with a trusted connection at all, so on those machines the second row is not a
preference. Prefer a least-privilege login scoped to this database over `sa`.

The startup log names which mode was used, so a connection that opened a
different way than intended is visible immediately rather than surfacing later
as a permissions error.

---

## Scope

This deployment serves **forecasting only**. `Layout/src/config/features.js`
lists the enabled pages; every other page, route and component is still in the
codebase and is re-enabled by adding its key to that list. On the API side,
`ENABLE_ALL_FEATURES=1` registers the reporting routers again.

The other pages are switched off because they query tables CHAMMU does not
have — `PPC`, `POWER_GRAPH`, `INVERTER_DAILY_GEN`, `TEMPERATURE_REPORT`,
`Alarms`, the tracker tables. Leaving them reachable would return 500s that
look like a broken server rather than a feature not yet wired up for this site.

---

## How this plant differs from the reference plant

The backend began as a copy of the Kalyon Niğde API, and CHAMMU's SCADA schema
is not the same. What changed, and what it costs:

| | Kalyon | CHAMMU |
|---|---|---|
| Inverters | `INVERTER_01`…`INVERTER_24` | `INVERTER_1`…`INVERTER_12` |
| Plant power | `PPC.GRID_ACTIVE_POWER_MEASURED` | summed from the 12 inverters |
| Irradiance | `WMS.AVG_GTI_IRRADIATION` | `WMS.GTI_RADIATION` |
| Combiners | `INVERTERn_SMB` | `ICR1_SMB*`, `ICR2_SMB*`, `MCR_SMB*` |

Fleet size and name padding are configurable (`INVERTER_COUNT`,
`INVERTER_NAME_PAD`), so neither is baked into the SQL.

### Three limitations that follow

**Generation is gross, not net.** It is summed at the inverters, so transformer
losses, station auxiliaries and night-time import are all downstream and
invisible. Real export at the point of connection is lower.

**Curtailment cannot be detected.** There is no set-point channel on this
plant, so `curtailment_detectable` is false. That means *not measurable*, not
*did not happen* — a curtailed day looks to the model like a cloudy one.

**The timezone of `TimeCol` is unverified.** On Kalyon it was proven to be UTC
by two independent measurements. No equivalent check has been run here, so days
are grouped by `TimeCol`'s own date. If the clock is local time and the site is
far from UTC, a solar day may straddle two dates and daily totals will be wrong
at the boundaries.

`WEATHER_LAT` and `INSTALLED_CAPACITY_MW` are deliberately unset. An unset
latitude disables the clear-sky quality rule rather than checking this plant's
sky against another site's; an unset capacity leaves Performance Ratio blank
rather than deriving it from a guessed nameplate.

---

## The data is not sufficient to validate a forecast

| | |
|---|---|
| Record | 2025-04-15 → 2025-07-19 |
| Distinct days | 44, across an 86-day span — collection was intermittent |
| Complete days (≥1380 min) | 42 |
| With usable weather | 37 |
| Usable for supervised training | 35 |

A run is possible and the page shows next-day, 7-day and 30-day volumes, but
the held-out test block is **7 days**. The reliability banner reads
**INSUFFICIENT DATA** and `target_met` stays false regardless of the score,
because clearing a target on seven settled days is not clearing it.

Only the next-day figure is measured by the backtest. Beyond that the model
feeds its own output back as the next lag, so with no future weather the series
carries no new information and decays toward the recent average — a reasonable
planning volume, not a forecast.

**Roughly 6–12 months of continuous logging is needed before any accuracy claim
can be made.** No code change substitutes for it.
