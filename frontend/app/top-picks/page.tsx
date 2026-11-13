"use client";

import Link from "next/link";

import {
  Badge,
  Card,
  ErrorNote,
  Loading,
  NoQualified,
  Note,
  PageHeader,
  ProbabilityBar,
  Stat,
} from "@/components/ui";
import { ageLabel, fmtOdds, fmtPercent, titleCase } from "@/lib/format";
import type { GatedPick, GatedPicksReport } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function supportingLine(pick: GatedPick): string | null {
  if (!pick.also_qualifying.length) return null;
  return pick.also_qualifying
    .map((row) => `${row.label} ${fmtPercent(row.probability, 0)}`)
    .join(" · ");
}

function PickCard({ pick, gate }: { pick: GatedPick; gate: number }) {
  const evTone =
    pick.expected_value_percent !== null && pick.expected_value_percent > 0
      ? "text-lime"
      : "text-mist/55";
  const blend = pick.model_vs_market;
  const grade = pick.data_quality.grade;

  return (
    <Card
      title={`${pick.rank}. ${pick.match}`}
      subtitle={`${pick.competition} · ${pick.country} · ${pick.kickoff_eat}`}
      actions={
        <>
          <Badge tone="good">clears {fmtPercent(gate, 0)}</Badge>
          {grade && <Badge tone="muted">data {titleCase(grade)}</Badge>}
        </>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-base font-semibold text-white">{pick.selection}</p>
          <p className="text-lg font-semibold tabular-nums text-mist">
            {fmtOdds(pick.odds)}
          </p>
        </div>

        <ProbabilityBar value={pick.probability} threshold={gate} />

        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <div>
            <p className="text-mist/40">EV at price</p>
            <p className={`tabular-nums ${evTone}`}>
              {pick.expected_value_percent === null
                ? "unavailable"
                : `${pick.expected_value_percent.toFixed(1)}%`}
            </p>
          </div>
          <div>
            <p className="text-mist/40">Edge over price</p>
            <p className="tabular-nums text-mist/75">{fmtPercent(pick.edge)}</p>
          </div>
          <div>
            <p className="text-mist/40">Data confidence</p>
            <p className="tabular-nums text-mist/75">{fmtPercent(pick.confidence, 0)}</p>
          </div>
          <div>
            <p className="text-mist/40">Expected goals</p>
            <p className="tabular-nums text-mist/75">
              {pick.expected_goals ?? "—"}
            </p>
          </div>
        </div>

        <div className="rounded-xl border border-edge bg-pitch/40 px-3 py-2 text-xs">
          <p className="text-mist/60">
            {pick.bookmaker ? `${pick.bookmaker} price` : "Price source unnamed"}
            {pick.source ? ` · ${pick.source}` : ""}
          </p>
          <p className="mt-0.5 text-mist/40">
            captured {ageLabel(pick.captured_at)}
            {pick.price_age_hours !== null
              ? ` · ${pick.price_age_hours.toFixed(1)} h old at analysis`
              : ""}
          </p>
        </div>

        {blend && (
          <p className="text-[11px] leading-relaxed text-mist/45">
            Blended with the de-vigged market: model{" "}
            {fmtPercent(blend.model ?? null)} · market {fmtPercent(blend.market ?? null)} · model
            weight {fmtPercent(blend.weight ?? null, 0)}
            {blend.verdict ? ` · ${blend.verdict}` : ""}
          </p>
        )}

        {supportingLine(pick) && (
          <div>
            <p className="text-[11px] uppercase tracking-wider text-mist/40">
              Also above the gate on this match
            </p>
            <p className="mt-1 text-xs text-mist/65">{supportingLine(pick)}</p>
          </div>
        )}

        {pick.confidence_reasons.length > 0 && (
          <p className="text-[11px] leading-relaxed text-mist/40">
            {pick.confidence_reasons.join(" · ")}
          </p>
        )}

        <Link
          href={`/analysis?fixture=${pick.fixture_id}`}
          className="inline-flex items-center gap-1 text-xs font-medium text-lime/80 hover:text-lime"
        >
          Open the full dossier →
        </Link>
      </div>
    </Card>
  );
}

export default function TopPicksPage() {
  const report = useApi<GatedPicksReport>("/markets/top-picks");
  const data = report.data;
  const gate = data?.threshold ?? 0.85;
  const picks = data?.picks ?? [];

  const rejected = Object.entries(data?.rejected ?? {}).filter(([, count]) => count > 0);

  return (
    <div>
      <PageHeader
        eyebrow="Today's best matches"
        title="Five best 85%+ match picks"
        description="The strongest outright result per match, ranked by the model's win probability, each published only with a real attributed bookmaker price and enough data confidence. Five is a cap, not a quota."
        actions={
          <button
            type="button"
            onClick={report.reload}
            className="border border-edge bg-panel text-mist/80 hover:border-lime/40 hover:text-lime"
          >
            Refresh
          </button>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Picks today"
          value={data ? `${data.count} / ${data.params.limit}` : "—"}
          hint={data ? `Matchday ${data.target_date} (EAT)` : "Loading"}
          tone={data && data.count > 0 ? "good" : "default"}
        />
        <Stat
          label="Probability gate"
          value={fmtPercent(gate, 0)}
          hint="Never lowered to fill the list"
        />
        <Stat
          label="Minimum data confidence"
          value={fmtPercent(data?.minimum_confidence ?? 0.6, 0)}
          hint="Applied to every pick"
        />
        <Stat
          label="Fixtures scanned"
          value={data?.fixtures_scanned ?? "—"}
          hint="Scheduled and priced today"
        />
      </div>

      <div className="mt-4">
        {report.loading && <Loading label="Ranking today's matches" />}
        {report.error && <ErrorNote message={report.error} />}
      </div>

      {data && (
        <>
          <div className="mt-4">
            <Note>{data.note}</Note>
          </div>

          {picks.length === 0 ? (
            <div className="mt-4">
              <NoQualified message={data.message} />
            </div>
          ) : (
            <div className="mt-4 grid gap-4 xl:grid-cols-2">
              {picks.map((pick) => (
                <PickCard key={pick.fixture_id} pick={pick} gate={gate} />
              ))}
            </div>
          )}

          {picks.length > 0 && picks.length < data.params.limit && (
            <p className="mt-3 text-[11px] text-mist/45">
              Only {picks.length} match{picks.length === 1 ? "" : "es"} cleared the gate with
              attributed prices, so the list is shorter than its cap.
            </p>
          )}

          {rejected.length > 0 && (
            <p className="mt-3 text-[11px] text-mist/40">
              Filtered out:{" "}
              {rejected
                .map(([reason, count]) => `${count} ${reason.replace(/_/g, " ")}`)
                .join(" · ")}
              .
            </p>
          )}
        </>
      )}
    </div>
  );
}
