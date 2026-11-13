import assert from "node:assert/strict";
import test from "node:test";

import { isProxied } from "./proxyAllowlist.ts";

test("reads the dashboard relies on are proxied", () => {
  for (const path of [
    "/health",
    "/bookmakers",
    "/connectors/status",
    "/fixtures",
    "/fixtures/live",
    "/fixtures/abc-123/analysis",
    "/ratings",
    "/markets/catalogue",
    "/portfolio/combos",
    "/evaluation/calibration",
    "/combos/daily",
    "/learning/summary",
    "/ledger/history",
    "/sources",
    "/settings",
    "/notifications",
    "/performance/summary",
    "/predictions/selections",
    "/ai/status",
  ]) {
    assert.equal(isProxied("GET", path), true, `GET ${path} should proxy`);
  }
});

test("writes the dashboard performs are proxied", () => {
  assert.equal(isProxied("POST", "/sources/refresh"), true);
  assert.equal(isProxied("POST", "/notifications"), true);
  assert.equal(isProxied("POST", "/combos/backtest"), true);
  assert.equal(isProxied("POST", "/combos/backtest/sweep"), true);
  assert.equal(isProxied("PUT", "/settings/combo_gate"), true);
});

test("dangerous engine operations stay unreachable from the browser", () => {
  for (const [method, path] of [
    ["POST", "/ledger/settle"],
    ["POST", "/fixtures/import-csv"],
    ["POST", "/fixtures/upload-csv"],
    ["POST", "/fixtures/seed-historical"],
    ["POST", "/fixtures/fetch-upcoming"],
    ["POST", "/fixtures/abc-123/quality"],
    ["POST", "/learning/train"],
    ["POST", "/ai/review"],
    ["POST", "/ratings"],
    ["PUT", "/fixtures/abc-123"],
    ["DELETE", "/settings/combo_gate"],
    ["DELETE", "/fixtures/abc-123"],
  ] as const) {
    assert.equal(isProxied(method, path), false, `${method} ${path} must not proxy`);
  }
});

test("unknown paths and traversal attempts are refused", () => {
  assert.equal(isProxied("GET", "/admin"), false);
  assert.equal(isProxied("GET", "/../secret"), false);
  assert.equal(isProxied("GET", "/fixtures/../../etc/passwd"), false);
  assert.equal(isProxied("GET", "//evil.example.com"), false);
});

test("prefix matching respects segment boundaries", () => {
  assert.equal(isProxied("GET", "/fixturesomething"), false);
  assert.equal(isProxied("GET", "/healthz"), false);
});
