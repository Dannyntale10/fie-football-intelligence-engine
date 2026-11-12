"use client";

import { useEffect, useState } from "react";

import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Loading,
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
import { fmtNumber, fmtOdds, fmtPercent, titleCase } from "@/lib/format";
import type { Dossier, Fixture } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const SEVERITY_TONES: Record<string, "bad" | "warn" | "muted"> = {
  high: "bad",
  medium: "warn",
  low: "muted",
};

function FormBlock({ form }: { form: Dossier["form"]["home"] }) {
  if (!form) return <Empty>No finished matches stored for this team.</Empty>;
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-mist">{form.team}</p>
        <Badge tone="muted">{form.played} played</Badge>
      </div>
      <p className="mt-1 font-mono text-sm tracking-[0.3em] text-lime">
        {form.form || "—"}
      </p>
      <p className="mt-1 text-xs text-mist/50">
        {form.record} · {form.points_per_game.toFixed(2)} PPG
      </p>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div>
          <p className="text-mist/40">Goals for / match</p>
          <p className="tabular-nums text-mist/85">{fmtNumber(form.goals_for_per_match)}</p>
        </div>
        <div>
          <p className="text-mist/40">Goals against / match</p>
          <p className="tabular-nums text-mist/85">{fmtNumber(form.goals_against_per_match)}</p>
        </div>
        <div>
          <p className="text-mist/40">Clean sheets</p>
          <p className="tabular-nums text-mist/85">{fmtPercent(form.clean_sheet_rate, 0)}</p>
        </div>
        <div>
          <p className="text-mist/40">BTTS rate</p>
          <p className="tabular-nums text-mist/85">{fmtPercent(form.btts_rate, 0)}</p>
        </div>
      </div>
    </div>
  );
}

export default function AnalysisPage() {
  const [fixtureId, setFixtureId] = useState("");

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("fixture");
    if (requested) setFixtureId(requested);
  }, []);

  const fixtures = useApi<Fixture[]>("/fixtures?limit=300&descending=true");
  const dossier = useApi<Dossier>(fixtureId ? `/fixtures/${fixtureId}/deep-analysis` : null);

  const data = dossier.data;

  return (
    <div>
      <PageHeader
        eyebrow="Match analysis"
        title="Per-match deep dossier"
        description="Form splits, head-to-head with recency caveats, the model-versus-market view, advisory team news and every data risk. Nothing here is a guarantee; missing feeds are named instead of filled in."
      />

      <Card className="mb-4">
        <label className="block max-w-xl text-xs text-mist/60">
          Fixture
          <select
            value={fixtureId}
            onChange={(event) => setFixtureId(event.target.value)}
            className="mt-1 w-full"
          >
            <option value="">Select a fixture…</option>
            {(fixtures.data ?? []).map((fixture) => (
              <option key={fixture.id} value={fixture.id}>
                {fixture.home_team} vs {fixture.away_team} · {fixture.competition}
              </option>
            ))}
          </select>
        </label>
      </Card>

      {!fixtureId && (
        <Empty>Choose a fixture to build its dossier.</Empty>
      )}

      {dossier.loading && <Loading label="Building dossier" />}
      {dossier.error && <ErrorNote message={dossier.error} />}

      {data && (
        <div className="space-y-4">
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-[11px] uppercase tracking-[0.2em] text-mist/40">
                  {data.fixture.competition} · {data.fixture.country}
                </p>
                <h2 className="mt-1 text-2xl font-semibold text-white">{data.fixture.match}</h2>
                <p className="mt-1 text-xs text-mist/50">
                  {data.fixture.kickoff_eat} EAT · status {data.fixture.status} · source{" "}
                  {data.fixture.source}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge tone={data.verdict.model_validated ? "good" : "warn"}>
                  Model {data.verdict.model_validated ? "validated" : "not validated"}
                </Badge>
                <Badge tone={data.verdict.ratings_fresh ? "good" : "bad"}>
                  Ratings {data.verdict.ratings_fresh ? "fresh" : "stale/missing"}
                </Badge>
                <Badge tone="muted">confidence {data.verdict.confidence}</Badge>
              </div>
            </div>
            <div className="mt-4">
              <Note tone={data.verdict.confidence === "low" ? "warn" : "muted"}>
                {data.verdict.headline}
              </Note>
            </div>
          </Card>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat
              label="Home expected goals"
              value={data.model.lambdas ? fmtNumber(data.model.lambdas.home_lambda) : "—"}
              hint={data.model.lambda_source ?? "unknown source"}
            />
            <Stat
              label="Away expected goals"
              value={data.model.lambdas ? fmtNumber(data.model.lambdas.away_lambda) : "—"}
              hint={data.model.lambda_source ?? "unknown source"}
            />
            <Stat
              label="Bookmaker margin"
              value={
                data.model.bookmaker_margin
                  ? `${Number(data.model.bookmaker_margin.margin_percent ?? 0).toFixed(2)}%`
                  : "—"
              }
              hint="Overround across the 1X2 market"
            />
            <Stat
              label="Analysed markets"
              value={data.markets.length}
              hint={`${data.best_bets.length} show positive measured value`}
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card title="Recent form" subtitle="Overall record from finished matches in the database.">
              <div className="grid gap-4 sm:grid-cols-2">
                <FormBlock form={data.form.home} />
                <FormBlock form={data.form.away} />
              </div>
            </Card>

            <Card title="Head-to-head" subtitle="Recency caveat applies: old meetings age quickly.">
              {data.h2h.total === 0 ? (
                <Empty>No head-to-head history stored. This is a gap, not a balanced record.</Empty>
              ) : (
                <div className="text-sm">
                  <p className="font-mono tracking-[0.3em] text-lime">{data.h2h.form_string}</p>
                  <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                    <div className="rounded-lg border border-edge bg-pitch/40 p-2">
                      <p className="text-mist/40">Home wins</p>
                      <p className="mt-1 text-lg font-semibold tabular-nums">{data.h2h.home_wins}</p>
                    </div>
                    <div className="rounded-lg border border-edge bg-pitch/40 p-2">
                      <p className="text-mist/40">Draws</p>
                      <p className="mt-1 text-lg font-semibold tabular-nums">{data.h2h.draws}</p>
                    </div>
                    <div className="rounded-lg border border-edge bg-pitch/40 p-2">
                      <p className="text-mist/40">Away wins</p>
                      <p className="mt-1 text-lg font-semibold tabular-nums">{data.h2h.away_wins}</p>
                    </div>
                  </div>
                  <p className="mt-3 text-xs text-mist/50">
                    {data.h2h.total} meetings · {fmtNumber(data.h2h.avg_total_goals)} goals per game
                    on average.
                  </p>
                </div>
              )}
            </Card>

            <Card title="Team news" subtitle="Advisory only unless a verified feed supplied it.">
              <div className="space-y-3 text-xs leading-relaxed text-mist/65">
                <div className="flex flex-wrap gap-2">
                  <Badge tone={data.team_news.source === "verified" ? "good" : "warn"}>
                    {data.team_news.source}
                  </Badge>
                  <Badge tone={data.team_news.verdict === "VETO" ? "bad" : "muted"}>
                    {data.team_news.verdict}
                  </Badge>
                </div>
                <p>
                  <span className="text-mist/40">Injuries: </span>
                  {data.team_news.injuries}
                </p>
                <p>
                  <span className="text-mist/40">Form: </span>
                  {data.team_news.form_summary}
                </p>
                <p>
                  <span className="text-mist/40">Risk: </span>
                  {data.team_news.risk_note}
                </p>
              </div>
            </Card>
          </div>

          <Card
            title="Model vs market"
            subtitle="Edge and EV are only computed against real, de-vigged bookmaker prices."
          >
            {data.markets.length === 0 ? (
              <Empty>
                No priced markets are stored for this fixture, so no edge can be measured.
              </Empty>
            ) : (
              <div className="overflow-x-auto">
                <table className={tableClass}>
                  <thead className={theadClass}>
                    <tr>
                      <th className={thClass}>Selection</th>
                      <th className={thClass}>Bookmaker odds</th>
                      <th className={thClass}>Model</th>
                      <th className={thClass}>Fair odds</th>
                      <th className={thClass}>Edge</th>
                      <th className={thClass}>EV</th>
                      <th className={thClass}>Calibration</th>
                      <th className={thClass}>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.markets.map((row) => (
                      <tr key={`${row.market_key}-${row.selection}`} className={rowClass}>
                        <td className={tdClass}>
                          <p className="text-mist/90">{row.selection}</p>
                          <p className="text-[11px] text-mist/40">{row.market_type}</p>
                        </td>
                        <td className={`${tdClass} tabular-nums`}>{fmtOdds(row.price)}</td>
                        <td className={tdClass}>
                          <ProbabilityBar value={row.model_probability} />
                        </td>
                        <td className={`${tdClass} tabular-nums`}>{fmtOdds(row.fair_odds)}</td>
                        <td
                          className={`${tdClass} tabular-nums ${
                            row.edge_percent > 0 ? "text-lime" : "text-mist/55"
                          }`}
                        >
                          {row.edge_percent.toFixed(1)}%
                        </td>
                        <td
                          className={`${tdClass} tabular-nums ${
                            row.expected_value_percent > 0 ? "text-lime" : "text-mist/55"
                          }`}
                        >
                          {row.expected_value_percent.toFixed(1)}%
                        </td>
                        <td className={tdClass}>
                          <Badge tone={row.calibration_applied ? "good" : "muted"}>
                            {row.calibration_applied ? "learned" : "raw"}
                          </Badge>
                        </td>
                        <td className={tdClass}>
                          <Badge tone={row.is_value ? "good" : "muted"}>
                            {row.is_value ? "value" : "no edge"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="Correct-score top five" subtitle="Most likely scorelines under the fitted Poisson grid.">
              {data.model.correct_score_top5.length === 0 ? (
                <Empty>No score grid was produced for this fixture.</Empty>
              ) : (
                <ul className="space-y-2">
                  {data.model.correct_score_top5.map((row) => (
                    <li key={row.score} className="flex items-center justify-between text-sm">
                      <span className="font-mono text-mist">{row.score}</span>
                      <span className="w-2/3">
                        <ProbabilityBar value={row.probability} threshold={1.01} />
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card title="Risks and data gaps" subtitle="Read these before trusting any edge above.">
              {data.risks.length === 0 ? (
                <Empty>No data risks were flagged.</Empty>
              ) : (
                <ul className="space-y-3">
                  {data.risks.map((risk) => (
                    <li key={risk.code} className="flex gap-3">
                      <Badge tone={SEVERITY_TONES[risk.severity] ?? "muted"}>{risk.severity}</Badge>
                      <p className="text-xs leading-relaxed text-mist/65">{risk.detail}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="Player form" subtitle="Only reported when a verified player feed exists.">
              <Note tone="warn">
                {data.player_form.available
                  ? "Player-level probabilities are available."
                  : data.player_form.reason}
              </Note>
            </Card>

            <Card title="Provenance" subtitle="Where each part of this dossier came from.">
              <ul className="space-y-2 text-xs text-mist/60">
                {Object.entries(data.sources).map(([key, value]) => (
                  <li key={key} className="flex justify-between gap-3">
                    <span className="text-mist/40">{titleCase(key)}</span>
                    <span className="text-right text-mist/80">{value}</span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
