import React, { Suspense, lazy } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Spinner } from '../components/Common'
import { isPageEnabled, HOME_PATH } from '../config/features'

// Every page is code-split. Previously all fifteen were imported eagerly, so opening
// the Dashboard also downloaded Analytics' chart library, the Excel writer and the
// PDF stack — one 1.29 MB bundle before anything could render. Each page now arrives
// only when its route is first visited; the heavy dependencies (Recharts, jsPDF,
// html2canvas) travel with the pages that actually use them.
//
// All pages are lazy, Dashboard included. In the original build Dashboard was the
// eager landing route, so splitting it would only have added a round trip before the
// first paint. Here the landing route is whatever config/features.js says, and a page
// this deployment has switched off must not sit in the initial bundle at all.
const Dashboard     = lazy(() => import('../pages/Dashboard/Dashboard'))
const Reports       = lazy(() => import('../pages/Reports/Reports'))
const Tracker       = lazy(() => import('../pages/Tracker/Tracker'))
const TrackerStatus = lazy(() => import('../pages/TrackerStatus/TrackerStatus'))
const DGRReports    = lazy(() => import('../pages/DGRReports/DGRReports'))
const Analytics     = lazy(() => import('../pages/Analytics/Analytics'))
const Preconfigured = lazy(() => import('../pages/Preconfigured/Preconfigured'))
const Forecast      = lazy(() => import('../pages/Forecast/Forecast'))
const PPCTrend      = lazy(() => import('../pages/PPCTrend/PPCTrend'))
const PowerGraph    = lazy(() => import('../pages/PowerGraph/PowerGraph'))
const Equipment     = lazy(() => import('../pages/Equipment/Equipment'))
const Alarms        = lazy(() => import('../pages/Alarms/Alarms'))
const Scheduled     = lazy(() => import('../pages/Scheduled/Scheduled'))
const Users         = lazy(() => import('../pages/Users/Users'))
const Settings      = lazy(() => import('../pages/Settings/Settings'))

// The full route table. `key` matches config/features.js and the sidebar, so a page
// is switched on in exactly one place. A disabled route is never registered: typing
// its URL redirects home rather than loading a page whose data this site lacks.
const ROUTES = [
  { key: 'dashboard',     path: '/dashboard',      element: <Dashboard /> },
  { key: 'reports',       path: '/reports',        element: <Reports /> },
  { key: 'tracker',       path: '/tracker',        element: <Tracker /> },
  { key: 'trackerStatus', path: '/tracker-status', element: <TrackerStatus /> },
  { key: 'dgr',           path: '/dgr',            element: <DGRReports /> },
  { key: 'analytics',     path: '/analytics',      element: <Analytics /> },
  { key: 'preconfigured', path: '/preconfigured',  element: <Preconfigured /> },
  { key: 'forecast',      path: '/forecast',       element: <Forecast /> },
  { key: 'ppcTrend',      path: '/ppc-trend',      element: <PPCTrend /> },
  { key: 'powerGraph',    path: '/power-graph',    element: <PowerGraph /> },
  { key: 'equipment',     path: '/equipment',      element: <Equipment /> },
  { key: 'alarms',        path: '/alarms',         element: <Alarms /> },
  { key: 'scheduled',     path: '/scheduled',      element: <Scheduled /> },
  { key: 'users',         path: '/users',          element: <Users /> },
  { key: 'settings',      path: '/settings',       element: <Settings /> },
]

/** Shown for the moment a page's chunk is in flight. */
function PageLoading() {
  return (
    <div className="flex items-center justify-center gap-2 py-20 text-ge-text3 text-[12px]">
      <Spinner size={14} /> Loading…
    </div>
  )
}

export default function AppRoutes() {
  const enabled = ROUTES.filter(r => isPageEnabled(r.key))

  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route path="/" element={<Navigate to={HOME_PATH} replace />} />
        {enabled.map(r => (
          <Route key={r.key} path={r.path} element={r.element} />
        ))}
        <Route path="*" element={<Navigate to={HOME_PATH} replace />} />
      </Routes>
    </Suspense>
  )
}
