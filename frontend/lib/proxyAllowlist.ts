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
  if (!isSafePath(path)) return false;
  const verb = method.toUpperCase();
  if (verb === "GET") return READ_PREFIXES.some((prefix) => underPrefix(path, prefix));
  if (verb === "POST") return WRITE_PATHS.has(path);
  if (verb === "PUT") return SETTINGS_WRITE.test(path);
  return false;
}
