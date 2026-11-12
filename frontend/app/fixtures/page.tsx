"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Loading,
  Note,
  PageHeader,
  rowClass,
  tableClass,
  tdClass,
  thClass,
  theadClass,
} from "@/components/ui";
import { eatDateTime, fmtOdds } from "@/lib/format";
import type { Fixture } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function FixturesPage() {
  const [search, setSearch] = useState("");
  const [competition, setCompetition] = useState("");
  const [status, setStatus] = useState("");
  const [upcomingOnly, setUpcomingOnly] = useState(true);

  const params = new URLSearchParams({ limit: "300" });
  if (upcomingOnly) params.set("upcoming_only", "true");
  if (status) params.set("status", status);
  if (competition) params.set("competition", competition);

  const { data, loading, error, reload } = useApi<Fixture[]>(`/fixtures?${params.toString()}`);

  const competitions = useMemo(() => {
    const names = new Set<string>();
    (data ?? []).forEach((fixture) => {
      if (fixture.competition) names.add(fixture.competition);
    });
    return Array.from(names).sort();
  }, [data]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return data ?? [];
    return (data ?? []).filter((fixture) =>
      [fixture.home_team, fixture.away_team, fixture.competition, fixture.country, fixture.source]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [data, search]);

  return (
    <div>
      <PageHeader
        eyebrow="Global fixtures"
        title="Fixture slate"
        description="Every fixture stored locally, with the source that supplied it. Kickoff times are shown in East Africa Time. A fixture is only analysable when its data quality allows it."
        actions={
          <button
            type="button"
            onClick={reload}
            className="border border-edge bg-panel text-mist/80 hover:border-lime/40 hover:text-lime"
          >
            Refresh
          </button>
        }
      />

      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-mist/60">
            Search
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Team, league, country…"
              className="mt-1 w-full"
            />
          </label>
          <label className="text-xs text-mist/60">
            Competition
            <select
              value={competition}
              onChange={(event) => setCompetition(event.target.value)}
              className="mt-1 w-full"
            >
              <option value="">All competitions</option>
              {competitions.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-mist/60">
            Status
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="mt-1 w-full"
            >
              <option value="">Any status</option>
              <option value="scheduled">Scheduled</option>
              <option value="finished">Finished</option>
              <option value="live">Live</option>
            </select>
          </label>
          <label className="flex items-end gap-2 text-xs text-mist/60">
            <input
              type="checkbox"
              checked={upcomingOnly}
              onChange={(event) => setUpcomingOnly(event.target.checked)}
              className="mb-2 h-4 w-4 rounded border-edge bg-pitch accent-lime"
            />
            <span className="pb-2">Upcoming only</span>
          </label>
        </div>
      </Card>

      {loading && <Loading label="Loading fixtures" />}
      {error && <ErrorNote message={error} />}

      {!loading && !error && filtered.length === 0 && (
        <Empty>
          No fixtures match these filters. If the database is empty, run the fixture scan on the
          backend or import a CSV.
        </Empty>
      )}

      {filtered.length > 0 && (
        <Card
          title={`${filtered.length} fixture${filtered.length === 1 ? "" : "s"}`}
          subtitle="1X2 prices come from the stored bookmaker feed; blanks mean no price was ingested."
        >
          <div className="overflow-x-auto">
            <table className={tableClass}>
              <thead className={theadClass}>
                <tr>
                  <th className={thClass}>Kickoff (EAT)</th>
                  <th className={thClass}>Match</th>
                  <th className={thClass}>Competition</th>
                  <th className={thClass}>1 / X / 2</th>
                  <th className={thClass}>Quality</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass}>Source</th>
                  <th className={thClass}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((fixture) => (
                  <tr key={fixture.id} className={rowClass}>
                    <td className={`${tdClass} whitespace-nowrap text-mist/60`}>
                      {eatDateTime(fixture.kickoff_utc)}
                    </td>
                    <td className={tdClass}>
                      <p className="font-medium text-mist">
                        {fixture.home_team} <span className="text-mist/40">vs</span>{" "}
                        {fixture.away_team}
                      </p>
                      <p className="text-[11px] text-mist/40">
                        {fixture.country} · #{fixture.id}
                      </p>
                    </td>
                    <td className={tdClass}>{fixture.competition}</td>
                    <td className={`${tdClass} tabular-nums`}>
                      {fmtOdds(fixture.home_odds)} / {fmtOdds(fixture.draw_odds)} /{" "}
                      {fmtOdds(fixture.away_odds)}
                    </td>
                    <td className={tdClass}>
                      {fixture.data_quality_grade ? (
                        <Badge
                          tone={
                            fixture.data_quality_grade === "A" || fixture.data_quality_grade === "B"
                              ? "good"
                              : fixture.data_quality_grade === "C"
                                ? "warn"
                                : "bad"
                          }
                        >
                          {fixture.data_quality_grade}
                          {fixture.data_quality_score !== null
                            ? ` · ${fixture.data_quality_score}`
                            : ""}
                        </Badge>
                      ) : (
                        <span className="text-mist/35">—</span>
                      )}
                    </td>
                    <td className={tdClass}>
                      <Badge tone={fixture.eligible_for_analysis ? "good" : "muted"}>
                        {fixture.status ?? "unknown"}
                      </Badge>
                    </td>
                    <td className={`${tdClass} text-mist/50`}>{fixture.source}</td>
                    <td className={tdClass}>
                      <div className="flex flex-wrap gap-2 text-xs">
                        <Link
                          href={`/analysis?fixture=${fixture.id}`}
                          className="text-lime hover:text-lime/80"
                        >
                          Analyse
                        </Link>
                        <Link
                          href={`/markets?fixture=${fixture.id}`}
                          className="text-mist/60 hover:text-mist"
                        >
                          Markets
                        </Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="mt-4">
        <Note>
          Fixture data is only as good as its source. A blank price is not a zero price, and a
          missing fixture is not an opportunity — it is a data gap to fix before analysis.
        </Note>
      </div>
    </div>
  );
}
