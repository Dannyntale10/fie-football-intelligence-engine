"use client";

import Link from "next/link";
import { useEffect } from "react";

import { Badge, Card, ErrorNote, Loading, Note, PageHeader } from "@/components/ui";
import { fmtPercent } from "@/lib/format";
import type { LiveFixture, LiveFixturesResponse, LivePrediction } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const TONE: Record<string, "good" | "warn" | "muted"> = {
  fresh: "good",
  stale: "warn",
  expired: "muted",
};

function Probabilities({ prediction }: { prediction: LivePrediction }) {
  const probabilities = prediction.probabilities;
  if (!probabilities) {
    return (
      <p className="text-xs text-mist/50">
        No forecast ({prediction.status.replaceAll("_", " ")})
      </p>
    );
  }
  const rows = [
    ["Home", probabilities.home_win],
    ["Draw", probabilities.draw],
    ["Away", probabilities.away_win],
  ] as const;
  return (
    <div className="space-y-1">
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-center gap-2 text-xs">
          <span className="w-12 text-mist/50">{label}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-pitch">
            <div className="h-full bg-lime" style={{ width: `${value * 100}%` }} />
          </div>
          <span className="w-12 text-right tabular-nums text-mist/80">
            {fmtPercent(value, 1)}
          </span>
        </div>
      ))}
    </div>
  );
}

function LiveCard({ fixture }: { fixture: LiveFixture }) {
  const prediction = fixture.live_prediction;
  return (
    <Card
      title={`${fixture.home_team} vs ${fixture.away_team}`}
      subtitle={`${fixture.competition} · ${fixture.kickoff_eat ?? ""}`}
      actions={
        prediction ? (
          <Badge tone={TONE[prediction.freshness] ?? "muted"}>
            {prediction.freshness}
          </Badge>
        ) : null
      }
    >
      <div className="space-y-3">
        <div className="flex items-baseline justify-between">
          <p className="text-2xl font-bold tabular-nums text-white">
            {fixture.home_score ?? "–"} : {fixture.away_score ?? "–"}
          </p>
          <p className="text-sm tabular-nums text-mist/60">
            {fixture.live_clock ??
              (prediction?.minute != null ? `${prediction.minute}'` : "—")}
          </p>
        </div>
        {prediction ? <Probabilities prediction={prediction} /> : null}
        <div className="flex items-center justify-between text-[11px] text-mist/45">
          <span>
            {prediction
              ? `Model ${prediction.model_version} · as of ${prediction.observed_at ?? "unknown"}`
              : "Waiting for the engine's first snapshot"}
          </span>
          <Link href={`/fixtures/${fixture.id}`} className="text-lime hover:underline">
            Match detail
          </Link>
        </div>
      </div>
    </Card>
  );
}

export default function LivePage() {
  const { data, loading, error, reload } =
    useApi<LiveFixturesResponse>("/fixtures/live");

  useEffect(() => {
    const timer = setInterval(reload, 30_000);
    return () => clearInterval(timer);
  }, [reload]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Live"
        description="Conditional win probabilities for matches in play."
      />
      <Note tone="warn">
        Model estimate, not a price. Informational only. Live numbers never enter the
        85% gate, a slip, or a staking recommendation.
      </Note>
      {error && <ErrorNote message={error} />}
      {loading && !data && <Loading label="Reading the live board" />}
      {data && data.warning && <Note tone="warn">{data.warning}</Note>}
      {data && data.fixtures.length === 0 && (
        <Card title="No matches in play">
          <p className="text-sm text-mist/60">
            Nothing is being played right now. The board refreshes every 30 seconds.
          </p>
        </Card>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        {data?.fixtures.map((fixture) => (
          <LiveCard key={fixture.id} fixture={fixture} />
        ))}
      </div>
    </div>
  );
}
