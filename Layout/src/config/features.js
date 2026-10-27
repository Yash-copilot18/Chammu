// src/config/features.js
//
// Which pages this deployment exposes.
//
// A plant is connected one capability at a time: the reporting pages need their
// source tables present and their columns mapped, which is not true the moment a
// new site's database is attached. Rather than ship pages that render an error,
// this build enables only what has actually been wired up for the site.
//
// Nothing is deleted. Every page, route and component is still in the codebase;
// the sidebar and the router both read this list, so re-enabling a page is one
// line here and nothing else.

/**
 * Page keys that are switched on. Keys match `NAV_ITEMS[].key` in
 * components/Sidebar/Sidebar.jsx and the route table in routes/AppRoutes.jsx.
 *
 * Current deployment: forecasting only.
 */
export const ENABLED_PAGES = [
  'forecast',
]

/** Where "/" lands, and where a disabled or unknown URL is redirected. */
export const HOME_PATH = '/forecast'

export const isPageEnabled = key => ENABLED_PAGES.includes(key)
