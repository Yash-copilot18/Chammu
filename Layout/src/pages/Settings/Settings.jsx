// src/pages/Settings/Settings.jsx
import React, { useState } from 'react'
import { PageHeader, AsyncButton } from '../../components/Common'
import { getActiveSite } from '../../config/sites'

function SettingRow({ label, value, type = 'text', options }) {
  const [val, setVal] = useState(value)
  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-ge-border last:border-b-0">
      <span className="text-[12px] text-ge-text2 flex-1">{label}</span>
      {options ? (
        <select className="form-control w-44 text-[12px]" value={val}
          onChange={e => setVal(e.target.value)}>
          {options.map(o => <option key={o}>{o}</option>)}
        </select>
      ) : type === 'toggle' ? (
        <button
          onClick={() => setVal(v => !v)}
          className={`relative w-10 h-5 rounded-full transition-colors ${val ? 'bg-ge-accent' : 'bg-ge-border2'}`}
        >
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all shadow
                           ${val ? 'left-5' : 'left-0.5'}`} />
        </button>
      ) : (
        <input type={type} className="form-control w-44 text-[12px]"
          value={val} onChange={e => setVal(e.target.value)} />
      )}
    </div>
  )
}

function SettingCard({ title, icon, children }) {
  return (
    <div className="card">
      <div className="card-title">{icon} {title}</div>
      {children}
      <div className="mt-3 pt-3 border-t border-ge-border">
        <AsyncButton className="btn btn-success btn-sm" successMsg="Settings saved!">
          💾 Save Changes
        </AsyncButton>
      </div>
    </div>
  )
}

export default function Settings() {
  const site = getActiveSite()
  return (
    <div>
      <PageHeader title="System Settings" subtitle="Platform configuration and preferences">
        <AsyncButton className="btn btn-outline btn-sm" successMsg="Configuration exported!">
          ⬇ Export Config
        </AsyncButton>
      </PageHeader>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Seeded from the active site in src/config/sites.js. These fields are
            labels the operator supplies, never measurements — a site that has not
            been configured shows an empty field, not a placeholder plant. */}
        <SettingCard title="Plant Configuration" icon="🏭">
          <SettingRow label="Plant Name"       value={site.name} />
          <SettingRow label="Capacity (MW AC)" value={site.capacityMw ?? ''} type="number" />
          <SettingRow label="Capacity (MWp DC)" value={site.dcCapacityMwp ?? ''} type="number" />
          <SettingRow label="Location"         value={site.location ?? ''} />
          <SettingRow label="Inverters"        value={site.inverters ?? ''} type="number" />
          <SettingRow label="Trackers"         value={site.trackers ?? ''} type="number" />
          <SettingRow label="Timezone"         value={site.timezone || 'UTC'} />
          <SettingRow label="Currency"         value={site.currency ?? ''} />
        </SettingCard>

        <SettingCard title="Report Settings" icon="📊">
          <SettingRow label="Default Interval"    value="Hourly"
            options={['1 Minute','5 Minutes','15 Minutes','Hourly','Daily']} />
          <SettingRow label="Default Function"    value="Average"
            options={['Average','Minimum','Maximum','Sum']} />
          <SettingRow label="Report Format"       value="PDF + Excel"
            options={['PDF','Excel','PDF + Excel']} />
          <SettingRow label="Auto Archive (days)" value="30" type="number" />
          <SettingRow label="Decimal Places"      value="2" type="number" />
          <SettingRow label="Auto-schedule DGR"   value={true} type="toggle" />
        </SettingCard>

        <SettingCard title="Notification Settings" icon="🔔">
          <SettingRow label="Alarm Email"          value="ops@ge.com"   type="email" />
          <SettingRow label="Critical Threshold"   value="Immediate"
            options={['Immediate','5 Minutes','15 Minutes']} />
          <SettingRow label="Warning Threshold"    value="15 Minutes"
            options={['5 Minutes','15 Minutes','30 Minutes','1 Hour']} />
          <SettingRow label="SMTP Server"          value="smtp.ge.com" />
          <SettingRow label="SMTP Port"            value="587" type="number" />
          <SettingRow label="Email Notifications"  value={true}  type="toggle" />
          <SettingRow label="SMS Alerts"           value={false} type="toggle" />
        </SettingCard>

        <SettingCard title="Data Retention" icon="🗄">
          <SettingRow label="Raw Data (1-min)"   value="1 Year"
            options={['6 Months','1 Year','2 Years']} />
          <SettingRow label="Hourly Data"        value="5 Years"
            options={['1 Year','3 Years','5 Years','10 Years']} />
          <SettingRow label="Daily Data"         value="10 Years"
            options={['5 Years','10 Years','20 Years']} />
          <SettingRow label="Monthly Data"       value="Unlimited"
            options={['10 Years','20 Years','Unlimited']} />
          <SettingRow label="Auto Backup"        value={true}  type="toggle" />
          <SettingRow label="Backup Frequency"   value="Daily"
            options={['Daily','Weekly','Monthly']} />
        </SettingCard>

        <SettingCard title="API & Integration" icon="🔌">
          <SettingRow label="API Base URL"       value={site.apiUrl || 'Not configured'} />
          <SettingRow label="Polling Interval"   value="60" type="number" />
          <SettingRow label="Timeout (ms)"       value="30000" type="number" />
          <SettingRow label="API Enabled"        value={true} type="toggle" />
        </SettingCard>

        <SettingCard title="Display & UI" icon="🖥">
          <SettingRow label="Language"           value="English"
            options={['English','Arabic','French','German']} />
          <SettingRow label="Date Format"        value="DD/MM/YYYY"
            options={['DD/MM/YYYY','MM/DD/YYYY','YYYY-MM-DD']} />
          <SettingRow label="Time Format"        value="24H"
            options={['12H','24H']} />
          <SettingRow label="Power Unit"         value="MW"
            options={['kW','MW','GW']} />
          <SettingRow label="Energy Unit"        value="MWh"
            options={['kWh','MWh','GWh']} />
          <SettingRow label="Compact Sidebar"    value={false} type="toggle" />
        </SettingCard>
      </div>
    </div>
  )
}
