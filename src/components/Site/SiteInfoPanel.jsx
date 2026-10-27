// src/components/Site/SiteInfoPanel.jsx
//
// The plant's identity card: name, location, installed capacity, equipment
// counts, commissioning date and timezone.
//
// Everything here comes from src/config/sites.js. None of it is measured and
// none of it is fetched — these are labels the operator supplies. A field that
// has not been configured shows an em dash, so an unconfigured plant reads as
// unconfigured rather than as a plant with zero inverters.

import React from 'react'
import { FactGrid, StatusBadge } from '../Common'
import { capacityLabel, isConnected } from '../../config/sites'
import { useApp } from '../../utils/AppContext'

const num = v => (v == null || v === '' ? null : Number(v).toLocaleString())

export default function SiteInfoPanel({ className = '' }) {
  const { activeSite: site } = useApp()

  const facts = [
    { label: 'Plant',            value: site.name },
    { label: 'Location',         value: site.location },
    { label: 'Capacity (AC)',    value: capacityLabel(site) },
    { label: 'Capacity (DC)',    value: site.dcCapacityMwp != null ? `${num(site.dcCapacityMwp)} MWp` : null },
    { label: 'Inverters',        value: num(site.inverters) },
    { label: 'Trackers',         value: num(site.trackers) },
    { label: 'Strings monitored', value: num(site.strings) },
    { label: 'Commissioned',     value: site.commissioned },
    { label: 'Timezone',         value: site.timezone },
    { label: 'Backend',          value: site.apiUrl || null },
  ]

  return (
    <div className={`card p-3 ${className}`.trim()}>
      <div className="card-title mb-2 justify-between">
        <span>Plant information</span>
        <StatusBadge status={isConnected(site) ? 'ONLINE' : 'UNAVAILABLE'}>
          {isConnected(site) ? 'CONNECTED' : 'NOT CONNECTED'}
        </StatusBadge>
      </div>
      <FactGrid facts={facts} cols="md:grid-cols-3 xl:grid-cols-5" />
      <div className="text-[11px] text-ge-text3 mt-2">
        Configured in <span className="font-mono">src/config/sites.js</span> (or the
        <span className="font-mono"> VITE_SITES</span> build variable). These are
        plant attributes, not measurements.
      </div>
    </div>
  )
}
