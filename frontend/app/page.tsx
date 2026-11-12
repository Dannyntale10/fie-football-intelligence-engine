"use client";

import Link from "next/link";
import { useState } from "react";

import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Loading,
  Note,
  NoQualified,
  PageHeader,
  ProbabilityBar,
  Stat,
  rowClass,
  tableClass,
  tdClass,
  thClass,
  theadClass,
} from "@/components/ui";
import { apiGet, apiPost } from "@/lib/api";
import {
  eatDateTime,
  fmtOdds,
  fmtPercent,
  fmtSignedPercent,
  titleCase,
} from "@/lib/format";
import type {
  ComboBacktestReport,
  ComboMetrics,
  DailyCombo,
  DailyComboReport,
  Fixture,
  Health,
  PerformanceSummary,
  Prediction,
  QualifiedPolicy,
  SourcesRefreshResult,
  SourcesResponse,
} from "@/lib/types";
import { useApi } from "@/lib/useApi";

function ComboMetricsTable({ data }: { data: Record<string, ComboMetrics> }) {
  const rows = ["2_leg", "3_leg", "total"].filter((key) => data[key]?.count);
  if (rows.length === 0) {
    return <p className="text-xs text-mist/45">No combos met the tier in this window.</p>;
  }
  return (
    <table className={tableClass}>
      <thead className={theadClass}>
        <tr>
          <th className={thClass}>Ticket</th>
          <th className={thClass}>Settled</th>
          <th className={thClass}>Hit rate</th>
          <th className={thClass}>Predicted</th>
          <th className={thClass}>ROI</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((key) => {
          const row = data[key];
          const roi = row.roi_percent;
          return (
            <tr key={key} className={rowClass}>
              <td className={tdClass}>{key === "total" ? "Both" : key.replace("_leg", "-leg")}</td>
              <td className={tdClass}>{row.count}</td>
              <td className={tdClass}>{fmtPercent(row.hit_rate ?? null)}</td>
              <td className={tdClass}>{fmtPercent(row.average_predicted_probability ?? null)}</td>
              <td className={`${tdClass} tabular-nums ${roi !== undefined && roi !== null && roi < 0 ? "text-rose-300" : "text-lime"}`}>
                {roi === undefined || roi === null ? "—" : `${roi > 0 ? "+" : ""}${roi.toFixed(2)}%`}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const rejectionLabels: Record<string, string> = {
  placeholder_fixture: "feed placeholder rows",
  no_priced_market: "no stored price",
  missing_1x2: "incomplete 1X2",
  unattributed_price: "price without attribution",
  stale_price: "stale price",
  below_probability_floor: "below the tier",
  duplicate_match: "duplicate match rows",
};

function ComboTicket({ combo }: { combo: DailyCombo }) {
  return (
    <div className="rounded-xl border border-lime/30 bg-lime/5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-mist">{combo.size}-team combo</p>
        <div className="flex items-center gap-2">
          <Badge tone="good">{fmtPercent(combo.combined_probability)} win probability</Badge>
          <Badge tone="default">{fmtOdds(combo.total_odds)} total odds</Badge>
        </div>
      </div>
      <ul className="mt-3 divide-y divide-edge/50">
        {combo.legs.map((leg) => (
          <li key={leg.fixture_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <div className="min-w-0">
              <p className="text-sm text-mist">{leg.match}</p>
              <p className="text-[11px] text-mist/45">
                {leg.selection} · {leg.kickoff_eat} · {leg.competition}
              </p>
            </div>
            <div className="text-right text-xs">
              <p className="tabular-nums text-mist/85">{fmtOdds(leg.odds)} · {fmtPercent(leg.probability)}</p>
              <p className="text-[11px] text-mist/45">
                {leg.bookmaker ?? "unattributed"}
                {leg.price_age_hours !== null && leg.price_age_hours !== undefined
                  ? ` · ${leg.price_age_hours.toFixed(1)} h old`
                  : ""}
              </p>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-mist/50">
        De-vigged market probability {fmtPercent(combo.implied_probability ?? null)} implied by the price.
        {combo.expected_value_percent !== null && combo.expected_value_percent !== undefined
          ? ` Expected value ${combo.expected_value_percent > 0 ? "+" : ""}${combo.expected_value_percent.toFixed(2)}%.`
          : ""}
      </p>
    </div>
  );
}

export default function OverviewPage() {
  const health = useApi<Health>("/health");
  const fixtures = useApi<Fixture[]>("/fixtures?upcoming_only=true&limit=8");
  const performance = useApi<PerformanceSummary>("/performance/summary");
  const predictions = useApi<{ count: number; predictions: Prediction[] }>(
    "/predictions?qualified_only=true&limit=6",
  );
  const sources = useApi<SourcesResponse>("/sources");
  const policy = useApi<QualifiedPolicy>("/markets/qualification-policy");

  const [comboReport, setComboReport] = useState<DailyComboReport | null>(null);
  const [comboLoading, setComboLoading] = useState(false);
  const [comboRefreshing, setComboRefreshing] = useState(false);
  const [comboError, setComboError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  const [backtest, setBacktest] = useState<ComboBacktestReport | null>(null);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [backtestError, setBacktestError] = useState<string | null>(null);

  const qualified = predictions.data?.predictions ?? [];
  const gate = policy.data?.minimum_probability ?? 0.85;

  async function analyseSlate() {
    setComboLoading(true);
    setComboError(null);
    setRefreshError(null);
    setRefreshNote(null);

    // Show the analysis from stored prices first: a full feed sync can hang on an
    // unreachable bookmaker host for minutes, and the report must not wait for it.
    try {
      setComboReport(await apiGet<DailyComboReport>("/combos/daily"));
    } catch (error) {
      setComboError(error instanceof Error ? error.message : String(error));
    } finally {
      setComboLoading(false);
    }

    setComboRefreshing(true);
    try {
      const refreshed = await apiPost<SourcesRefreshResult>("/sources/refresh");
      setComboReport(await apiGet<DailyComboReport>("/combos/daily"));

      const feeds = Object.values(refreshed.feeds ?? {});
      const failed = feeds.filter((feed) => feed.status === "failed").length;
      const totals = refreshed.totals ?? {};
      setRefreshNote(
        `Synced ${feeds.length - failed}/${feeds.length} bookmaker feeds · ` +
          `${totals.odds_updated ?? 0} prices updated · ` +
          `${totals.inserted_new ?? 0} new fixtures.`,
      );
      if (failed > 0) {
        const reasons = feeds
          .filter((feed) => feed.status === "failed" && feed.error)
          .map((feed) => `${feed.provider}: ${feed.error}`);
        setRefreshError(
          `${failed} bookmaker feed(s) unreachable, so their prices were not refreshed${
            reasons.length ? ` (${reasons.join("; ")})` : ""
          }.`,
        );
      }
    } catch (error) {
      // The report above still stands on the last stored prices.
      setRefreshError(error instanceof Error ? error.message : String(error));
    } finally {
      setComboRefreshing(false);
    }
  }

  async function runHistoryTest() {
    setBacktestLoading(true);
    setBacktestError(null);
    try {
      setBacktest(await apiPost<ComboBacktestReport>("/combos/backtest"));
    } catch (error) {
      setBacktestError(error instanceof Error ? error.message : String(error));
    } finally {
      setBacktestLoading(false);
    }
  }

  const rejected = Object.entries(comboReport?.scope.rejected ?? {}).filter(
    ([, count]) => count > 0,
  );

  return (
    <div>
      <PageHeader
        eyebrow="Control room"
        title="Football Intelligence Overview"
        description="One place to see the fixture slate, the qualified shortlist, provider readiness and the honest performance ledger. Every number here is measured, not projected."
        actions={
          <Badge tone={health.data?.status === "ok" ? "good" : "bad"}>
            API {health.data?.status ?? (health.error ? "offline" : "…")}
          </Badge>
        }
      />

      {health.error && <ErrorNote message={health.error} />}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Fixtures tracked"
          value={health.data?.total_fixtures ?? "—"}
          hint={health.data ? `Database ${health.data.database}` : "Awaiting backend"}
        />
        <Stat
          label="Upcoming slate"
          value={fixtures.data ? fixtures.data.length : "—"}
          hint="Next fixtures in the local database"
        />
        <Stat
          label="Real settled slips"
          value={performance.data ? performance.data.settled : "—"}
          hint={performance.data?.scope ?? "Synthetic data is excluded"}
        />
        <Stat
          label="Hit rate"
          value={fmtPercent(performance.data?.accuracy ?? null)}
          hint={
            performance.data
              ? `${performance.data.wins}W · ${performance.data.losses}L · ${performance.data.voids}V`
              : undefined
          }
          tone={
            performance.data?.accuracy && performance.data.accuracy >= 0.5 ? "good" : "default"
          }
        />
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="ROI"
          value={fmtSignedPercent(performance.data?.roi ?? null)}
          hint={performance.data ? `Profit ${performance.data.profit.toFixed(2)}` : undefined}
          tone={
            performance.data?.roi && performance.data.roi < 0 ? "bad" : "default"
          }
        />
        <Stat
          label="Qualification gate"
          value={fmtPercent(gate, 0)}
          hint={`Confidence ≥ ${fmtPercent(policy.data?.minimum_confidence ?? 0.6, 0)}`}
        />
        <Stat
          label="Providers configured"
          value={sources.data ? sources.data.configured_count : "—"}
          hint={sources.data ? `${sources.data.providers.length} adapters known` : undefined}
        />
        <Stat
          label="Modes"
          value="Paper only"
          hint="Automatic bet placement is disabled by policy"
        />
      </div>

      <Card
        className="mt-6"
        title="Today's 2–3 team combos"
        subtitle="One selection per fixture from today's real bookmaker prices, de-vigged. The 75% tier is exploratory and sits below the published 85% gate; the gate itself is unchanged."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={analyseSlate}
              disabled={comboLoading || comboRefreshing}
              className="border border-lime/40 bg-lime/10 text-lime hover:bg-lime/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {comboLoading
                ? "Analysing stored prices…"
                : comboRefreshing
                  ? "Refreshing bookmaker feeds…"
                  : "Analyse today's slate"}
            </button>
            <button
              type="button"
              onClick={runHistoryTest}
              disabled={backtestLoading}
              className="border border-edge bg-panel text-mist/80 hover:border-lime/40 hover:text-lime disabled:cursor-not-allowed disabled:opacity-60"
            >
              {backtestLoading ? "Replaying history…" : "Test on history"}
            </button>
          </div>
        }
      >
        {comboLoading && <Loading label="Analysing the stored bookmaker prices" />}
        {comboRefreshing && (
          <Loading label="Refreshing the public bookmaker feeds; the report below uses stored prices meanwhile" />
        )}
        {refreshError && (
          <Note tone="warn">
            {refreshError} The report below was rebuilt from whatever prices are stored.
          </Note>
        )}
        {refreshNote && <Note>{refreshNote}</Note>}
        {comboError && <ErrorNote message={comboError} />}
        {!comboReport && !comboLoading && !comboError && (
          <Note>
            Press “Analyse today's slate” to refresh the public bookmaker feeds and build 2- and
            3-team combos from today's attributed prices. With no qualifying legs the answer is
            NO QUALIFIED SELECTIONS FOUND — never a fabricated pick.
          </Note>
        )}

        {comboReport && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-xs text-mist/55">
              <Badge tone={comboReport.tier.below_published_gate ? "warn" : "good"}>
                {comboReport.tier.label}
              </Badge>
              <span>{comboReport.matchday_label}</span>
              <span>· {comboReport.scope.fixtures_on_slate} on the slate</span>
              <span>
                · {comboReport.scope.qualified_legs} legs at or above{" "}
                {fmtPercent(comboReport.params.min_leg_probability, 0)}
              </span>
            </div>

            {comboReport.best ? (
              <ComboTicket combo={comboReport.best} />
            ) : (
              <Empty>
                <p className="text-sm font-semibold text-mist/80">{comboReport.message}</p>
                <p className="mt-2 text-xs text-mist/45">
                  Nothing met the {fmtPercent(comboReport.params.min_leg_probability, 0)}{" "}
                  market-probability tier with an attributed price. The 85% published gate is
                  unchanged.
                </p>
              </Empty>
            )}

            {comboReport.legs.length > 0 && (
              <div>
                <p className="mb-2 text-[11px] uppercase tracking-wider text-mist/45">Leg pool</p>
                <div className="overflow-x-auto">
                  <table className={tableClass}>
                    <thead className={theadClass}>
                      <tr>
                        <th className={thClass}>Match</th>
                        <th className={thClass}>Selection</th>
                        <th className={thClass}>Odds</th>
                        <th className={thClass}>Probability</th>
                        <th className={thClass}>Bookmaker</th>
                        <th className={thClass}>Price age</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comboReport.legs.map((leg) => (
                        <tr key={leg.fixture_id} className={rowClass}>
                          <td className={tdClass}>
                            <p>{leg.match}</p>
                            <p className="text-[11px] text-mist/45">
                              {leg.kickoff_eat} · {leg.competition}
                            </p>
                          </td>
                          <td className={tdClass}>{leg.selection}</td>
                          <td className={`${tdClass} tabular-nums`}>{fmtOdds(leg.odds)}</td>
                          <td className={`${tdClass} tabular-nums`}>
                            {fmtPercent(leg.probability)}{" "}
                            <span className="text-[11px] text-mist/40">
                              ({fmtPercent(leg.market_probability)} market)
                            </span>
                          </td>
                          <td className={tdClass}>
                            {leg.bookmaker ?? "—"}
                            <p className="text-[11px] text-mist/40">{leg.source ?? ""}</p>
                          </td>
                          <td className={`${tdClass} tabular-nums`}>
                            {leg.price_age_hours === null
                              ? "—"
                              : `${leg.price_age_hours.toFixed(1)} h`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {rejected.length > 0 && (
              <p className="text-[11px] text-mist/45">
                Excluded:{" "}
                {rejected
                  .map(([key, count]) => `${rejectionLabels[key] ?? key} (${count})`)
                  .join(" · ")}
              </p>
            )}

            <Note>
              {comboReport.learning.applied
                ? `Calibration learned from ${comboReport.learning.n ?? 0} settled legs (a=${comboReport.learning.a}, b=${comboReport.learning.b}).`
                : "No validated calibration yet; these probabilities are the raw de-vigged market prices."}{" "}
              {comboReport.note}
            </Note>
          </div>
        )}

        {(backtestLoading || backtestError || backtest) && (
          <div className="mt-5 border-t border-edge/60 pt-4">
            <p className="mb-3 text-sm font-semibold text-mist">Historical test</p>
            {backtestLoading && <Loading label="Replaying settled fixtures" />}
            {backtestError && <ErrorNote message={backtestError} />}
            {backtest && backtest.status !== "ok" && (
              <ErrorNote message={backtest.message ?? "No settled history available."} />
            )}
            {backtest?.status === "ok" && (
              <>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <Stat
                    label="Settled matches"
                    value={backtest.scope.settled_fixtures}
                    hint={
                      backtest.window
                        ? `${backtest.window.matchdays} matchdays · ${backtest.window.first_matchday} → ${backtest.window.last_matchday}`
                        : undefined
                    }
                  />
                  <Stat
                    label="2-leg holdout"
                    value={fmtPercent(backtest.combos?.holdout["2_leg"]?.hit_rate ?? null)}
                    hint={`${backtest.combos?.holdout["2_leg"]?.count ?? 0} top tickets · ${backtest.params.price_mode} prices`}
                  />
                  <Stat
                    label="3-leg holdout"
                    value={fmtPercent(backtest.combos?.holdout["3_leg"]?.hit_rate ?? null)}
                    hint={`${backtest.combos?.holdout["3_leg"]?.count ?? 0} top tickets`}
                  />
                  <Stat
                    label="Learning"
                    value={backtest.learning.applied ? "Applied" : "Not applied"}
                    hint={`${backtest.learning.n} settled legs in the fit`}
                    tone={backtest.learning.applied ? "good" : "default"}
                  />
                </div>
                <div className="mt-4 grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
                  <div>
                    <p className="mb-2 text-[11px] uppercase tracking-wider text-mist/45">
                      Holdout · latest {Math.round(backtest.params.holdout_share * 100)}%
                    </p>
                    <ComboMetricsTable data={backtest.combos?.holdout ?? {}} />
                  </div>
                  {backtest.holdout_after_learning && (
                    <div>
                      <p className="mb-2 text-[11px] uppercase tracking-wider text-mist/45">
                        Holdout · after learning
                      </p>
                      <ComboMetricsTable data={backtest.holdout_after_learning} />
                    </div>
                  )}
                  <div>
                    <p className="mb-2 text-[11px] uppercase tracking-wider text-mist/45">
                      All history
                    </p>
                    <ComboMetricsTable data={backtest.combos?.all ?? {}} />
                  </div>
                </div>
                <p className="mt-3 text-[11px] text-mist/45">
                  Top-ranked ticket per size per matchday, flat stakes. ROI is measured at
                  historical prices; a negative ROI is expected at a bookmaker's own margin.
                </p>
                {backtest.learning.applied && (
                  <div className="mt-3">
                    <Note>
                      Calibration stored for live use: a={backtest.learning.a}, b=
                      {backtest.learning.b} ({backtest.learning.n} settled legs). Holdout log loss{" "}
                      {backtest.learning.logloss_before?.toFixed(4)} →{" "}
                      {backtest.learning.logloss_after?.toFixed(4)} (improvement{" "}
                      {backtest.learning.improvement?.toFixed(4)}).
                      {backtest.holdout_after_learning
                        ? " The after-learning table re-selects the holdout with a calibration fitted on training matchdays only, so it is not in-sample."
                        : " A training-only calibration did not clear its own held-out bar, so no after-learning holdout comparison is published."}
                    </Note>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </Card>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Qualified selections"
          subtitle={`Only markets at or above ${fmtPercent(gate, 0)} probability with adequate data confidence appear here.`}
          actions={
            <Link href="/predictions" className="text-xs text-lime hover:text-lime/80">
              All predictions →
            </Link>
          }
        >
          {predictions.loading && <Loading label="Loading qualified predictions" />}
          {predictions.error && <ErrorNote message={predictions.error} />}
          {!predictions.loading && !predictions.error && qualified.length === 0 && (
            <NoQualified
              message={policy.data?.message_when_empty ?? "NO QUALIFIED SELECTIONS FOUND."}
            />
          )}
          {qualified.length > 0 && (
            <ul className="divide-y divide-edge/60">
              {qualified.map((row) => (
                <li key={row.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-mist">
                      {titleCase(row.family)}
                      <span className="text-mist/45"> · {row.selection}</span>
                    </p>
                    <p className="mt-0.5 text-[11px] text-mist/40">
                      Fixture #{row.fixture_id ?? "—"} · fair odds{" "}
                      {row.fair_odds ? row.fair_odds.toFixed(2) : "—"} ·{" "}
                      {eatDateTime(row.created_at)}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <ProbabilityBar value={row.probability} threshold={gate} />
                    <Badge tone={row.qualified ? "good" : "muted"}>
                      {row.qualified ? "Qualified" : "Not qualified"}
                    </Badge>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Next fixtures"
          subtitle="Sorted by kickoff, times in EAT."
          actions={
            <Link href="/fixtures" className="text-xs text-lime hover:text-lime/80">
              Open slate →
            </Link>
          }
        >
          {fixtures.loading && <Loading label="Loading fixtures" />}
          {fixtures.error && <ErrorNote message={fixtures.error} />}
          {!fixtures.loading && !fixtures.error && (fixtures.data?.length ?? 0) === 0 && (
            <Empty>
              No upcoming fixtures stored. Import or fetch a slate from the backend before running
              models.
            </Empty>
          )}
          <ul className="divide-y divide-edge/60">
            {(fixtures.data ?? []).map((fixture) => (
              <li key={fixture.id} className="py-3">
                <p className="text-sm text-mist">
                  {fixture.home_team} <span className="text-mist/40">vs</span> {fixture.away_team}
                </p>
                <p className="mt-0.5 text-[11px] text-mist/40">
                  {eatDateTime(fixture.kickoff_utc)} · {fixture.competition} · {fixture.source}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Daily rhythm (EAT)" subtitle="Configured schedule — editable in Settings.">
          <ol className="space-y-2 text-sm">
            {[
              ["06:00", "Fixture scan"],
              ["06:30", "Statistics sync"],
              ["07:00", "Model run"],
              ["07:30", "Qualified shortlist"],
              ["Every 30 min", "Odds refresh"],
              ["After FT", "Results & settlement"],
            ].map(([time, task]) => (
              <li key={task} className="flex items-center justify-between gap-3">
                <span className="text-mist/50 text-xs tabular-nums">{time}</span>
                <span className="text-mist/85">{task}</span>
              </li>
            ))}
          </ol>
        </Card>

        <Card title="Provider readiness" subtitle="Free-plan adapters only.">
          {sources.loading && <Loading label="Loading providers" />}
          {sources.error && <ErrorNote message={sources.error} />}
          {sources.data && (
            <ul className="space-y-2 text-sm">
              {sources.data.providers.map((provider) => (
                <li key={provider.name} className="flex items-center justify-between gap-2">
                  <span className="text-mist/85">{provider.name}</span>
                  <Badge tone={provider.configured ? "good" : "muted"}>
                    {provider.configured ? "Configured" : "Needs key"}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Rules this platform will not break">
          <ul className="space-y-2 text-xs leading-relaxed text-mist/60">
            <li>• Never fabricate fixtures, odds, injuries, lineups or statistics.</li>
            <li>• Never lower the 85% gate to fill a report.</li>
            <li>• Never present an estimate as a BetPawa price.</li>
            <li>• Never claim a market was analysed when it was not priced.</li>
            <li>• Never hide losing predictions — history is append-only.</li>
            <li>• Never place bets automatically.</li>
          </ul>
        </Card>
      </div>

      {performance.data?.note && (
        <div className="mt-4">
          <Note>{performance.data.note}</Note>
        </div>
      )}
    </div>
  );
}
