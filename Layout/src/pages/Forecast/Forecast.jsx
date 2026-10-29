// src/pages/Forecast/Forecast.jsx
//
// Day-ahead generation forecasting and deviation detection.
//
// The page is built around one principle: never show an accuracy figure without
// the window it was measured on, and never show the agreed target as met unless
// the held-out test says so. The score banner is green only when the backend
// reports target_met — the colour is driven by measurement, not by hope.

import React, { useState, useEffect, useCallback } from 'react'
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ReferenceLine,
} from 'recharts'
import {
  KpiCard, PageHeader, DataTable, Skeleton, Spinner,
  StatusBadge, InfoPanel, AlertCard, FactGrid,
} from '../../components/Common'
import { useApp } from '../../utils/AppContext'
import DeviceSection from '../../components/Devices/DeviceSection'
import {
  fetchForecastStatus, runForecast, fetchForecastLatest, fetchForecastNextDay,
  fetchForecastHistory, fetchForecastDeviations, reconcileForecast,
} from '../../services/api'

const fmt = (v, d = 0) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })

const shortDate = (s) => (s ? String(s).slice(0, 10) : '—')

// Status colouring now lives in the shared <StatusBadge>, so OK / UNDER / OVER /
// UNRELIABLE read identically here and anywhere else they appear.

/**
 * One predicted-volume block: total energy, the days it covers, and whether
 * the backtest actually measured that horizon.
 *
 * The validated/unvalidated badge sits on the card rather than in a footnote
 * below, because the number is what gets read aloud in a meeting and the
 * caveat has to travel with it. A block the run could not fill reports how
 * many days it does cover instead of quietly summing fewer and calling it a
 * month.
 */
/** Badge colour per evidence state. Amber for thin, grey for untested — green
 *  is reserved for a horizon the backtest actually covered properly. */
const HORIZON_TONE = {
  measured: 'green',
  measured_thin: 'amber',
  not_measured: 'neutral',
}

function HorizonCard({ label, h, fallback = null }) {
  const total = h?.total_mwh ?? null
  const shown = total != null ? fmt(total) : (fallback ?? '—')

  return (
    <div className="kpi-card">
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <span className="font-mono text-[10px] text-ge-text3 uppercase tracking-widest">
          {label}
        </span>
        {/* Three states, not two. The badge is driven by `quality` from the
            API: a figure the backtest measured well, one it measured on too
            few days, and one it never reached. The sample size sits directly
            underneath, because a badge alone invites the reader to assume the
            number behind it was tested more than it was. */}
        {h && (
          <StatusBadge tone={HORIZON_TONE[h.quality] || 'neutral'}>
            {h.label || (h.validated ? 'MEASURED' : 'PLANNING ESTIMATE')}
          </StatusBadge>
        )}
      </div>

      <div className="font-mono text-[22px] font-semibold text-ge-text1 leading-none mb-1.5">
        {shown}
        <span className="text-[12px] text-ge-text3 ml-1">MWh</span>
      </div>

      {h && total != null && (
        <div className="text-[10px] font-mono text-ge-text3 leading-relaxed space-y-0.5">
          <div>{shortDate(h.start)} → {shortDate(h.end)}</div>
          <div>{fmt(h.mean_daily_mwh)} MWh/day average</div>
          {h.lower_mwh != null && (
            <div>range {fmt(h.lower_mwh)} – {fmt(h.upper_mwh)}</div>
          )}
          {/* The evidence, on the card. */}
          <div className={h.quality === 'measured' ? 'text-ge-text3' : 'text-amber-400'}>
            {h.quality === 'not_measured'
              ? 'not backtested at this range'
              : `backtested on ${h.observations} day${h.observations === 1 ? '' : 's'}`}
          </div>
          {!h.complete && (
            <div className="text-amber-400">
              only {h.days_available} of {h.days_requested} days predicted
            </div>
          )}
        </div>
      )}
      {h?.basis && (
        <div className="text-[10px] text-ge-text3 leading-relaxed mt-2 pt-2
                        border-t border-ge-border/60">
          {h.basis}
        </div>
      )}
    </div>
  )
}

export default function Forecast() {
  const { activeSite } = useApp()
  const [status, setStatus]       = useState(null)
  const [latest, setLatest]       = useState(null)
  const [nextDay, setNextDay]     = useState(null)
  const [history, setHistory]     = useState(null)
  const [deviations, setDeviations] = useState(null)
  const [loading, setLoading]     = useState(true)
  const [running, setRunning]     = useState(false)
  const [error, setError]         = useState('')

  const loadAll = useCallback(async () => {
    setError('')
    try {
      // The shared axios client unwraps the body in its response interceptor
      // (`res => res.data`), so these are payloads already — never `.data`.
      const [s, l, n, h, d] = await Promise.all([
        fetchForecastStatus(),
        fetchForecastLatest(),
        fetchForecastNextDay(),
        fetchForecastHistory(180),
        fetchForecastDeviations(false, 60),
      ])
      setStatus(s); setLatest(l); setNextDay(n)
      setHistory(h); setDeviations(d)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Could not load forecast data.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadAll() }, [loadAll])

  // Train, then reconcile any completed days, then refresh everything.
  const onRun = async () => {
    if (running) return
    setRunning(true); setError('')
    try {
      await runForecast({ target: 'energy_net_mwh' })
      await reconcileForecast(400)
      await loadAll()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Forecast run failed.')
    } finally {
      setRunning(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-3">
        <PageHeader title="Generation Forecast" subtitle="Loading…" />
        <Skeleton h="h-24" /><Skeleton h="h-64" />
      </div>
    )
  }

  const cov   = status?.coverage || {}
  const wx    = status?.weather || {}
  const run   = latest?.available ? latest : null
  const rel   = nextDay?.reliability || run?.reliability
  const met   = !!run?.target_met
  // Next-day / 7-day / 30-day blocks, summed server-side from the forward
  // predictions. Absent until a run exists, so every read is guarded.
  const H     = nextDay?.horizons || {}
  const chart = (history?.points || []).map(p => ({
    date: shortDate(p.date),
    actual: p.actual_mwh,
    predicted: p.predicted_mwh,
    // Recharts stacks an Area from a base, so the band is [lower, upper].
    band: p.lower_mwh != null && p.upper_mwh != null
      ? [p.lower_mwh, p.upper_mwh] : null,
  }))

  return (
    <div className="space-y-3">
      <PageHeader
        title="Generation Forecast & Deviation Detection"
        subtitle={`${activeSite.name} · day-ahead energy prediction from plant `
                  + 'history, scored on a held-out period'}
      >
        <button className="btn btn-primary btn-sm" onClick={onRun} disabled={running}>
          {running ? <><Spinner size={12} /> Running…</> : 'Run forecast'}
        </button>
      </PageHeader>

      {error && (
        <InfoPanel tone="red">
          <span className="text-red-300">{error}</span>
        </InfoPanel>
      )}

      {/* ── validation status: the honest headline ───────────────────────── */}
      {run && (
        <div className={`card p-3 border ${met ? 'border-emerald-700/60 bg-emerald-950/20'
                                               : 'border-amber-700/60 bg-amber-950/20'}`}>
          <div className="flex items-start gap-3">
            <StatusBadge tone={met ? 'green' : 'amber'} className="flex-shrink-0 mt-0.5">
              {rel?.level || (met ? 'VALIDATED' : 'LIMITED')}
            </StatusBadge>
            <div className="text-[12px] text-ge-text2 leading-relaxed">
              {rel?.reason}
              {' '}Measured on {shortDate(run.test_start)} – {shortDate(run.test_end)}
              {' '}({run.n_test} days held out of training), model <b>{run.model}</b>.
            </div>
          </div>
        </div>
      )}

      {/* ── predicted volumes: next day, week, month ─────────────────────── */}
      {/* Kept separate from the accuracy figures below, because they answer
          different questions: how much energy to expect, versus how far the
          model has earned trust. Only the next-day figure is measured by the
          backtest, and each card says which it is. */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <HorizonCard label="Next day" h={H.next_day}
                     fallback={nextDay?.available ? fmt(nextDay.predicted_mwh) : null} />
        <HorizonCard label="Next 7 days" h={H.next_7_days} />
        <HorizonCard label="Next 30 days" h={H.next_30_days} />
      </div>

      {/* ── measured accuracy ────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KpiCard label={`Forecast score (target ${fmt(run?.target_score_pct)}%)`}
                 value={run ? fmt(run.score_pct, 1) : '—'} unit="%"
                 color={met ? 'green' : 'amber'} />
        <KpiCard label="WAPE" value={run ? fmt(run.wape_pct, 1) : '—'} unit="%" color="blue" />
        <KpiCard label="MAE" value={run ? fmt(run.mae_mwh) : '—'} unit="MWh" color="blue" />
        <KpiCard label="RMSE" value={run ? fmt(run.rmse_mwh) : '—'} unit="MWh" color="blue" />
      </div>

      {/* ── weather input disclosure ─────────────────────────────────────── */}
      <InfoPanel
        title="Weather inputs used by the model"
        note={'Measured weather is never used as a model input. Scoring against the '
            + 'irradiance that actually occurred would inflate the result by a wide '
            + 'margin and could not be reproduced in service.'}
      >
        {wx.configured ? (
          <>Provider <b>{wx.provider}</b> is configured. Forecast irradiance, air
            temperature and cloud cover inform the forward prediction.</>
        ) : (
          <>
            <StatusBadge status="UNAVAILABLE" className="mr-2">NO WEATHER FORECAST</StatusBadge>
            {wx.reason}
          </>
        )}
      </InfoPanel>

      {/* ── actual vs predicted ──────────────────────────────────────────── */}
      <div className="card p-3">
        <div className="card-title mb-2">Actual vs predicted energy</div>
        {chart.length === 0 ? (
          <div className="text-[12px] text-ge-text3 py-8 text-center">
            No forecast points yet. Use <b>Run forecast</b> to produce them.
          </div>
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chart} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#243043" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#7d8da3' }} />
                <YAxis tick={{ fontSize: 10, fill: '#7d8da3' }}
                       label={{ value: 'MWh / day', angle: -90, position: 'insideLeft',
                                fill: '#7d8da3', fontSize: 10 }} />
                <Tooltip contentStyle={{ background: '#121a26', border: '1px solid #243043',
                                         fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Area dataKey="band" name="Prediction interval" stroke="none"
                      fill="#0b63a8" fillOpacity={0.18} connectNulls={false} />
                <Line dataKey="actual" name="Actual" stroke="#22c55e" strokeWidth={2}
                      dot={false} connectNulls={false} />
                <Line dataKey="predicted" name="Predicted" stroke="#f59e0b"
                      strokeWidth={2} strokeDasharray="4 3" dot={false} />
                <ReferenceLine y={0} stroke="#334155" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="text-[11px] text-ge-text3 mt-1">
          {history?.note}
        </div>
      </div>

      {/* ── model comparison ─────────────────────────────────────────────── */}
      {run?.detail?.leaderboard?.length > 0 && (
        <div className="card p-3">
          <div className="card-title mb-2">
            Model comparison — day-ahead, on the validation period only
          </div>
          <DataTable
            columns={[
              { key: 'model', label: 'Model' },
              { key: 'score', label: 'Score %' },
              { key: 'wape',  label: 'WAPE %' },
              { key: 'mae',   label: 'MAE MWh' },
              { key: 'rmse',  label: 'RMSE MWh' },
              { key: 'bias',  label: 'Bias MWh' },
              { key: 'kind',  label: 'Type' },
            ]}
            rows={run.detail.leaderboard.map(r => ({
              model: r.model === run.model ? `${r.model}  ← selected` : r.model,
              score: fmt(r.score_pct, 1),
              wape:  fmt(r.wape_pct, 1),
              mae:   fmt(r.mae_mwh),
              rmse:  fmt(r.rmse_mwh),
              bias:  fmt(r.bias_mwh),
              kind:  r.is_baseline ? 'baseline' : 'model',
            }))}
          />
          <div className="text-[11px] text-ge-text3 mt-2">
            Baselines are scored on identical folds. A model that cannot beat
            them has earned nothing, and that outcome is shown rather than hidden.
          </div>
        </div>
      )}

      {/* ── accuracy by condition and horizon ────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {run?.detail?.by_condition?.length > 0 && (
          <div className="card p-3">
            <div className="card-title mb-2">Accuracy by measured condition</div>
            <DataTable
              columns={[
                { key: 'c', label: 'Condition' },
                { key: 's', label: 'Score %' },
                { key: 'm', label: 'MAE MWh' },
                { key: 'n', label: 'Days' },
              ]}
              rows={run.detail.by_condition.map(c => ({
                c: c.condition, s: fmt(c.score_pct, 1), m: fmt(c.mae_mwh), n: c.n,
              }))}
            />
          </div>
        )}
        {run?.detail?.by_horizon?.length > 0 && (
          <div className="card p-3">
            <div className="card-title mb-2">Accuracy by days ahead</div>
            <DataTable
              columns={[
                { key: 'h', label: 'Days ahead' },
                { key: 's', label: 'Score %' },
                { key: 'm', label: 'MAE MWh' },
                { key: 'n', label: 'Days' },
              ]}
              rows={run.detail.by_horizon.map(h => ({
                h: h.horizon_days, s: fmt(h.score_pct, 1), m: fmt(h.mae_mwh), n: h.n,
              }))}
            />
          </div>
        )}
      </div>

      {/* ── deviations ───────────────────────────────────────────────────── */}
      <div className="card p-3">
        <div className="card-title mb-2">Deviations and alert history</div>
        {(deviations?.deviations || []).length === 0 ? (
          <div className="text-[12px] text-ge-text3 py-6 text-center">
            No reconciled days yet. Deviations appear once a predicted day has
            completed and been compared with the measured total.
          </div>
        ) : (
          <div className="space-y-2">
            {deviations.deviations.map((d, i) => (
              <AlertCard
                key={i}
                timestamp={shortDate(d.date)}
                status={d.status}
                details={d.hypotheses || []}
                summary={
                  <>
                    predicted {fmt(d.predicted_mwh)} MWh · actual {fmt(d.actual_mwh)} MWh ·
                    deviation <b>{fmt(d.deviation_mwh)} MWh ({fmt(d.deviation_pct, 1)}%)</b>
                  </>
                }
              />
            ))}
          </div>
        )}
        <div className="text-[11px] text-ge-text3 mt-2">{deviations?.disclaimer}</div>
      </div>

      {/* ── data freshness and limits ────────────────────────────────────── */}
      <div className="card p-3">
        <div className="card-title mb-2">Data coverage and freshness</div>
        <FactGrid
          cols="md:grid-cols-3 xl:grid-cols-4"
          facts={[
            { label: 'Plant record',
              value: `${shortDate(cov.plant_first)} → ${shortDate(cov.plant_last)}` },
            { label: 'Weather record',
              value: `${shortDate(cov.weather_first)} → ${shortDate(cov.weather_last)}` },
            { label: 'Complete days',        value: cov.complete_days },
            { label: 'Days with weather',    value: cov.days_with_weather },
            { label: 'Supervised rows',      value: cov.supervised_rows },
            { label: 'Curtailed days',       value: cov.curtailed_days },
            { label: 'Heavily curtailed',    value: cov.heavily_curtailed_days },
            { label: 'Complete-day threshold',
              value: cov.complete_day_threshold_minutes != null
                ? `${cov.complete_day_threshold_minutes} min` : null },
          ]}
        />
        <div className="text-[11px] text-ge-text3 mt-2 leading-relaxed">
          {cov.timezone_note} {status?.data_freshness?.note}
        </div>
      </div>

      {/* Device-wise forecasting, appended below everything above. Nothing
          already on this page is moved, removed or restyled by it. */}
      <DeviceSection />
    </div>
  )
}
