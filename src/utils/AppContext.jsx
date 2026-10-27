// src/utils/AppContext.jsx
import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react'
import { SITES, getActiveSite, setActiveSiteId } from '../config/sites'

const AppContext = createContext(null)

function getInitials(name) {
  return name
    .split(' ')
    .map(w => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2)
}

// Storage keys are namespaced to this product. The Kalyon dashboard uses the
// 'ge_' prefix; if both are ever served from the same origin, neither can read
// or overwrite the other's theme, profile or saved date range.
const REPORT_RANGE_KEY = 'solarops.report_date_range'

// A range is usable only when both ends parse as a real date/time and From is not
// after To. Compared as strings: 'YYYY-MM-DDTHH:mm' sorts chronologically, so no Date
// object (and no timezone) is involved.
const RANGE_VALUE_RE = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$/
export function isValidRange(from, to) {
  if (!from || !to) return false
  if (!RANGE_VALUE_RE.test(from) || !RANGE_VALUE_RE.test(to)) return false
  return from <= to
}

function loadReportDateRange() {
  try {
    const raw = localStorage.getItem(REPORT_RANGE_KEY)
    if (raw) {
      const v = JSON.parse(raw)
      if (v && v.from && v.to) return v
    }
  } catch {}
  return null                      // nothing stored → Analytics uses its own default
}

function loadUserProfile() {
  try {
    const raw = localStorage.getItem('solarops.user_profile')
    if (raw) return JSON.parse(raw)
  } catch {}
  return { name: 'Plant Operator', email: '', initials: 'PO' }
}

export function AppProvider({ children }) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  // ── Active site ────────────────────────────────────────────────────────────
  // Read once at mount. The registry owns persistence, because services/api.js
  // needs the backend URL before any component renders; switching site reloads
  // the app, so this value is constant for the lifetime of the page.
  const [activeSite] = useState(getActiveSite)
  const selectSite = useCallback(id => setActiveSiteId(id), [])

  const [toasts, setToasts] = useState([])
  const [modalState, setModalState] = useState({ open: false, title: '', content: null })

  // ── Theme ──────────────────────────────────────────────────────────────────
  const [theme, setTheme] = useState(
    () => localStorage.getItem('solarops.theme') || 'dark'
  )

  useEffect(() => {
    localStorage.setItem('solarops.theme', theme)
    if (theme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }, [theme])

  const toggleTheme = useCallback(() => {
    setTheme(t => (t === 'dark' ? 'light' : 'dark'))
  }, [])

  // ── User Profile ───────────────────────────────────────────────────────────
  const [userProfile, setUserProfileState] = useState(loadUserProfile)

  const updateUserProfile = useCallback((partial) => {
    setUserProfileState(prev => {
      const next = { ...prev, ...partial }
      if (!next.initials) next.initials = getInitials(next.name || 'KA')
      localStorage.setItem('solarops.user_profile', JSON.stringify(next))
      return next
    })
  }, [])

  // ── Shared report date range (Reports → Analytics) ─────────────────────────
  // The From/To the user picked on the Reports page, published here so Analytics
  // opens on the SAME range without either page importing the other. Stored as the
  // Reports page's own 'YYYY-MM-DDTHH:mm' strings — its formatting is unchanged, and
  // consumers that only need the day (Analytics uses <input type="date">) take the
  // date part. Persisted like the theme/profile above so a browser reload restores
  // it; when nothing is stored the value is null and Analytics keeps its own default.
  const [reportDateRange, setReportDateRangeState] = useState(loadReportDateRange)

  // Publishing is guarded so only a COMPLETE, ordered range is ever shared: a
  // <input type="datetime-local"> reports '' (or a partial value) while the user is
  // still filling it in, and From can momentarily exceed To when only one end has
  // been updated. Rejecting those here — at the single writer — means consumers never
  // have to defend against a half-typed or backwards range, and no request is ever
  // issued for one. An unchanged range returns the previous object so nothing
  // re-renders.
  const setReportDateRange = useCallback((from, to) => {
    if (!isValidRange(from, to)) return
    setReportDateRangeState(prev => {
      if (prev && prev.from === from && prev.to === to) return prev   // no-op re-render
      const next = { from, to }
      try { localStorage.setItem(REPORT_RANGE_KEY, JSON.stringify(next)) } catch {}
      return next
    })
  }, [])

  // ── Page Refresh Registry ──────────────────────────────────────────────────
  // Pages register an async refresh fn; Header calls triggerRefresh()
  const refreshFnRef = useRef(null)
  const [refreshing, setRefreshing] = useState(false)

  const registerRefresh = useCallback((fn) => {
    refreshFnRef.current = fn
  }, [])

  const triggerRefresh = useCallback(async () => {
    if (!refreshFnRef.current) return
    setRefreshing(true)
    try {
      await refreshFnRef.current()
    } finally {
      setRefreshing(false)
    }
  }, [])

  // ── Toast ──────────────────────────────────────────────────────────────────
  const toastSeq = useRef(0)
  const showToast = useCallback((msg, type = 'success') => {
    // Monotonic unique id — Date.now() alone collides for toasts fired in the
    // same millisecond, causing React "two children with the same key" warnings.
    const id = `${Date.now()}-${toastSeq.current++}`
    setToasts(prev => [...prev, { id, msg, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000)
  }, [])

  // ── Modal ──────────────────────────────────────────────────────────────────
  const openModal = useCallback((title, content) => {
    setModalState({ open: true, title, content })
  }, [])

  const closeModal = useCallback(() => {
    setModalState(s => ({ ...s, open: false }))
  }, [])

  return (
    <AppContext.Provider value={{
      sidebarCollapsed, setSidebarCollapsed,
      sites: SITES, activeSite, selectSite,
      toasts, showToast,
      modalState, openModal, closeModal,
      theme, toggleTheme,
      userProfile, updateUserProfile,
      reportDateRange, setReportDateRange,
      refreshing, registerRefresh, triggerRefresh,
    }}>
      {children}
    </AppContext.Provider>
  )
}

export const useApp = () => useContext(AppContext)
