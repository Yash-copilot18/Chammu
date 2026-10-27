// src/components/Sidebar/Sidebar.jsx
import React from 'react'
import { NavLink } from 'react-router-dom'
import {
  MdDashboard, MdBarChart, MdAssignment, MdAnalytics,
  MdBuild, MdNotifications, MdSchedule, MdPeople, MdSettings,
  MdChevronLeft, MdChevronRight, MdSatelliteAlt, MdShowChart, MdBolt, MdTune, MdTrendingUp } from 'react-icons/md'
import { useApp } from '../../utils/AppContext'
import { isPageEnabled } from '../../config/features'

// Each item carries the `key` that config/features.js switches on. An item whose
// page is not enabled for this deployment is left out of the sidebar AND has no
// route registered, so its URL cannot be reached either — unlike `hidden: true`,
// which only removed it from the menu.
//
// `hidden: true` is kept for pages that exist and work but are deliberately not
// advertised; it is independent of the feature switch.
const NAV_ITEMS = [
  { key: 'dashboard',     label: 'Dashboard',         path: '/dashboard',      icon: MdDashboard,     group: 'Operations' },
  { key: 'reports',       label: 'Reports',           path: '/reports',        icon: MdBarChart,      group: 'Operations' },
  { key: 'tracker',       label: 'Tracker Reports',   path: '/tracker',        icon: MdSatelliteAlt,  group: 'Operations', hidden: true },
  { key: 'trackerStatus', label: 'Tracker Status',    path: '/tracker-status', icon: MdSatelliteAlt,  group: 'Operations', hidden: true },
  { key: 'dgr',           label: 'DGR Reports',       path: '/dgr',            icon: MdAssignment,    group: 'Operations' },
  { key: 'analytics',     label: 'Analytics',         path: '/analytics',      icon: MdAnalytics,     group: 'Operations' },
  { key: 'preconfigured', label: 'Preconfigured Reports', path: '/preconfigured', icon: MdTune,       group: 'Operations' },
  { key: 'forecast',      label: 'Generation Forecast', path: '/forecast',     icon: MdTrendingUp,    group: 'Operations' },
  { key: 'ppcTrend',      label: 'PPC Trend',         path: '/ppc-trend',      icon: MdShowChart,     group: 'Operations', hidden: true },
  { key: 'powerGraph',    label: 'Power Graph',       path: '/power-graph',    icon: MdBolt,          group: 'Operations', hidden: true },
  { key: 'equipment',     label: 'Equipment',         path: '/equipment',      icon: MdBuild,         group: 'Monitoring', hidden: true },
  { key: 'alarms',        label: 'Alarms',            path: '/alarms',         icon: MdNotifications, group: 'Monitoring', hidden: true },
  { key: 'scheduled',     label: 'Scheduled Reports', path: '/scheduled',      icon: MdSchedule,      group: 'Monitoring' },
  { key: 'users',         label: 'User Management',   path: '/users',          icon: MdPeople,        group: 'Admin' },
  { key: 'settings',      label: 'Settings',          path: '/settings',       icon: MdSettings,      group: 'Admin', hidden: true },
]

const GROUPS = ['Operations', 'Monitoring', 'Admin']

export default function Sidebar() {
  const { sidebarCollapsed, setSidebarCollapsed } = useApp()

  return (
    <aside
      className={`flex flex-col bg-ge-dark border-r border-ge-border flex-shrink-0 z-10 transition-all duration-250 ${
        sidebarCollapsed ? 'w-14' : 'w-[220px]'
      }`}
    >
      {/* Vendor mark (public/trinity-touch-logo.png) — the platform's branding, which
          is the same whichever plant is selected; the PLANT's identity lives in the
          header's site selector instead. The PNG carries its own opaque white
          background, so no chip or padding is added around it: it sits flush in the
          52px header band. object-contain preserves the original proportions. */}
      <div className={`flex items-center border-b border-ge-border min-h-[52px] ${sidebarCollapsed ? 'justify-center px-2' : 'px-4'}`}>
        <img
          src="/trinity-touch-logo.png"
          alt="Trinity Touch"
          className={`object-contain ${sidebarCollapsed ? 'h-8 w-10' : 'h-10 max-w-full'}`}
        />
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-2">
        {GROUPS.map(group => {
          const items = NAV_ITEMS.filter(
            i => i.group === group && !i.hidden && isPageEnabled(i.key))
          if (items.length === 0) return null
          return (
          <div key={group} className="py-1">
            {!sidebarCollapsed && (
              <div className="px-4 py-1.5 text-[9px] font-semibold text-ge-text3 uppercase tracking-widest">
                {group}
              </div>
            )}
            {items.map(item => (
              <NavLink
                key={item.path}
                to={item.path}
                className={({ isActive }) =>
                  'nav-item' + (isActive ? ' active' : '') +
                  (sidebarCollapsed ? ' justify-center px-0' : '')
                }
                title={sidebarCollapsed ? item.label : undefined}
              >
                <item.icon className="text-[17px] flex-shrink-0" />
                {!sidebarCollapsed && (
                  <span className="text-[13px] truncate">{item.label}</span>
                )}
              </NavLink>
            ))}
          </div>
          )
        })}
      </nav>

      {/* Collapse button */}
      <div className="p-3 border-t border-ge-border">
        <button
          onClick={() => setSidebarCollapsed(s => !s)}
          className="w-full flex items-center justify-center gap-1.5 py-1.5 bg-ge-surface
                     border border-ge-border rounded-md text-ge-text2 text-xs
                     hover:bg-ge-elevated hover:text-ge-text1 transition-all"
        >
          {sidebarCollapsed
            ? <MdChevronRight className="text-base" />
            : <><MdChevronLeft className="text-base" /><span>Collapse</span></>
          }
        </button>
      </div>
    </aside>
  )
}
