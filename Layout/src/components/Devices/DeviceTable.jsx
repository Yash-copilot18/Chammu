// src/components/Devices/DeviceTable.jsx
//
// The device-wise table: sortable headers, paginated body.
//
// It renders its own <table className="data-table"> rather than going through
// the shared <DataTable>, for one reason: sortable column headers need a click
// handler on each <th>, which DataTable does not expose. Writing the markup
// here keeps DataTable untouched for its two dozen existing callers while
// reusing the same `.data-table` styling, so the two look identical.

import React, { useMemo, useState } from 'react'
import { StatusBadge, Pagination } from '../Common'

/** Badge colour by what the backtest actually established for that row. */
const VALIDATION_TONE = {
  'MEASURED': 'green',
  'PROVISIONAL': 'amber',
  'PLANNING ESTIMATE': 'neutral',
  'INSUFFICIENT DATA': 'neutral',
  'NOT FORECAST': 'neutral',
}

const STATUS_TONE = {
  OK: 'green',
  'BELOW PEERS': 'amber',
  'ZERO OUTPUT': 'red',
  'STALE DATA': 'amber',
  'NO DATA': 'neutral',
  'INSUFFICIENT DATA': 'neutral',
  'FORECAST BLOCKED': 'neutral',
}

/**
 * Render a value that may legitimately be zero.
 *
 * `0` and "no reading" are different facts and must not look the same: a
 * combiner that genuinely produced nothing is a finding, while one that was
 * never read is a gap. Only null/undefined becomes an em dash.
 */
export const val = (v, digits = 1, suffix = '') => {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const n = Number(v).toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })
  return suffix ? `${n} ${suffix}` : n
}

const shortDate = s => (s ? String(s).slice(0, 10) : '—')

/** Days between a timestamp and the newest one in the data set. */
function freshness(lastSeen, newest) {
  if (!lastSeen) return { label: '—', stale: false }
  const d = new Date(lastSeen)
  const ref = newest ? new Date(newest) : d
  const days = Math.round((ref - d) / 86400000)
  if (!Number.isFinite(days)) return { label: shortDate(lastSeen), stale: false }
  return {
    label: days <= 0 ? shortDate(lastSeen) : `${shortDate(lastSeen)} (−${days}d)`,
    stale: days >= 7,
  }
}

const PER_PAGE = 15

export default function DeviceTable({ columns, rows, newestSeen, emptyMsg }) {
  const [sortKey, setSortKey] = useState(null)
  const [asc, setAsc] = useState(false)
  const [page, setPage] = useState(1)

  const sorted = useMemo(() => {
    if (!sortKey) return rows
    const copy = [...rows]
    copy.sort((a, b) => {
      const x = a[sortKey], y = b[sortKey]
      // Rows with no value sort last whichever way the column is pointing:
      // a missing reading is not "the smallest", it is absent.
      if (x === null || x === undefined) return 1
      if (y === null || y === undefined) return -1
      if (typeof x === 'number' && typeof y === 'number') return asc ? x - y : y - x
      return asc ? String(x).localeCompare(String(y)) : String(y).localeCompare(String(x))
    })
    return copy
  }, [rows, sortKey, asc])

  const total = sorted.length
  const pageRows = sorted.slice((page - 1) * PER_PAGE, page * PER_PAGE)

  const toggle = key => {
    if (key === sortKey) { setAsc(a => !a) } else { setSortKey(key); setAsc(false) }
    setPage(1)
  }

  if (total === 0) {
    return <div className="text-[12px] text-ge-text3 py-8 text-center">{emptyMsg}</div>
  }

  return (
    <>
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map(c => (
                <th
                  key={c.key}
                  onClick={c.sortable === false ? undefined : () => toggle(c.key)}
                  className={`${c.align === 'right' ? 'text-right' : 'text-left'} ${
                    c.sortable === false ? '' : 'cursor-pointer select-none hover:text-ge-text1'
                  }`}
                  title={c.sortable === false ? undefined : 'Sort by this column'}
                >
                  {c.label}
                  {sortKey === c.key && (
                    <span className="ml-1 text-ge-accent">{asc ? '▲' : '▼'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map(row => (
              <tr key={row.device}>
                {columns.map(c => (
                  <td key={c.key}
                      className={c.align === 'right' ? 'text-right' : ''}>
                    {c.render ? c.render(row, { freshness, newestSeen }) : (
                      row[c.key] === null || row[c.key] === undefined ? '—' : row[c.key]
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > PER_PAGE && (
        <Pagination page={page} setPage={setPage} total={total} perPage={PER_PAGE} />
      )}
    </>
  )
}

export { VALIDATION_TONE, STATUS_TONE, shortDate, freshness }
