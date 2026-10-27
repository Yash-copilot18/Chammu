# SolarOps — Plant Monitoring

A standalone solar plant monitoring dashboard, independent of the Kalyon Niğde
build in `../Frontend`. Same pages, same features, same visual language — but no
plant hard-coded anywhere.

The two applications share no files, no build and no browser storage. Changing
one cannot affect the other.

---

## What is different from `../Frontend`

| | `../Frontend` (Kalyon) | `SolarOps-Frontend` (this) |
|---|---|---|
| Plant identity | hard-coded in 7 files | `src/config/sites.js`, one place |
| Header | `<h1>KALYON NIGDE 130 MW</h1>` | site picker: name + installed capacity |
| Backend URL | one, from `VITE_API_URL` | per site, so switching site switches backend |
| Storage keys | `ge_*`, `kalyon.*` | `solarops.*` |
| Plant metadata | none | location, AC/DC capacity, equipment counts, commissioning date |

Everything else — every page, chart, KPI, table, filter, report and export — is
carried over unchanged.

---

## Configuring a plant

### One plant

```bash
cp .env.example .env
# set VITE_API_URL to your backend
npm install
npm run dev
```

### Several plants

Set `VITE_SITES` to a JSON array. Each entry needs its own `apiUrl`:

```
VITE_SITES=[{"id":"north","name":"Northfield Solar Park","location":"Konya","capacityMw":130,"inverters":64,"apiUrl":"https://north.example.com"},{"id":"south","name":"Southgate Solar Park","location":"Antalya","capacityMw":85,"apiUrl":"https://south.example.com"}]
```

The picker then appears in the header. See `.env.example` for every field.

**Why one backend per site.** The API has no site dimension: every endpoint
answers for the single plant its database is attached to. A picker that changed
the name above the page while the numbers underneath stayed the same would be
presenting one plant's generation as another's. Switching site therefore
switches backend and reloads, so no response from the previous plant can land in
the new plant's charts.

A site configured without an `apiUrl` is labelled **Not connected**, and a banner
explains that the empty pages are unconfigured rather than an outage.

---

## Pages

| Route | Page |
|---|---|
| `/dashboard` | Operations Dashboard — plant information, 8 KPIs, power trend, irradiance vs power, daily energy |
| `/reports` | Report Automation — equipment selection, date/time configuration, exports |
| `/dgr` | Generation Reports — DGR / MGR / YGR, inverter-wise and monthly charts, PDF/Excel/CSV |
| `/analytics` | Analytics & Performance — daily, monthly, top-10, PR trend, performer tables |
| `/preconfigured` | Preconfigured Reports — saved templates, column builder |
| `/forecast` | Generation Forecast & Deviation Detection |
| `/scheduled` | Scheduled Reports |
| `/users` | User Management |

Reachable by URL but hidden from the sidebar (`hidden: true` in
`src/components/Sidebar/Sidebar.jsx`), exactly as in the original:
`/tracker`, `/tracker-status`, `/ppc-trend`, `/power-graph`, `/equipment`,
`/alarms`, `/settings`.

---

## Shared components

`src/components/Common/index.jsx` — `KpiCard`, `PageHeader`, `DataTable`,
`Pagination`, `SearchInput`, `Skeleton`, `Spinner`, `ProgressBar`, `StatusDot`,
`FormRow`, `FormGroup`, `Dialog`, `ConfirmDialog`, `PromptDialog`,
`ToastContainer`, `Modal`, and new in this build: **`StatusBadge`**,
**`InfoPanel`**, **`AlertCard`**, **`FactGrid`**.

`src/components/Site/` — `SiteInfoPanel`, `UnconfiguredBanner`.
`src/components/Header/SiteSelector.jsx` — the plant picker.

---

## Theme

Unchanged from the original: `--ge-*` CSS custom properties in `src/index.css`
for dark and light, surfaced through Tailwind as `ge-navy`, `ge-card`,
`ge-accent`, `ge-text1` and the rest. Dark is the default; the toggle is in the
header.

---

## Scripts

```bash
npm run dev        # Vite dev server
npm run build      # production build to dist/
npm run preview    # serve the build locally
```
