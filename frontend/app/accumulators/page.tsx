"use client";

import { useState } from "react";

import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Loading,
  NoQualified,
  Note,
  PageHeader,
  ProbabilityBar,
  Stat,
  rowClass,
  tableClass,
  tdClass,
  thClass,
  theadClass,
} from "@/components/ui";
import { eatDateTime, fmtOdds, fmtPercent, titleCase } from "@/lib/format";
import type { Combo, ComboResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const TARGETS = [2.0, 2.5, 3.0];

function ComboCard({ combo, label }: { combo: Combo; label: string }) {
  return (
    <div className="rounded-2xl border border-edge bg-pitch/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone="good">{label}</Badge>
          {combo.grade && <Badge tone="muted">Grade {combo.grade}</Badge>}
          {combo.rank && <Badge tone="muted">Rank #{combo.rank}</Badge>}
        </div>
        <p className="text-lg font-semibold tabular-nums text-lime">
          {fmtOdds(combo.total_odds)}
          <span className="ml-1 text-[11px] font-normal text-mist/45">total odds</span>
        </p>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div>
          <p className="text-mist/45">Joint probability</p>
          <p className="mt-0.5 font-semibold tabular-nums text-mist">
            {fmtPercent(combo.combined_probability)}
          </p>
        </div>
        <div>
          <p className="text-mist/45">Worst leg</p>
          <p className="mt-0.5 font-semibold tabular-nums text-mist">
            {fmtPercent(combo.min_leg_probability)}
          </p>
        </div>
        <div>
          <p className="text-mist/45">EV</p>
          <p
            className={`mt-0.5 font-semibold tabular-nums ${
              combo.expected_value_percent > 0 ? "text-lime" : "text-rose-200"
            }`}
          >
            {combo.expected_value_percent.toFixed(1)}%
          </p>
        </div>
        <div>
          <p className="text-mist/45">Suggested stake</p>
          <p className="mt-0.5 font-semibold tabular-nums text-mist">
            {combo.kelly_stake_percent.toFixed(2)}%
          </p>
        </div>
      </div>

      <ul className="mt-3 divide-y divide-edge/60">
        {combo.legs.map((leg) => (
          <li key={`${leg.fixture_id}-${leg.market_key}`} className="py-2.5 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="text-mist/90">{leg.selection}</p>
                <p className="text-[11px] text-mist/40">
                  {leg.match} · {leg.competition} · {eatDateTime(leg.kickoff_utc)}
                </p>
              </div>
              <div className="flex items-center gap-3 text-xs">
                <span className="text-mist/50">{titleCase(leg.market_type ?? leg.market_key)}</span>
                <span className="tabular-nums text-mist">{fmtOdds(leg.odds)}</span>
                <Badge tone={leg.odds_source === "market" ? "good" : "warn"}>
                  {leg.odds_source === "market" ? "bookmaker" : "estimate"}
                </Badge>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function AccumulatorsPage() {
  const [mode, setMode] = useState<"ev" | "certainty">("certainty");
  const [legs, setLegs] = useState(3);
  const [target, setTarget] = useState(2.5);
  const [bankroll, setBankroll] = useState(1000);
  const [minEdge, setMinEdge] = useState(3);
  const [includeSynthetic, setIncludeSynthetic] = useState(false);

  const params = new URLSearchParams({
    selection_mode: mode,
    num_legs: String(legs),
    min_total_odds: String(target),
    bankroll: String(bankroll),
    min_edge_percent: String(minEdge),
    allow_synthetic_odds: String(includeSynthetic),
  });

  const { data, loading, error, reload } = useApi<ComboResponse>(
    `/portfolio/combos?${params.toString()}`,
  );

  const combos: Combo[] = data
    ? [data.best_combo, ...data.alternatives].filter((combo): combo is Combo => Boolean(combo))
    : [];

  return (
    <div>
      <PageHeader
        eyebrow="Accumulators"
        title="Daily accumulator builder"
        description="Same-day slips built from independent fixtures. Certainty mode targets a genuine 85% joint win probability; EV mode targets a measured edge over the de-vigged bookmaker price. Correlation is not assumed away."
        actions={
          <button
            type="button"
            onClick={reload}
            className="border border-edge bg-panel text-mist/80 hover:border-lime/40 hover:text-lime"
          >
            Rebuild
          </button>
        }
      />

      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
          <label className="text-xs text-mist/60">
            Mode
            <select
              value={mode}
              onChange={(event) => setMode(event.target.value as "ev" | "certainty")}
              className="mt-1 w-full"
            >
              <option value="certainty">Certainty (85% joint)</option>
              <option value="ev">EV vs market</option>
            </select>
          </label>
          <label className="text-xs text-mist/60">
            Legs
            <select
              value={legs}
              onChange={(event) => setLegs(Number(event.target.value))}
              className="mt-1 w-full"
            >
              <option value={2}>2 legs</option>
              <option value={3}>3 legs</option>
            </select>
          </label>
          <label className="text-xs text-mist/60">
            Target odds
            <select
              value={target}
              onChange={(event) => setTarget(Number(event.target.value))}
              className="mt-1 w-full"
            >
              {TARGETS.map((value) => (
                <option key={value} value={value}>
                  {value.toFixed(2)}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-mist/60">
            Bankroll
            <input
              type="number"
              min={10}
              value={bankroll}
              onChange={(event) => setBankroll(Number(event.target.value))}
              className="mt-1 w-full"
            />
          </label>
          <label className="text-xs text-mist/60">
            Min edge % (EV)
            <input
              type="number"
              min={0}
              max={50}
              step={0.5}
              value={minEdge}
              onChange={(event) => setMinEdge(Number(event.target.value))}
              className="mt-1 w-full"
            />
          </label>
          <label className="flex items-end gap-2 text-xs text-mist/60">
            <input
              type="checkbox"
              checked={includeSynthetic}
              onChange={(event) => setIncludeSynthetic(event.target.checked)}
              className="mb-2 h-4 w-4 rounded border-edge bg-pitch accent-lime"
            />
            <span className="pb-2">Allow estimated prices (labelled)</span>
          </label>
        </div>
        {mode === "ev" && (
          <p className="mt-3 text-[11px] text-mist/45">
            EV mode refuses legs where ratings are missing or where the model and market disagree by
            more than the plausibility cap. Unvalidated edges stay blocked unless the backend is run
            with an explicit research flag.
          </p>
        )}
      </Card>

      {loading && <Loading label="Building slips" />}
      {error && <ErrorNote message={error} />}

      {data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Matchday" value={data.matchday_label} hint={`${data.matchday_matches_count} fixtures in scope`} />
            <Stat label="Candidate legs" value={data.total_candidate_legs} hint="Priced and eligible" />
            <Stat
              label="Slips generated"
              value={data.total_combos_generated}
              hint="All rules applied before ranking"
              tone={data.total_combos_generated > 0 ? "good" : "default"}
            />
            <Stat
              label="Mode"
              value={mode === "ev" ? "EV" : "Certainty"}
              hint={mode === "ev" ? `Min edge ${minEdge}%` : "85% joint floor"}
            />
          </div>

          <div className="mt-4">
            <Note tone={data.blocked_reason ? "warn" : "muted"}>
              {data.blocked_reason
                ? `Blocked: ${data.blocked_reason}`
                : data.odds_policy.note}
            </Note>
          </div>

          <div className="mt-6">
            <Card
              title="Top qualifying market structures"
              subtitle={`${data.top_picks.method}. One structure per fixture, weighted so probability carries the largest budget.`}
              actions={
                <Badge tone={data.top_picks.qualified > 0 ? "good" : "muted"}>
                  {data.top_picks.qualified} qualified
                </Badge>
              }
            >
              <div className="mb-3 flex flex-wrap gap-2">
                <Badge tone="good">Win probability ≥ {fmtPercent(data.top_picks.threshold, 0)}</Badge>
                <Badge tone="muted">Confidence ≥ {fmtPercent(data.top_picks.min_confidence, 0)}</Badge>
                <Badge tone="muted">{data.top_picks.evaluated_fixtures} fixtures evaluated</Badge>
                <Badge tone="muted">{data.top_picks.priced_fixtures} with real prices</Badge>
                {data.top_picks.estimates_skipped > 0 && (
                  <Badge tone="warn">{data.top_picks.estimates_skipped} estimated prices excluded</Badge>
                )}
              </div>

              {data.top_picks.picks.length === 0 ? (
                <NoQualified message={data.top_picks.message} />
              ) : (
                <div className="overflow-x-auto">
                  <table className={tableClass}>
                    <thead className={theadClass}>
                      <tr>
                        <th className={thClass}>Rank</th>
                        <th className={thClass}>Match</th>
                        <th className={thClass}>Structure</th>
                        <th className={thClass}>Selection</th>
                        <th className={thClass}>Odds</th>
                        <th className={thClass}>Win probability</th>
                        <th className={thClass}>Confidence</th>
                        <th className={thClass}>Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.top_picks.picks.map((pick) => (
                        <tr key={`${pick.fixture_id}-${pick.market_key}`} className={rowClass}>
                          <td className={`${tdClass} tabular-nums`}>#{pick.rank}</td>
                          <td className={tdClass}>
                            <p className="text-mist/90">{pick.match}</p>
                            <p className="text-[11px] text-mist/40">
                              {pick.competition} · {pick.kickoff_eat}
                            </p>
                          </td>
                          <td className={tdClass}>{pick.market_type}</td>
                          <td className={tdClass}>
                            <p>{pick.selection}</p>
                            {pick.runner_ups.length > 0 && (
                              <p className="mt-1 text-[11px] text-mist/40">
                                next:{" "}
                                {pick.runner_ups
                                  .map(
                                    (runner) =>
                                      `${runner.market_type} ${fmtPercent(runner.probability)} @ ${fmtOdds(runner.odds)}`,
                                  )
                                  .join(" · ")}
                              </p>
                            )}
                          </td>
                          <td className={`${tdClass} tabular-nums`}>
                            <div className="flex items-center gap-2">
                              <span>{fmtOdds(pick.odds)}</span>
                              <Badge tone={pick.odds_source === "market" ? "good" : "warn"}>
                                {pick.odds_source === "market" ? "bookmaker" : "estimate"}
                              </Badge>
                            </div>
                          </td>
                          <td className={tdClass}>
                            <ProbabilityBar value={pick.probability} threshold={data.top_picks.threshold} />
                            {pick.blended && (
                              <p className="mt-1 text-[11px] text-mist/40">
                                blend w {((pick.blend_weight ?? 0) * 100).toFixed(1)}%
                              </p>
                            )}
                          </td>
                          <td className={`${tdClass} tabular-nums`}>{fmtPercent(pick.confidence)}</td>
                          <td className={tdClass}>
                            <span className="font-semibold tabular-nums text-mist">{pick.score.toFixed(1)}</span>
                            <span className="ml-1 text-[11px] text-mist/45">{pick.grade}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="mt-3">
                <Note>
                  {data.top_picks.note} A high probability is not a guarantee, and EV is shown as
                  measured — these picks are not automatically +EV.
                </Note>
              </div>
            </Card>
          </div>

          {!data.blocked_reason && combos.length === 0 && (
            <div className="mt-4">
              <Empty>
                No accumulator satisfied every rule for {data.matchday_label}. No slip is the
                correct output — the rules are never relaxed to force one.
              </Empty>
            </div>
          )}

          {combos.length > 0 && (
            <div className="mt-6 grid gap-4 xl:grid-cols-2">
              {combos.map((combo, index) => (
                <ComboCard
                  key={`${combo.total_odds}-${index}`}
                  combo={combo}
                  label={index === 0 ? "Best slip" : `Alternative ${index}`}
                />
              ))}
            </div>
          )}

          {data.ranking.combos.length > 0 && (
            <div className="mt-6">
              <Card
                title="Ranked slips"
                subtitle={`${data.ranking.method} · highest first`}
              >
                <div className="overflow-x-auto">
                  <table className={tableClass}>
                    <thead className={theadClass}>
                      <tr>
                        <th className={thClass}>Rank</th>
                        <th className={thClass}>Grade</th>
                        <th className={thClass}>Legs</th>
                        <th className={thClass}>Odds</th>
                        <th className={thClass}>Joint</th>
                        <th className={thClass}>EV</th>
                        <th className={thClass}>Selections</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.ranking.combos.map((row) => (
                        <tr key={`${row.rank}-${row.total_odds}`} className={rowClass}>
                          <td className={`${tdClass} tabular-nums`}>#{row.rank ?? "—"}</td>
                          <td className={tdClass}>{row.grade ?? "—"}</td>
                          <td className={tdClass}>{row.legs_count}</td>
                          <td className={`${tdClass} tabular-nums`}>{fmtOdds(row.total_odds)}</td>
                          <td className={`${tdClass} tabular-nums`}>
                            <ProbabilityBar value={row.combined_probability} />
                          </td>
                          <td
                            className={`${tdClass} tabular-nums ${
                              row.expected_value_percent > 0 ? "text-lime" : "text-rose-200"
                            }`}
                          >
                            {row.expected_value_percent.toFixed(1)}%
                          </td>
                          <td className={`${tdClass} text-mist/60`}>{row.legs}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          )}

          {data.ranking.matches.length > 0 && (
            <div className="mt-4">
              <Card
                title="Ranked individual selections"
                subtitle="Highest composite score first. These are the legs that survived the gates."
              >
                <div className="overflow-x-auto">
                  <table className={tableClass}>
                    <thead className={theadClass}>
                      <tr>
                        <th className={thClass}>Rank</th>
                        <th className={thClass}>Match</th>
                        <th className={thClass}>Selection</th>
                        <th className={thClass}>Odds</th>
                        <th className={thClass}>Probability</th>
                        <th className={thClass}>Edge</th>
                        <th className={thClass}>Price source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.ranking.matches.map((row, index) => (
                        <tr key={`${row.match}-${row.selection}-${index}`} className={rowClass}>
                          <td className={`${tdClass} tabular-nums`}>#{row.rank ?? "—"}</td>
                          <td className={tdClass}>{row.match}</td>
                          <td className={tdClass}>{row.selection}</td>
                          <td className={`${tdClass} tabular-nums`}>{fmtOdds(row.odds)}</td>
                          <td className={tdClass}>
                            <ProbabilityBar value={row.probability} />
                          </td>
                          <td className={`${tdClass} tabular-nums`}>
                            {row.edge_percent === null ? "—" : `${row.edge_percent.toFixed(1)}%`}
                          </td>
                          <td className={tdClass}>
                            <Badge tone={row.odds_source === "market" ? "good" : "warn"}>
                              {row.odds_source}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          )}

          <div className="mt-4">
            <Note tone="warn">
              Accumulators multiply risk. A high joint probability is not a guarantee, and past
              accuracy does not repeat automatically. Stakes shown are quarter-Kelly suggestions at
              most — paper-trade for 4–8 weeks before treating any result as real.
            </Note>
          </div>
        </>
      )}
    </div>
  );
}
