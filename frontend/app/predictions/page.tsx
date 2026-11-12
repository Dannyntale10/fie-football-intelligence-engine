"use client";

import { useMemo, useState } from "react";

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
import { eatDateTime, fmtOdds, fmtPercent, titleCase } from "@/lib/format";
import type { Prediction, QualifiedPolicy } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type PredictionResponse = { count: number; predictions: Prediction[] };

type SelectionRow = {
  id: number;
  prediction_id: number;
  fixture_id: number | null;
  family: string;
  selection: string;
  line: number | null;
  probability: number;
  confidence: number | null;
  price: number | null;
  bookmaker: string | null;
  ev: number | null;
  qualified: boolean;
};

export default function PredictionsPage() {
  const [qualifiedOnly, setQualifiedOnly] = useState(false);
  const [search, setSearch] = useState("");

  const path = `/predictions?limit=300${qualifiedOnly ? "&qualified_only=true" : ""}`;
  const predictions = useApi<PredictionResponse>(path);
  const selections = useApi<SelectionRow[]>("/predictions/selections?limit=200");
  const policy = useApi<QualifiedPolicy>("/markets/qualification-policy");

  const gate = policy.data?.minimum_probability ?? 0.85;

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const list = predictions.data?.predictions ?? [];
    if (!needle) return list;
    return list.filter((row) =>
      [row.family, row.selection, String(row.fixture_id ?? "")]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [predictions.data, search]);

  const qualifiedCount = rows.filter((row) => row.qualified).length;

  return (
    <div>
      <PageHeader
        eyebrow="Prediction history"
        title="Predictions"
        description="Every recorded prediction is append-only: new versions are added, losing versions are never overwritten or deleted. Probability and confidence are separate measurements."
        actions={
          <Badge tone={qualifiedOnly ? "good" : "muted"}>
            {qualifiedOnly ? "Qualified only" : "All versions"}
          </Badge>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Records" value={predictions.data?.count ?? "—"} hint="Newest first, capped at 300" />
        <Stat
          label="Qualified in view"
          value={qualifiedCount}
          hint={`Probability ≥ ${fmtPercent(gate, 0)}`}
          tone={qualifiedCount > 0 ? "good" : "default"}
        />
        <Stat label="Minimum confidence" value={fmtPercent(policy.data?.minimum_confidence ?? 0.6, 0)} />
        <Stat label="Overwrite policy" value="Append-only" hint="Losing versions stay visible" />
      </div>

      <Card className="mt-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-[220px] flex-1 text-xs text-mist/60">
            Search family, selection or fixture
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="mt-1 w-full"
              placeholder="e.g. double_chance or 1.5"
            />
          </label>
          <label className="flex items-center gap-2 pb-2 text-xs text-mist/60">
            <input
              type="checkbox"
              checked={qualifiedOnly}
              onChange={(event) => setQualifiedOnly(event.target.checked)}
              className="h-4 w-4 rounded border-edge bg-pitch accent-lime"
            />
            Qualified selections only
          </label>
          <button
            type="button"
            onClick={predictions.reload}
            className="mb-0.5 border border-edge bg-panel text-mist/80 hover:border-lime/40 hover:text-lime"
          >
            Refresh
          </button>
        </div>
      </Card>

      <div className="mt-4">
        {predictions.loading && <Loading label="Loading predictions" />}
        {predictions.error && <ErrorNote message={predictions.error} />}
        {!predictions.loading && !predictions.error && rows.length === 0 && (
          <NoQualified message={policy.data?.message_when_empty ?? "NO QUALIFIED SELECTIONS FOUND."} />
        )}
        {rows.length > 0 && (
          <Card title={`${rows.length} prediction record${rows.length === 1 ? "" : "s"}`}>
            <div className="overflow-x-auto">
              <table className={tableClass}>
                <thead className={theadClass}>
                  <tr>
                    <th className={thClass}>Recorded (EAT)</th>
                    <th className={thClass}>Fixture</th>
                    <th className={thClass}>Family</th>
                    <th className={thClass}>Selection</th>
                    <th className={thClass}>Probability</th>
                    <th className={thClass}>Confidence</th>
                    <th className={thClass}>Fair odds</th>
                    <th className={thClass}>EV</th>
                    <th className={thClass}>Gate</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} className={rowClass}>
                      <td className={`${tdClass} whitespace-nowrap text-mist/55`}>
                        {eatDateTime(row.created_at)}
                      </td>
                      <td className={tdClass}>{row.fixture_id ?? "—"}</td>
                      <td className={tdClass}>
                        <p className="text-mist/90">{titleCase(row.family)}</p>
                        <p className="text-[11px] text-mist/40">#{row.id}</p>
                      </td>
                      <td className={tdClass}>
                        {row.selection}
                        {row.line !== null ? ` ${row.line}` : ""}
                      </td>
                      <td className={tdClass}>
                        <ProbabilityBar value={row.probability} threshold={gate} />
                      </td>
                      <td className={`${tdClass} tabular-nums text-mist/65`}>
                        {fmtPercent(row.confidence)}
                      </td>
                      <td className={`${tdClass} tabular-nums`}>{fmtOdds(row.fair_odds)}</td>
                      <td
                        className={`${tdClass} tabular-nums ${
                          row.ev !== null && row.ev > 0 ? "text-lime" : "text-mist/55"
                        }`}
                      >
                        {row.ev === null ? "unavailable" : `${(row.ev * 100).toFixed(1)}%`}
                      </td>
                      <td className={tdClass}>
                        <Badge tone={row.qualified ? "good" : "muted"}>
                          {row.qualified ? "Qualified" : "Below gate"}
                        </Badge>
                        {row.qualification_reason && (
                          <p className="mt-1 text-[11px] text-mist/40">{row.qualification_reason}</p>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      <div className="mt-6">
        <Card
          title="Recorded selections"
          subtitle="Selections attached to predictions, including the price and bookmaker used when available."
        >
          {selections.loading && <Loading label="Loading selections" />}
          {selections.error && <ErrorNote message={selections.error} />}
          {selections.data && selections.data.length === 0 && (
            <Empty>No selections recorded yet.</Empty>
          )}
          {selections.data && selections.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className={tableClass}>
                <thead className={theadClass}>
                  <tr>
                    <th className={thClass}>Fixture</th>
                    <th className={thClass}>Family</th>
                    <th className={thClass}>Selection</th>
                    <th className={thClass}>Probability</th>
                    <th className={thClass}>Price</th>
                    <th className={thClass}>Bookmaker</th>
                    <th className={thClass}>EV</th>
                    <th className={thClass}>Qualified</th>
                  </tr>
                </thead>
                <tbody>
                  {selections.data.map((row) => (
                    <tr key={row.id} className={rowClass}>
                      <td className={tdClass}>{row.fixture_id ?? "—"}</td>
                      <td className={tdClass}>{titleCase(row.family)}</td>
                      <td className={tdClass}>
                        {row.selection}
                        {row.line !== null ? ` ${row.line}` : ""}
                      </td>
                      <td className={`${tdClass} tabular-nums`}>{fmtPercent(row.probability)}</td>
                      <td className={`${tdClass} tabular-nums`}>{fmtOdds(row.price)}</td>
                      <td className={`${tdClass} text-mist/55`}>{row.bookmaker ?? "—"}</td>
                      <td className={`${tdClass} tabular-nums`}>
                        {row.ev === null ? "—" : `${(row.ev * 100).toFixed(1)}%`}
                      </td>
                      <td className={tdClass}>
                        <Badge tone={row.qualified ? "good" : "muted"}>
                          {row.qualified ? "Yes" : "No"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <div className="mt-4">
        <Note>
          A qualified label never implies a guaranteed result. It means the selection cleared the
          probability and confidence gates at the time of recording, with the data then available.
        </Note>
      </div>
    </div>
  );
}
