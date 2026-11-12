"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Loading,
  Note,
  PageHeader,
  Stat,
  rowClass,
  tableClass,
  tdClass,
  thClass,
  theadClass,
} from "@/components/ui";
import { eatDateTime, fmtPercent, fmtSignedPercent, titleCase } from "@/lib/format";
import type { CalibrationReport, PerformanceSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type PredictionAccuracy = {
  period_days: number;
  total_matches: number;
  correct_predictions: number;
  accuracy_percent: number;
  details: Array<{
    match: string;
    predicted: string;
    actual: string;
    confidence: number;
    correct: boolean;
  }>;
};

type ClvSummary = {
  n_selections: number;
  mean_clv_percent: number | null;
  beat_close_percent: number | null;
  by_market: Record<string, { n: number; mean_clv_percent: number }>;
  recent: Array<{
    fixture_id: number;
    market_key: string;
    selection_price: number;
    closing_price: number;
    clv_percent: number;
    beat_close: boolean;
  }>;
  note?: string;
};

const tooltipStyle = {
  backgroundColor: "#0d1f16",
  border: "1px solid #1d3a2a",
  borderRadius: 8,
  color: "#d1fae5",
  fontSize: 12,
};

export default function PerformancePage() {
  const summary = useApi<PerformanceSummary>("/performance/summary?days=90");
  const accuracy = useApi<PredictionAccuracy>("/performance/predictions?days=30");
  const clv = useApi<ClvSummary>("/evaluation/clv?days=90");
  const calibration = useApi<CalibrationReport>("/evaluation/calibration");

  const reliability = (calibration.data?.reliability ?? [])
    .filter((row) => row.predictions > 0)
    .map((row) => ({
      bin: row.bin,
      confidence: Number((row.mean_confidence * 100).toFixed(1)),
      observed: Number((row.observed_rate * 100).toFixed(1)),
      predictions: row.predictions,
    }));

  const calibrationMissing =
    calibration.error?.toLowerCase().includes("no calibration") ?? false;

  return (
    <div>
      <PageHeader
        eyebrow="Performance analytics"
        title="Measured performance"
        description="Paper-trading results, prediction accuracy, closing-line value and walk-forward calibration. Only real, non-synthetic slips count; a losing record is reported exactly as it is."
        actions={<Badge tone="warn">Paper trading</Badge>}
      />

      {summary.loading && <Loading label="Loading ledger" />}
      {summary.error && <ErrorNote message={summary.error} />}

      {summary.data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat
              label="Settled slips (90d)"
              value={summary.data.settled}
              hint={`${summary.data.pending} pending · ${summary.data.bets} total`}
            />
            <Stat
              label="Accuracy"
              value={fmtPercent(summary.data.accuracy)}
              hint={`${summary.data.wins}W · ${summary.data.losses}L · ${summary.data.voids}V`}
              tone={
                summary.data.accuracy !== null && summary.data.accuracy >= 0.5 ? "good" : "bad"
              }
            />
            <Stat
              label="ROI"
              value={fmtSignedPercent(summary.data.roi)}
              hint={`Yield ${fmtSignedPercent(summary.data.yield)}`}
              tone={
                summary.data.roi !== null && summary.data.roi >= 0 ? "good" : "bad"
              }
            />
            <Stat
              label="Profit / loss"
              value={summary.data.profit.toFixed(2)}
              hint={`Stake ${summary.data.stake.toFixed(2)}`}
              tone={summary.data.profit >= 0 ? "good" : "bad"}
            />
          </div>

          <div className="mt-4">
            <Note>{summary.data.note}</Note>
          </div>

          {Object.keys(summary.data.by_slip_type).length > 0 && (
            <div className="mt-4">
              <Card title="By slip type" subtitle="Synthetic test slips are excluded from every row.">
                <div className="overflow-x-auto">
                  <table className={tableClass}>
                    <thead className={theadClass}>
                      <tr>
                        <th className={thClass}>Slip type</th>
                        <th className={thClass}>Bets</th>
                        <th className={thClass}>W / L / V</th>
                        <th className={thClass}>Accuracy</th>
                        <th className={thClass}>Stake</th>
                        <th className={thClass}>Profit</th>
                        <th className={thClass}>ROI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(summary.data.by_slip_type).map(([type, bucket]) => (
                        <tr key={type} className={rowClass}>
                          <td className={tdClass}>{titleCase(type)}</td>
                          <td className={`${tdClass} tabular-nums`}>{bucket.bets}</td>
                          <td className={`${tdClass} tabular-nums`}>
                            {bucket.wins} / {bucket.losses} / {bucket.voids}
                          </td>
                          <td className={`${tdClass} tabular-nums`}>
                            {fmtPercent(bucket.accuracy)}
                          </td>
                          <td className={`${tdClass} tabular-nums`}>{bucket.stake.toFixed(2)}</td>
                          <td
                            className={`${tdClass} tabular-nums ${
                              bucket.profit >= 0 ? "text-lime" : "text-rose-200"
                            }`}
                          >
                            {bucket.profit.toFixed(2)}
                          </td>
                          <td className={`${tdClass} tabular-nums`}>{fmtSignedPercent(bucket.roi)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          )}
        </>
      )}

      <div className="mt-6 grid gap-4 xl:grid-cols-2">
        <Card
          title="1X2 prediction accuracy (30 days)"
          subtitle="Walk-forward on finished matches; completed fixtures only."
        >
          {accuracy.loading && <Loading label="Scoring predictions" />}
          {accuracy.error && <ErrorNote message={accuracy.error} />}
          {accuracy.data && accuracy.data.total_matches === 0 && (
            <Empty>No finished matches in the last 30 days to score.</Empty>
          )}
          {accuracy.data && accuracy.data.total_matches > 0 && (
            <>
              <div className="mb-4 flex flex-wrap items-end gap-4">
                <p className="text-4xl font-semibold tabular-nums text-lime">
                  {accuracy.data.accuracy_percent.toFixed(1)}%
                </p>
                <p className="pb-1 text-xs text-mist/50">
                  {accuracy.data.correct_predictions} of {accuracy.data.total_matches} matches
                </p>
              </div>
              <div className="max-h-72 overflow-auto">
                <table className={tableClass}>
                  <thead className={theadClass}>
                    <tr>
                      <th className={thClass}>Match</th>
                      <th className={thClass}>Predicted</th>
                      <th className={thClass}>Actual</th>
                      <th className={thClass}>Confidence</th>
                      <th className={thClass}>Hit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accuracy.data.details.map((row) => (
                      <tr key={row.match} className={rowClass}>
                        <td className={tdClass}>{row.match}</td>
                        <td className={tdClass}>{row.predicted}</td>
                        <td className={tdClass}>{row.actual}</td>
                        <td className={`${tdClass} tabular-nums`}>{row.confidence}%</td>
                        <td className={tdClass}>
                          <Badge tone={row.correct ? "good" : "bad"}>
                            {row.correct ? "Hit" : "Miss"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>

        <Card
          title="Closing-line value (90 days)"
          subtitle="Did the prices we took beat the closing price?"
        >
          {clv.loading && <Loading label="Loading CLV" />}
          {clv.error && <ErrorNote message={clv.error} />}
          {clv.data && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Stat
                  label="Selections matched"
                  value={clv.data.n_selections}
                  tone={clv.data.n_selections > 0 ? "default" : "muted"}
                />
                <Stat
                  label="Mean CLV"
                  value={
                    clv.data.mean_clv_percent === null
                      ? "—"
                      : `${clv.data.mean_clv_percent.toFixed(2)}%`
                  }
                  tone={
                    clv.data.mean_clv_percent !== null && clv.data.mean_clv_percent > 0
                      ? "good"
                      : "bad"
                  }
                  hint={
                    clv.data.beat_close_percent === null
                      ? undefined
                      : `${clv.data.beat_close_percent}% beat the close`
                  }
                />
              </div>
              {clv.data.note && (
                <div className="mt-3">
                  <Note>{clv.data.note}</Note>
                </div>
              )}
              {clv.data.recent.length > 0 && (
                <div className="mt-3 max-h-56 overflow-auto">
                  <table className={tableClass}>
                    <thead className={theadClass}>
                      <tr>
                        <th className={thClass}>Fixture</th>
                        <th className={thClass}>Market</th>
                        <th className={thClass}>Taken</th>
                        <th className={thClass}>Close</th>
                        <th className={thClass}>CLV</th>
                      </tr>
                    </thead>
                    <tbody>
                      {clv.data.recent.map((row) => (
                        <tr key={`${row.fixture_id}-${row.market_key}`} className={rowClass}>
                          <td className={tdClass}>#{row.fixture_id}</td>
                          <td className={tdClass}>{row.market_key}</td>
                          <td className={`${tdClass} tabular-nums`}>{row.selection_price}</td>
                          <td className={`${tdClass} tabular-nums`}>{row.closing_price}</td>
                          <td
                            className={`${tdClass} tabular-nums ${
                              row.clv_percent > 0 ? "text-lime" : "text-rose-200"
                            }`}
                          >
                            {row.clv_percent.toFixed(2)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      <div className="mt-6">
        <Card
          title="Walk-forward calibration"
          subtitle="Out-of-sample model versus de-vigged market. Saved report only — recompute with POST /evaluation/calibration."
        >
          {calibration.loading && <Loading label="Loading calibration" />}
          {calibrationMissing && (
            <Empty>
              No calibration report has been saved yet. Run the walk-forward evaluation on the
              backend before trusting any edge.
            </Empty>
          )}
          {calibration.error && !calibrationMissing && <ErrorNote message={calibration.error} />}
          {calibration.data && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={calibration.data.skill.validated ? "good" : "bad"}>
                  {calibration.data.verdict}
                </Badge>
                <Badge tone="muted">{calibration.data.n_matches} matches</Badge>
                <Badge tone="muted">{calibration.data.n_competitions} competitions</Badge>
                <Badge tone="muted">generated {eatDateTime(calibration.data.generated_at)}</Badge>
              </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Stat
                  label="Model Brier"
                  value={calibration.data.model.brier?.toFixed(4) ?? "—"}
                  hint={`Market ${calibration.data.market.brier?.toFixed(4) ?? "—"}`}
                  tone={calibration.data.skill.validated ? "good" : "bad"}
                />
                <Stat
                  label="Model log loss"
                  value={calibration.data.model.log_loss?.toFixed(4) ?? "—"}
                  hint={`Market ${calibration.data.market.log_loss?.toFixed(4) ?? "—"}`}
                />
                <Stat
                  label="Expected calibration error"
                  value={calibration.data.model.ece?.toFixed(4) ?? "—"}
                />
                <Stat
                  label="Accuracy"
                  value={`${calibration.data.model.accuracy_percent?.toFixed(1) ?? "—"}%`}
                  hint={`Market ${calibration.data.market.accuracy_percent?.toFixed(1) ?? "—"}%`}
                />
              </div>

              {reliability.length > 0 && (
                <div className="mt-5 h-72 w-full min-w-0">
                  <p className="mb-2 text-xs text-mist/50">
                    Reliability: stated confidence versus observed hit rate per bin.
                  </p>
                  <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                    <LineChart data={reliability} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                      <CartesianGrid stroke="#1d3a2a" strokeDasharray="3 3" />
                      <XAxis dataKey="bin" stroke="#6b8f7c" fontSize={11} />
                      <YAxis stroke="#6b8f7c" fontSize={11} domain={[0, 100]} unit="%" />
                      <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => `${value}%`} />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      <Line
                        type="monotone"
                        dataKey="confidence"
                        name="Mean confidence"
                        stroke="#a3e635"
                        strokeWidth={2}
                        dot={{ r: 2 }}
                      />
                      <Line
                        type="monotone"
                        dataKey="observed"
                        name="Observed rate"
                        stroke="#38bdf8"
                        strokeWidth={2}
                        dot={{ r: 2 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}

              {reliability.length > 0 && (
                <div className="mt-4 h-64 w-full min-w-0">
                  <p className="mb-2 text-xs text-mist/50">Predictions per confidence bin.</p>
                  <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                    <BarChart data={reliability} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                      <CartesianGrid stroke="#1d3a2a" strokeDasharray="3 3" />
                      <XAxis dataKey="bin" stroke="#6b8f7c" fontSize={11} />
                      <YAxis stroke="#6b8f7c" fontSize={11} allowDecimals={false} />
                      <Tooltip contentStyle={tooltipStyle} />
                      <Bar dataKey="predictions" name="Predictions" fill="#a3e635" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              {Object.keys(calibration.data.per_competition).length > 0 && (
                <div className="mt-4 overflow-x-auto">
                  <table className={tableClass}>
                    <thead className={theadClass}>
                      <tr>
                        <th className={thClass}>Competition</th>
                        <th className={thClass}>Matches</th>
                        <th className={thClass}>Model Brier</th>
                        <th className={thClass}>Market Brier</th>
                        <th className={thClass}>Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(calibration.data.per_competition).map(([name, row]) => (
                        <tr key={name} className={rowClass}>
                          <td className={tdClass}>{name}</td>
                          <td className={`${tdClass} tabular-nums`}>{row.n_matches}</td>
                          <td className={`${tdClass} tabular-nums`}>{row.model_brier?.toFixed(4)}</td>
                          <td className={`${tdClass} tabular-nums`}>{row.market_brier?.toFixed(4)}</td>
                          <td className={tdClass}>
                            <Badge tone={row.verdict === "model_beats_market" ? "good" : "bad"}>
                              {row.verdict}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="mt-4">
                <Note tone={calibration.data.skill.validated ? "good" : "warn"}>
                  {calibration.data.skill.validated
                    ? "The model beat the de-vigged market out of sample in this report. Keep monitoring: one report is evidence, not a guarantee."
                    : "The model did not beat the de-vigged market out of sample. Until it does, EV slips should stay gated off regardless of how confident the model looks."}
                </Note>
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
