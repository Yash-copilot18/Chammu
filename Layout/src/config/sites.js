// src/config/sites.js
//
// The site registry — the one place this dashboard learns which solar plants it
// serves and what each one is called.
//
// WHY A REGISTRY AND NOT A CONSTANT
// The original Kalyon build hard-coded the plant name, capacity and location in
// seven separate files (header, Analytics, DGR Reports, Power Graph, Settings,
// Equipment, the PDF footer). Re-pointing it at another plant meant editing all
// seven. Here every one of those reads from the active site instead, so a new
// plant is a configuration change rather than a code change.
//
// ONE SITE = ONE BACKEND
// This is the important rule. The API exposes no site dimension: every endpoint
// answers for the single plant its database is attached to. A selector that
// switched the name above the page while the numbers underneath stayed the same
// would be presenting one plant's generation as another's, so each site carries
// its OWN `apiUrl`. Switching site switches backend. A site configured without
// one is shown as "not connected" and its pages render empty rather than
// borrowing figures from the site before it.
//
// HOW TO ADD A SITE
// Set VITE_SITES at build time to a JSON array (see .env.example). Nothing in
// this file needs editing. Values left null display as "—"; none of them are
// invented, and none of them are measurements — they are labels the operator
// supplies.

const STORAGE_KEY = 'solarops.active_site'

/**
 * The shape of a site. Every field is optional except `id` and `name`; anything
 * absent renders as an em dash rather than a guess.
 */
const SITE_FIELDS = {
  id: null,            // stable key, used in storage — never shown
  name: null,          // 'Northfield Solar Park'
  shortName: null,     // header chip when space is tight; falls back to `name`
  location: null,      // 'Konya, Türkiye'
  capacityMw: null,    // AC capacity, MW
  dcCapacityMwp: null, // DC capacity, MWp
  inverters: null,     // equipment counts, for the Equipment page header
  trackers: null,
  strings: null,
  commissioned: null,  // 'YYYY-MM'
  timezone: 'UTC',     // IANA name or a display label
  currency: null,
  apiUrl: '',          // backend for THIS site; empty → not connected
}

/** A single placeholder site so a fresh checkout runs and is obviously unconfigured. */
const TEMPLATE_SITE = {
  ...SITE_FIELDS,
  id: 'site-01',
  name: 'Solar Plant 01',
  shortName: 'SITE 01',
  apiUrl: import.meta.env.VITE_API_URL || '',
}

function normalise(raw, index) {
  const id = String(raw.id || `site-${String(index + 1).padStart(2, '0')}`)
  return {
    ...SITE_FIELDS,
    ...raw,
    id,
    name: raw.name || id,
    apiUrl: raw.apiUrl || '',
  }
}

/**
 * Parse VITE_SITES. A malformed value must not take the app down — it falls back
 * to the template site and says so in the console, which is a working dashboard
 * with a visible warning rather than a white screen.
 */
function loadSites() {
  const raw = import.meta.env.VITE_SITES
  if (!raw) return [TEMPLATE_SITE]
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length === 0) {
      throw new Error('VITE_SITES must be a non-empty JSON array')
    }
    const sites = parsed.map(normalise)
    const ids = new Set()
    for (const s of sites) {
      if (ids.has(s.id)) throw new Error(`duplicate site id "${s.id}"`)
      ids.add(s.id)
    }
    return sites
  } catch (e) {
    console.error('[sites] VITE_SITES could not be read — falling back to the ' +
                  'template site.', e)
    return [TEMPLATE_SITE]
  }
}

export const SITES = loadSites()

export function getSite(id) {
  return SITES.find(s => s.id === id) || SITES[0]
}

/**
 * The site the user last chose, or the first configured one.
 *
 * Read directly from localStorage rather than from React state, because
 * `services/api.js` needs the backend URL at module-load time — before any
 * provider has mounted. Keeping the lookup here means the API client and the UI
 * can never disagree about which site is active.
 */
export function getActiveSiteId() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && SITES.some(s => s.id === stored)) return stored
  } catch { /* storage blocked — fall through to the default */ }
  return SITES[0].id
}

export function getActiveSite() {
  return getSite(getActiveSiteId())
}

/**
 * Persist the choice and reload.
 *
 * The reload is deliberate. Every page holds data fetched from the PREVIOUS
 * site's backend, and several have polling intervals in flight. Swapping the
 * base URL underneath them would let a late response from the old site land in
 * the new site's charts. A reload guarantees that cannot happen, and it costs a
 * fraction of a second against the risk of showing one plant's output under
 * another plant's name.
 */
export function setActiveSiteId(id) {
  if (!SITES.some(s => s.id === id)) return
  if (id === getActiveSiteId()) return
  try { localStorage.setItem(STORAGE_KEY, id) } catch { /* ignore */ }
  window.location.reload()
}

/** True when this site has a backend to talk to. */
export const isConnected = site => Boolean(site && site.apiUrl)

/** '130 MW', or null when capacity has not been configured. */
export function capacityLabel(site) {
  if (!site || site.capacityMw == null) return null
  const n = Number(site.capacityMw)
  if (!Number.isFinite(n)) return null
  return `${n.toLocaleString()} MW`
}

/** 'Northfield Solar Park · 130 MW · Konya' for report headers and PDF footers. */
export function siteDescriptor(site) {
  return [site?.name, capacityLabel(site), site?.location]
    .filter(Boolean).join(' · ')
}
