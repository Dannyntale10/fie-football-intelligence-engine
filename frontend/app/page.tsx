"use client";

import Link from "next/link";

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
} from "@/components/ui";
import { eatDateTime, fmtPercent, fmtSignedPercent, titleCase } from "@/lib/format";
import type {
  Fixture,
  Health,
  PerformanceSummary,
  Prediction,
  QualifiedPolicy,
  SourcesResponse,
} from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function OverviewPage() {
  const health = useApi<Health>("/health");
  const fixtures = useApi<Fixture[]>("/fixtures?upcoming_only=true&limit=8");
  const performance = useApi<PerformanceSummary>("/performance/summary");
  const predictions = useApi<{ count: number; predictions: Prediction[] }>(
    "/predictions?qualified_only=true&limit=6",
  );
  const sources = useApi<SourcesResponse>("/sources");
  const policy = useApi<QualifiedPolicy>("/markets/qualification-policy");

  const qualified = predictions.data?.predictions ?? [];
  const gate = policy.data?.minimum_probability ?? 0.85;

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

      <div className="mt-6 grid gap-4 xl:grid-cols-3">
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
