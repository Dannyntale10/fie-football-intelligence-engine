/**
 * Which engine routes the dashboard proxy forwards.
 *
 * The proxy holds the API key server-side, so an open passthrough would hand
 * every engine route to anyone who loads the dashboard. Reads are allowed by
 * prefix; writes only for the exact calls the pages make.
 */

const READ_PREFIXES = [
  "/health",
  "/bookmakers",
  "/connectors/status",
  "/fixtures",
  "/ratings",
  "/markets",
  "/bankroll",
  "/portfolio",
  "/evaluation",
  "/combos",
  "/learning",
  "/live/calibration",
  "/ledger",
  "/sources",
  "/settings",
  "/notifications",
  "/performance",
  "/predictions",
  "/ai/status",
];

const WRITE_PATHS = new Set([
  "/sources/refresh",
  "/notifications",
  "/combos/backtest",
  "/combos/backtest/sweep",
]);

const SETTINGS_WRITE = /^\/settings\/[A-Za-z0-9_.-]+$/;

function underPrefix(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

function isSafePath(path: string): boolean {
  if (!path.startsWith("/") || path.startsWith("//")) return false;
  return !path.split("/").some((segment) => segment === ".." || segment === ".");
}

export function isProxied(method: string, path: string): boolean {
  // Callers may hand over the browser path including its query string; only
  // the path decides routing, and the proxy forwards the search separately.
  const route = path.split(/[?#]/)[0];
  if (!isSafePath(route)) return false;
  const verb = method.toUpperCase();
  if (verb === "GET") return READ_PREFIXES.some((prefix) => underPrefix(route, prefix));
  if (verb === "POST") return WRITE_PATHS.has(route);
  if (verb === "PUT") return SETTINGS_WRITE.test(route);
  return false;
}
