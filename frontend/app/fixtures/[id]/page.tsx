"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { Badge, Card, ErrorNote, Loading, Note, PageHeader } from "@/components/ui";
import { fmtPercent } from "@/lib/format";
import type { Fixture, LiveHistoryResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const RESULT_LABEL: Record<string, string> = {
  home_win: "Home win",
  draw: "Draw",
  away_win: "Away win",
};

export default function FixtureDetailPage() {
  const params = useParams<{ id: string }>();
  const fixture = useApi<Fixture>(`/fixtures/${params.id}`);
  const live = useApi<LiveHistoryResponse>(`/fixtures/${params.id}/live`);
  const latest = live.data?.latest;
  const latestProbabilities = latest?.probabilities;

  return (
    <div className="space-y-4">
      <PageHeader
        title={
          fixture.data
            ? `${fixture.data.home_team} vs ${fixture.data.away_team}`
            : "Fixture"
        }
        description={
          fixture.data
            ? `${fixture.data.competition} · ${fixture.data.kickoff_eat ?? ""}`
            : undefined
        }
      />
      {fixture.error && <ErrorNote message={fixture.error} />}
      {live.error && <ErrorNote message={live.error} />}
      {fixture.loading && !fixture.data && <Loading label="Loading the fixture" />}

      {fixture.data && (
        <Card title="Result and state">
          <div className="flex flex-wrap items-center gap-4">
            <p className="text-3xl font-bold tabular-nums text-white">
              {fixture.data.home_score ?? "–"} : {fixture.data.away_score ?? "–"}
            </p>
            <Badge tone="muted">{fixture.data.status ?? "unknown"}</Badge>
            <span className="text-sm text-mist/60">{fixture.data.live_clock ?? "—"}</span>
            <Link
              href={`/analysis?fixture=${fixture.data.id}`}
              className="text-sm text-lime hover:underline"
            >
              Open full analysis
            </Link>
          </div>
        </Card>
      )}

      {latest && (
        <Card
          title="Live forecast"
          actions={<Badge tone="muted">{latest.freshness}</Badge>}
        >
          {latestProbabilities ? (
            <div className="grid grid-cols-3 gap-3 text-center">
              {(["home_win", "draw", "away_win"] as const).map((key) => (
                <div key={key}>
                  <p className="text-xs text-mist/45">
                    {key === "home_win" ? "Home" : key === "draw" ? "Draw" : "Away"}
                  </p>
                  <p className="text-xl font-semibold tabular-nums text-white">
                    {fmtPercent(latestProbabilities[key], 1)}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-mist/55">
              No forecast: {latest.status.replaceAll("_", " ")}.
            </p>
          )}
          <Note tone="warn">
            Model estimate, not a price. Informational only.
          </Note>
        </Card>
      )}

      <Card title="Settled snapshots">
        {live.data && live.data.history.length === 0 && (
          <p className="text-sm text-mist/55">
            No settled snapshots in the last 24 hours.
          </p>
        )}
        {live.data && live.data.history.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-mist/40">
                <tr>
                  <th className="py-2">Minute</th>
                  <th className="py-2">Score at snapshot</th>
                  <th className="py-2">Home / Draw / Away</th>
                  <th className="py-2">Final</th>
                  <th className="py-2">Brier</th>
                </tr>
              </thead>
              <tbody>
                {live.data.history.map((row) => (
                  <tr key={row.minute_bucket} className="border-t border-edge">
                    <td className="py-2 tabular-nums">{row.minute ?? "—"}</td>
                    <td className="py-2 tabular-nums">
                      {row.score.home ?? "–"} : {row.score.away ?? "–"}
                    </td>
                    <td className="py-2 tabular-nums">
                      {row.probabilities
                        ? `${fmtPercent(row.probabilities.home_win, 0)} / ${fmtPercent(row.probabilities.draw, 0)} / ${fmtPercent(row.probabilities.away_win, 0)}`
                        : "—"}
                    </td>
                    <td className="py-2">
                      {row.result ? RESULT_LABEL[row.result] ?? row.result : "Open"}
                    </td>
                    <td className="py-2 tabular-nums">
                      {row.brier?.toFixed(3) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
