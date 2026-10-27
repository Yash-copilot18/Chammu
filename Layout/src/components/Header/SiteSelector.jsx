// src/components/Header/SiteSelector.jsx
//
// The plant picker that sits at the left of the header.
//
// It shows the active site's name and installed capacity, and opens a list of
// every configured site. With only one site configured it renders as a plain,
// non-interactive label — a dropdown arrow that opens a menu of one is noise.

import React, { useEffect, useRef, useState } from 'react'
import { MdExpandMore, MdSolarPower, MdCheck } from 'react-icons/md'
import { SITES, capacityLabel, isConnected, setActiveSiteId } from '../../config/sites'
import { useApp } from '../../utils/AppContext'

/** Name + capacity, sized to sit inside the 52px header. */
function SiteIdentity({ site, compact = false }) {
  const cap = capacityLabel(site)
  return (
    <div className="min-w-0 text-left">
      <div className="text-[13px] font-semibold text-ge-text1 leading-tight truncate">
        {compact ? (site.shortName || site.name) : site.name}
      </div>
      <div className="text-[10px] font-mono text-ge-text3 leading-tight truncate">
        {/* Capacity and location are labels the operator configures. Neither is
            measured, so an unconfigured field shows as a dash, never a guess. */}
        {[cap, site.location].filter(Boolean).join(' · ') || 'Capacity not configured'}
      </div>
    </div>
  )
}

export default function SiteSelector() {
  const { activeSite } = useApp()
  const [open, setOpen] = useState(false)
  const boxRef = useRef(null)
  const single = SITES.length < 2

  // Close on an outside click or Escape — the two ways a user expects a menu to
  // go away without choosing anything.
  useEffect(() => {
    if (!open) return
    const onDown = e => { if (!boxRef.current?.contains(e.target)) setOpen(false) }
    const onKey = e => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (single) {
    return (
      <div className="flex items-center gap-2 min-w-0 max-w-[320px]">
        <MdSolarPower className="text-ge-accent text-[18px] flex-shrink-0" />
        <SiteIdentity site={activeSite} />
      </div>
    )
  }

  return (
    <div className="relative min-w-0" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 max-w-[320px] min-w-0 pl-2 pr-1.5 py-1 rounded-md
                   border border-transparent hover:border-ge-border hover:bg-ge-surface
                   transition-all"
        title="Switch plant"
      >
        <MdSolarPower className="text-ge-accent text-[18px] flex-shrink-0" />
        <SiteIdentity site={activeSite} />
        <MdExpandMore
          className={`text-ge-text3 text-base flex-shrink-0 transition-transform ${
            open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute left-0 top-full mt-1.5 w-[300px] max-h-[60vh] overflow-y-auto
                     bg-ge-card border border-ge-border rounded-lg shadow-xl z-50 py-1"
        >
          <div className="px-3 py-1.5 text-[9px] font-semibold text-ge-text3 uppercase tracking-widest">
            Plants ({SITES.length})
          </div>
          {SITES.map(site => {
            const active = site.id === activeSite.id
            return (
              <button
                key={site.id}
                role="option"
                aria-selected={active}
                onClick={() => { setOpen(false); setActiveSiteId(site.id) }}
                className={`w-full flex items-start gap-2 px-3 py-2 text-left transition-colors ${
                  active ? 'bg-ge-elevated' : 'hover:bg-ge-surface'}`}
              >
                <MdCheck className={`text-[15px] mt-0.5 flex-shrink-0 ${
                  active ? 'text-ge-accent' : 'text-transparent'}`} />
                <div className="min-w-0 flex-1">
                  <div className="text-[12px] font-medium text-ge-text1 truncate">{site.name}</div>
                  <div className="text-[10px] font-mono text-ge-text3 truncate">
                    {[capacityLabel(site), site.location].filter(Boolean).join(' · ') || '—'}
                  </div>
                </div>
                {/* A site with no backend configured is labelled, so nobody
                    selects it and reads an empty dashboard as an outage. */}
                {!isConnected(site) && (
                  <span className="status-pill pill-amber flex-shrink-0 mt-0.5">Not connected</span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
