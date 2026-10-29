// src/components/Devices/DeviceSection.jsx
//
// Device-wise forecasting, appended below the plant forecast.
//
// Three device types, three different kinds of answer, and the section does
// not pretend otherwise:
//
//   Inverters  a real per-device forecast, each with its own model chosen on
//              its own folds, and its own held-out sample size on the row
//   Combiners  peer comparison, no forecast. With 34 complete days and 34
//              boxes reporting nothing, ranking against neighbours is the
//              honest question; "how much will box 29 make tomorrow" is not
//   Meters     inventory only, forecasting blocked on an unresolved
//              reconciliation against the inverters
//
// Nothing above this component on the page is touched.

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { MdRefresh } from 'react-icons/md'
import {
  Spinner, StatusBadge, InfoPanel, SearchInput,
} from '../Common'
import DeviceTable, { VALIDATION_TONE, STATUS_TONE, val, shortDate } from './DeviceTable'
import {
  fetchDeviceInventory, fetchDeviceForecasts, runDeviceForecasts,
  fetchCombiners, fetchDeviceMeters,
} from '../../services/api'

const TABS = [
  { key: 'inverters', label: 'Inverters' },
  { key: 'combiners', label: 'Combiners' },
  { key: 'meters', label: 'Meters' },
]

function Freshness({ value, newest }) {
  if (!value) return <span className="text-ge-text3">—</span>
  const d = new Date(value)
  const ref = newest ? new Date(newest) : d
  const days = Math.round((ref - d) / 86400000)
  const stale = Number.isFinite(days) && days >= 7
  return (
    <span className={stale ? 'text-amber-400' : 'text-ge-text2'}>
      {shortDate(value)}{stale ? ` (−${days}d)` : ''}
    </span>
  )
}

export default function DeviceSection() {
  const [tab, setTab] = useState('inverters')
  const [inventory, setInventory] = useState(null)
  const [forecasts, setForecasts] = useState(null)
  const [combiners, setCombiners] = useState(null)
  const [meters, setMeters] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('ALL')

  const load = useCallback(async () => {
    setError('')
    try {
      // The axios client unwraps the body in its interceptor, so these are
      // payloads already — never `.data`.
      const [inv, fc, mt] = await Promise.all([
        fetchDeviceInventory(), fetchDeviceForecasts(), fetchDeviceMeters(),
      ])
      setInventory(inv); setForecasts(fc); setMeters(mt)
    } catch (e) {
      setError(e?.message || 'Could not load device data.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Combiner analysis scans 170 tables, so it is fetched only when that tab is
  // first opened rather than on every page load.
  useEffect(() => {
    if (tab !== 'combiners' || combiners) return
    let cancelled = false
    ;(async () => {
      try {
        const c = await fetchCombiners()
        if (!cancelled) setCombiners(c)
      } catch (e) {
        if (!cancelled) setError(e?.message || 'Could not load combiner analysis.')
      }
    })()
    return () => { cancelled = true }
  }, [tab, combiners])

  // A run already under way is reported by the server, so the button stays
  // disabled across a page reload instead of inviting a second click that can
  // only ever be rejected.
  const serverRunning = !!forecasts?.running
  const busy = running || serverRunning

  const onRun = async () => {
    if (busy) return
    setRunning(true); setError('')
    try {
      await runDeviceForecasts()
      setForecasts(await fetchDeviceForecasts())
    } catch (e) {
      // "already in progress" is not a failure — the run the user asked for is
      // happening. Showing it in red taught people to click again, which is
      // the one thing that cannot help.
      const msg = e?.message || 'Device forecast run failed.'
      if (/already in progress/i.test(msg)) {
        setForecasts(await fetchDeviceForecasts())
      } else {
        setError(msg)
      }
    } finally {
      setRunning(false)
    }
  }

  // While a run is under way, poll until it finishes so the table fills in on
  // its own rather than waiting for the user to reload.
  useEffect(() => {
    if (!serverRunning) return
    const id = setInterval(async () => {
      try { setForecasts(await fetchDeviceForecasts()) } catch { /* keep polling */ }
    }, 5000)
    return () => clearInterval(id)
  }, [serverRunning])

  const newest = inventory?.inverters?.reduce(
    (a, d) => (!a || (d.last_seen && d.last_seen > a) ? d.last_seen : a), null)

  // How much of the fleet the breakdown actually covers. Counted from the
  // results rather than hard-coded, so it stays true as history accumulates
  // and a device becomes evaluable.
  const invRows = forecasts?.devices || []
  const eligible = invRows.filter(r => r.n_test > 0).length
  const ineligible = invRows.filter(r => !r.n_test).map(r => r.device)

  // ── rows for the active tab ───────────────────────────────────────────────
  const raw = tab === 'inverters' ? (forecasts?.devices || [])
    : tab === 'combiners' ? (combiners?.devices || [])
    : (meters?.devices || [])

  const statuses = useMemo(
    () => ['ALL', ...Array.from(new Set(raw.map(r => r.status).filter(Boolean))).sort()],
    [raw])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return raw.filter(r =>
      (statusFilter === 'ALL' || r.status === statusFilter) &&
      (!q || String(r.device).toLowerCase().includes(q) ||
             String(r.group || '').toLowerCase().includes(q)))
  }, [raw, query, statusFilter])

  // ── column sets ───────────────────────────────────────────────────────────
  const inverterCols = [
    { key: 'device', label: 'Device' },
    { key: 'model', label: 'Model',
      render: r => r.model || <span className="text-ge-text3">—</span> },
    { key: 'latest_actual_mwh', label: 'Latest actual', align: 'right',
      render: r => val(r.latest_actual_mwh, 1) },
    { key: 'predicted_mwh', label: 'Next day', align: 'right',
      render: r => val(r.predicted_mwh, 1) },
    { key: 'forecast_date', label: 'For', sortable: false,
      render: r => shortDate(r.forecast_date) },
    // Cumulative volumes. Deliberately not given a validation badge of their
    // own: the column header says planning estimate once, for the pair.
    { key: 'h7', label: 'Next 7 d', align: 'right', sortable: false,
      render: r => val(r.horizons?.next_7_days?.total_mwh, 0) },
    { key: 'h30', label: 'Next 30 d', align: 'right', sortable: false,
      render: r => val(r.horizons?.next_30_days?.total_mwh, 0) },
    { key: 'score_pct', label: 'Score %', align: 'right',
      render: r => val(r.score_pct, 1) },
    { key: 'mae_mwh', label: 'MAE', align: 'right',
      render: r => val(r.mae_mwh, 2) },
    { key: 'r2', label: 'R²', align: 'right',
      render: r => (r.r2 === null || r.r2 === undefined ? '—' : (
        <span className={r.r2 < 0 ? 'text-amber-400' : ''}>{val(r.r2, 2)}</span>
      )) },
    { key: 'n_test', label: 'Held out', align: 'right',
      render: r => (r.n_test ? `${r.n_test} d` : '—') },
    { key: 'validation', label: 'Evidence', sortable: false,
      render: r => (
        <StatusBadge tone={VALIDATION_TONE[r.validation] || 'neutral'}>
          {r.validation}
        </StatusBadge>
      ) },
    { key: 'last_seen', label: 'Last reading', sortable: false,
      render: r => <Freshness value={r.last_seen} newest={newest} /> },
  ]

  const combinerCols = [
    { key: 'device', label: 'Device' },
    { key: 'group', label: 'Group' },
    { key: 'latest_actual_kwh', label: 'Latest kWh', align: 'right',
      render: r => val(r.latest_actual_kwh, 1) },
    { key: 'mean_actual_kwh', label: 'Mean kWh/day', align: 'right',
      render: r => val(r.mean_actual_kwh, 1) },
    { key: 'group_median_kwh', label: 'Group median', align: 'right',
      render: r => val(r.group_median_kwh, 0) },
    { key: 'deviation_pct', label: 'vs peers', align: 'right',
      render: r => (r.deviation_pct === null || r.deviation_pct === undefined ? '—' : (
        <span className={r.deviation_pct < -25 ? 'text-amber-400' : ''}>
          {r.deviation_pct > 0 ? '+' : ''}{val(r.deviation_pct, 1)}%
        </span>
      )) },
    { key: 'peer_rank', label: 'Rank', align: 'right',
      render: r => (r.peer_rank ? `${r.peer_rank} / ${r.peer_total}` : '—') },
    { key: 'complete_days', label: 'Days', align: 'right',
      render: r => val(r.complete_days, 0) },
    { key: 'status', label: 'Status', sortable: false,
      render: r => (
        <StatusBadge tone={STATUS_TONE[r.status] || 'neutral'}>{r.status}</StatusBadge>
      ) },
    { key: 'last_seen', label: 'Last reading', sortable: false,
      render: r => <Freshness value={r.last_seen} newest={newest} /> },
  ]

  const meterCols = [
    { key: 'device', label: 'Device' },
    { key: 'rows', label: 'Rows', align: 'right', render: r => val(r.rows, 0) },
    { key: 'days', label: 'Days', align: 'right', render: r => val(r.days, 0) },
    { key: 'implausible_register_rows', label: 'Implausible readings', align: 'right',
      render: r => (r.implausible_register_rows
        ? <span className="text-amber-400">{val(r.implausible_register_rows, 0)}</span>
        : val(r.implausible_register_rows, 0)) },
    { key: 'status', label: 'Status', sortable: false,
      render: r => (
        <StatusBadge tone={STATUS_TONE[r.status] || 'neutral'}>{r.status}</StatusBadge>
      ) },
    { key: 'last_seen', label: 'Last reading', sortable: false,
      render: r => <Freshness value={r.last_seen} newest={newest} /> },
  ]

  const cols = tab === 'inverters' ? inverterCols
    : tab === 'combiners' ? combinerCols : meterCols

  const counts = inventory?.counts || {}

  return (
    <div className="card p-3 mt-3">
      <div className="card-title mb-2 justify-between flex-wrap gap-2">
        <span>Device-wise forecasting and performance</span>
        {tab === 'inverters' && (
          <button className="btn btn-outline btn-sm" onClick={onRun} disabled={busy}>
            {busy
              ? <><Spinner size={12} /> Running… {invRows.length} of{' '}
                  {forecasts?.total || counts.inverters || '?'}</>
              : <><MdRefresh className="text-sm" /> Run device forecasts</>}
          </button>
        )}
      </div>

      {error && (
        <InfoPanel tone="red" className="mb-3">
          <span className="text-red-300">{error}</span>
        </InfoPanel>
      )}

      {/* device type selector */}
      <div className="flex items-center gap-2 flex-wrap mb-3">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); setQuery(''); setStatusFilter('ALL') }}
            className={`px-2.5 py-1 text-[11px] rounded border transition-all ${
              tab === t.key
                ? 'bg-ge-blue text-white border-ge-blue'
                : 'bg-ge-elevated border-ge-border text-ge-text2 hover:text-ge-text1'}`}
          >
            {t.label}
            <span className="ml-1.5 font-mono opacity-70">
              {counts[t.key] ?? '—'}
            </span>
          </button>
        ))}

        <SearchInput value={query} onChange={setQuery}
                     placeholder="Search device…" className="w-44 ml-auto" />

        <select className="form-control w-40 text-[11px]"
                value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          {statuses.map(s => (
            <option key={s} value={s}>{s === 'ALL' ? 'All statuses' : s}</option>
          ))}
        </select>
      </div>

      {/* what this tab's numbers are worth */}
      {tab === 'inverters' && (
        <>
          {/* The one thing a reader must not do with this table is add it up
              and call the result the plant forecast. Said before the numbers,
              not in a footnote after them — and only once there ARE numbers,
              because "covers 0 of 12" above an empty table is noise. */}
          {invRows.length > 0 && (
            <InfoPanel tone="amber" className="mb-3"
              title="This is a breakdown, not the plant forecast">
              The official plant forecast is the one above, measured on all
              twelve inverters' history. This table covers{' '}
              <b>{eligible} of {invRows.length} inverters</b>
              {ineligible.length > 0 && <>
                {' '}— {ineligible.join(' and ')}{' '}
                {ineligible.length === 1 ? 'has' : 'have'} too little usable
                history to evaluate yet
              </>}. The rows below therefore{' '}
              <b>do not sum to the plant total</b>, and no figure here has been
              scaled up to cover the devices that are missing from it.
            </InfoPanel>
          )}

          <div className="text-[11px] text-ge-text3 mb-2 leading-relaxed">
            Each inverter is backtested on its own history, and picks its own
            model from the same five candidates on identical chronological folds
            — the plant's winner is not inherited. Per-device error is larger
            than the plant's, because errors that cancel across twelve inverters
            do not cancel within one. A negative R² means the model tracked that
            device's day-to-day variation worse than its own average would have.
            {' '}<b>Next 7 d and Next 30 d are planning estimates</b>: only the
            next-day figure is backtested, and beyond it each day is predicted
            from the previous day's prediction with no new information entering.
          </div>
        </>
      )}
      {tab === 'combiners' && combiners && (
        <div className="text-[11px] text-ge-text3 mb-2 leading-relaxed">
          {combiners.basis}
        </div>
      )}
      {tab === 'meters' && meters?.reconciliation && (
        <InfoPanel tone="amber" className="mb-3"
          title="Meter forecasting is blocked"
          note={meters.reconciliation.detail}>
          Over {meters.reconciliation.paired_days} days where both sources have
          data, the outgoing meter reads a median{' '}
          <b>{meters.reconciliation.median_meter_over_inverter_pct}%</b> of the
          summed inverter output. Export cannot exceed gross generation, so one
          of the two is mis-scaled. Readings are shown untouched — no correction
          has been applied to make them agree.
        </InfoPanel>
      )}

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-10 text-ge-text3 text-[12px]">
          <Spinner size={14} /> Loading devices…
        </div>
      ) : tab === 'combiners' && !combiners ? (
        <div className="flex items-center justify-center gap-2 py-10 text-ge-text3 text-[12px]">
          <Spinner size={14} /> Analysing {counts.combiners || ''} combiner boxes…
        </div>
      ) : (
        <DeviceTable
          columns={cols}
          rows={rows}
          newestSeen={newest}
          emptyMsg={
            tab !== 'inverters' ? 'No devices match this filter.'
              : serverRunning
                ? `Run in progress — ${invRows.length} of ${forecasts?.total || '?'} devices done. Each takes a few seconds; results appear as they finish.`
                : invRows.length
                  ? 'No devices match this filter.'
                  : 'No device run yet. Use Run device forecasts. It takes a few minutes for twelve inverters.'
          }
        />
      )}

      {tab === 'inverters' && forecasts?.generated_at && (
        <div className="text-[11px] text-ge-text3 mt-2">
          Last run {String(forecasts.generated_at).replace('T', ' ').slice(0, 19)}.
          {' '}Results are held in memory and cleared when the backend restarts.
        </div>
      )}
    </div>
  )
}
