// src/components/Site/UnconfiguredBanner.jsx
//
// Shown when the selected plant has no backend URL configured.
//
// Without it every page would render its empty state, and an empty dashboard
// looks exactly like a plant that has stopped generating. The banner makes the
// difference explicit: there is nothing to show because nothing is connected,
// not because the plant is down.

import React from 'react'
import { MdInfoOutline } from 'react-icons/md'
import { isConnected } from '../../config/sites'
import { useApp } from '../../utils/AppContext'

export default function UnconfiguredBanner() {
  const { activeSite } = useApp()
  if (isConnected(activeSite)) return null

  return (
    <div className="card p-3 mb-3 border border-amber-700/60 bg-amber-950/20">
      <div className="flex items-start gap-2.5">
        <MdInfoOutline className="text-amber-400 text-[18px] flex-shrink-0 mt-0.5" />
        <div className="text-[12px] text-ge-text2 leading-relaxed">
          <b className="text-ge-text1">{activeSite.name}</b> has no backend configured,
          so no plant data can be loaded. The pages below show the full layout with
          empty states — they are not reporting an outage.
          <div className="text-[11px] text-ge-text3 mt-1">
            Set <span className="font-mono">apiUrl</span> for this site in{' '}
            <span className="font-mono">VITE_SITES</span>, or{' '}
            <span className="font-mono">VITE_API_URL</span> for a single-plant
            deployment, then rebuild.
          </div>
        </div>
      </div>
    </div>
  )
}
