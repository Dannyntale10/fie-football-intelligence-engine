"use client";

import { useEffect, useMemo, useState } from "react";

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
import { ageLabel, eatDateTime, fmtOdds, fmtPercent, titleCase } from "@/lib/format";
import type {
  DiscoveryResponse,
  Fixture,
  MarketAnalysisResponse,
  MarketCatalogue,
  ValueTipsResponse,
} from "@/lib/types";
import { useApi } from "@/lib/useApi";

const STATUS_TONES: Record<string, "good" | "warn" | "bad" | "muted" | "default"> = {
  ok: "good",
  key_not_modelled: "warn",
  insufficient_model_data: "warn",
  no_model: "muted",
  unmodelled: "muted",
};

export default function MarketsPage() {
  const [fixtureId, setFixtureId] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("fixture");
    if (requested) setFixtureId(requested);
  }, []);

  const catalogue = useApi<MarketCatalogue>("/markets/catalogue");
  const fixtures = useApi<Fixture[]>("/fixtures?limit=300&descending=true");
  const discovery = useApi<DiscoveryResponse>(
    fixtureId ? `/markets/fixtures/${fixtureId}/discovery` : null,
  );
  const analysis = useApi<MarketAnalysisResponse>(
    fixtureId ? `/markets/fixtures/${fixtureId}/analysis` : null,
  );
  const valueTips = useApi<ValueTipsResponse>("/markets/value-tips?days_ahead=3");

  const families = useMemo(() => {
    const rows = catalogue.data?.families ?? [];
    const needle = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (category && row.category !== category) return false;
      if (!needle) return true;
      return [row.family, row.label, row.category, row.model, row.settlement_group]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [catalogue.data, category, search]);

  const categories = useMemo(
    () => Object.keys(catalogue.data?.by_category ?? {}).sort(),
    [catalogue.data],
  );

  const offered = analysis.data?.selections ?? [];

  return (
    <div>
      <PageHeader
        eyebrow="Universal market engine"
        title="All available markets"
        description="The catalogue lists every market family this engine understands. It is not a claim of coverage: only markets a source actually priced are discovered and analysed. Every price keeps its bookmaker, source and capture time."
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Families in catalogue"
          value={catalogue.data?.families.length ?? "—"}
          hint={catalogue.data ? `Engine ${catalogue.data.engine}` : undefined}
        />
        <Stat
          label="Categories"
          value={categories.length || "—"}
          hint="Broadcast groupings, not a limit on markets"
        />
        <Stat
          label="Qualification gate"
          value={fmtPercent(catalogue.data?.qualification_threshold ?? 0.85, 0)}
          hint="Applied to every discovered selection"
        />
        <Stat
          label="Fixture selected"
          value={fixtureId || "None"}
          hint="Pick a fixture to inspect what was actually offered"
        />
      </div>

      {catalogue.loading && <div className="mt-4"><Loading label="Loading catalogue" /></div>}
      {catalogue.error && <div className="mt-4"><ErrorNote message={catalogue.error} /></div>}
      {catalogue.data && (
        <div className="mt-4">
          <Note>{catalogue.data.note}</Note>
        </div>
      )}

      <Card
        className="mt-6"
        title="Value tips beyond 1X2"
        subtitle="Totals, draw-no-bet and Asian handicap, compared against the de-vigged price from the same bookmaker. A gap outside the trusted band is stale ratings, not value, so it is dropped."
        actions={
          valueTips.data ? (
            <Badge tone={valueTips.data.tips.length > 0 ? "good" : "muted"}>
              {valueTips.data.tips.length} tips · {valueTips.data.fixtures_scanned} fixtures scanned
            </Badge>
          ) : undefined
        }
      >
        {valueTips.loading && <Loading label="Scanning priced markets" />}
        {valueTips.error && <ErrorNote message={valueTips.error} />}

        {valueTips.data && valueTips.data.tips.length === 0 && (
          <NoQualified message={valueTips.data.message ?? "NO QUALIFIED SELECTIONS FOUND."} />
        )}

        {valueTips.data && valueTips.data.tips.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className={tableClass}>
                <thead className={theadClass}>
                  <tr>
                    <th className={thClass}>Match</th>
                    <th className={thClass}>Market</th>
                    <th className={thClass}>Price</th>
                    <th className={thClass}>Bookmaker</th>
                    <th className={thClass}>Captured</th>
                    <th className={thClass}>Market</th>
                    <th className={thClass}>Model</th>
                    <th className={thClass}>Edge</th>
                    <th className={thClass}>EV</th>
                  </tr>
                </thead>
                <tbody>
                  {valueTips.data.tips.map((tip, index) => (
                    <tr
                      key={`${tip.fixture_id}-${tip.family}-${tip.selection}-${index}`}
                      className={rowClass}
                    >
                      <td className={tdClass}>
                        <p className="text-mist/90">{tip.match}</p>
                        <p className="text-[11px] text-mist/40">
                          {tip.competition} · {eatDateTime(tip.kickoff_utc)}
                        </p>
                      </td>
                      <td className={tdClass}>
                        <p className="text-mist/90">{tip.label ?? tip.family}</p>
                        <p className="text-[11px] text-mist/40">
                          {tip.selection}
                          {tip.line !== null ? ` ${tip.line}` : ""} · {tip.family}
                        </p>
                      </td>
                      <td className={`${tdClass} tabular-nums`}>{fmtOdds(tip.price)}</td>
                      <td className={`${tdClass} text-mist/60`}>
                        {tip.bookmaker ?? "unknown"}
                      </td>
                      <td className={`${tdClass} text-mist/45`}>{ageLabel(tip.captured_at)}</td>
                      <td className={`${tdClass} tabular-nums text-mist/55`}>
                        {fmtPercent(tip.market_probability)}
                      </td>
                      <td className={`${tdClass} tabular-nums`}>
                        {fmtPercent(tip.probability)}
                      </td>
                      <td className={`${tdClass} tabular-nums text-lime`}>
                        +{tip.edge_percent.toFixed(1)} pts
                      </td>
                      <td
                        className={`${tdClass} tabular-nums ${
                          tip.ev_percent > 0 ? "text-lime" : "text-mist/55"
                        }`}
                      >
                        {tip.ev_percent > 0 ? "+" : ""}
                        {tip.ev_percent.toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3">
              <Note>
                Edge is model probability minus de-vigged market probability, kept only inside{" "}
                {valueTips.data.thresholds.min_edge_percent}–
                {valueTips.data.thresholds.max_edge_percent} points, at most{" "}
                {valueTips.data.thresholds.per_fixture} per fixture. Dropped{" "}
                {valueTips.data.dropped.below_min_edge ?? 0} below the floor,{" "}
                {valueTips.data.dropped.above_max_edge ?? 0} above the ceiling,{" "}
                {valueTips.data.dropped.no_market_probability ?? 0} without a de-vig and{" "}
                {valueTips.data.dropped.non_positive_ev ?? 0} with no positive EV.
              </Note>
            </div>
          </>
        )}
      </Card>

      <div className="mt-6 grid gap-4 xl:grid-cols-5">
        <Card
          className="xl:col-span-3"
          title="Market catalogue"
          subtitle="Families, settlement groups and the model that produces probabilities."
        >
          <div className="mb-3 grid gap-2 sm:grid-cols-2">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search families, models, settlement groups…"
            />
            <select value={category} onChange={(event) => setCategory(event.target.value)}>
              <option value="">All categories</option>
              {categories.map((name) => (
                <option key={name} value={name}>
                  {titleCase(name)}
                </option>
              ))}
            </select>
          </div>

          {families.length === 0 ? (
            <Empty>No family matches that search.</Empty>
          ) : (
            <div className="max-h-[560px] overflow-auto">
              <table className={tableClass}>
                <thead className={theadClass}>
                  <tr>
                    <th className={thClass}>Family</th>
                    <th className={thClass}>Category</th>
                    <th className={thClass}>Model</th>
                    <th className={thClass}>Settlement group</th>
                  </tr>
                </thead>
                <tbody>
                  {families.map((row) => (
                    <tr key={row.family} className={rowClass}>
                      <td className={tdClass}>
                        <p className="text-mist">{row.label}</p>
                        <p className="text-[11px] text-mist/40">{row.family}</p>
                      </td>
                      <td className={tdClass}>{titleCase(row.category)}</td>
                      <td className={tdClass}>
                        {row.model ? (
                          <Badge tone="default">{row.model}</Badge>
                        ) : (
                          <Badge tone="muted">catalogue only</Badge>
                        )}
                      </td>
                      <td className={`${tdClass} text-mist/55`}>{row.settlement_group}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          className="xl:col-span-2"
          title="What this fixture actually carries"
          subtitle="Discovery reads only prices already stored for the fixture."
        >
          <label className="block text-xs text-mist/60">
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

          {!fixtureId && (
            <div className="mt-4">
              <Empty>Choose a fixture to discover its priced markets and run analysis.</Empty>
            </div>
          )}

          {fixtureId && discovery.loading && <div className="mt-4"><Loading label="Discovering markets" /></div>}
          {fixtureId && discovery.error && <div className="mt-4"><ErrorNote message={discovery.error} /></div>}

          {discovery.data && (
            <div className="mt-4 space-y-3">
              <div className="flex flex-wrap gap-2">
                <Badge tone="good">{discovery.data.discovered_count} priced selections</Badge>
                <Badge>{discovery.data.family_count} families</Badge>
                <Badge tone={discovery.data.unmodelled_count > 0 ? "warn" : "muted"}>
                  {discovery.data.unmodelled_count} unmodelled
                </Badge>
                <Badge tone="muted">{discovery.data.data_status}</Badge>
              </div>
              {discovery.data.discovered_count === 0 && (
                <Empty>
                  No priced markets are stored for this fixture. That is a data gap, not a finding.
                </Empty>
              )}
              {discovery.data.discovered_count > 0 && (
                <div className="max-h-[360px] overflow-auto">
                  <table className={tableClass}>
                    <thead className={theadClass}>
                      <tr>
                        <th className={thClass}>Raw market</th>
                        <th className={thClass}>Selection</th>
                        <th className={thClass}>Price</th>
                        <th className={thClass}>Bookmaker</th>
                        <th className={thClass}>Captured</th>
                      </tr>
                    </thead>
                    <tbody>
                      {discovery.data.markets.map((row, index) => (
                        <tr key={`${row.raw_name}-${row.raw_selection}-${index}`} className={rowClass}>
                          <td className={tdClass}>
                            <p className="text-mist/90">{row.raw_name}</p>
                            <p className="text-[11px] text-mist/40">
                              {row.family ? row.family : "unmodelled"} · settlement{" "}
                              {row.settlement_group}
                            </p>
                          </td>
                          <td className={tdClass}>{row.selection}</td>
                          <td className={`${tdClass} tabular-nums`}>{fmtOdds(row.price)}</td>
                          <td className={`${tdClass} text-mist/60`}>{row.bookmaker}</td>
                          <td className={`${tdClass} text-mist/45`}>{ageLabel(row.captured_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      {fixtureId && (
        <div className="mt-6">
          <Card
            title="Market analysis"
            subtitle="Probability is the model output, blended with the de-vigged price for 1X2 when a fitted blend exists. Confidence is evidence quality; EV only appears when a real price exists."
            actions={
              analysis.data ? (
                <Badge tone={analysis.data.qualified > 0 ? "good" : "muted"}>
                  {analysis.data.qualified} qualified / {analysis.data.evaluated} evaluated
                </Badge>
              ) : undefined
            }
          >
            {analysis.loading && <Loading label="Analysing discovered markets" />}
            {analysis.error && <ErrorNote message={analysis.error} />}

            {analysis.data && analysis.data.qualified === 0 && (
              <div className="mb-4">
                <NoQualified message={analysis.data.message} />
              </div>
            )}

            {analysis.data && analysis.data.blend.applied && (
              <div className="mb-4">
                <Note>
                  1X2 probabilities pool the model with the de-vigged price (model weight{" "}
                  {((analysis.data.blend.weight ?? 0) * 100).toFixed(1)}%, temperature{" "}
                  {(analysis.data.blend.temperature ?? 1).toFixed(2)}).{" "}
                  {analysis.data.blend.note}
                </Note>
              </div>
            )}

            {analysis.data && offered.length > 0 && (
              <div className="overflow-x-auto">
                <table className={tableClass}>
                  <thead className={theadClass}>
                    <tr>
                      <th className={thClass}>Market</th>
                      <th className={thClass}>Selection</th>
                      <th className={thClass}>Price</th>
                      <th className={thClass}>Implied</th>
                      <th className={thClass}>De-vig</th>
                      <th className={thClass}>Probability</th>
                      <th className={thClass}>EV</th>
                      <th className={thClass}>Confidence</th>
                      <th className={thClass}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {offered.map((row, index) => (
                      <tr key={`${row.bookmaker}-${row.raw_name}-${row.selection}-${index}`} className={rowClass}>
                        <td className={tdClass}>
                          <p className="text-mist/90">{row.label}</p>
                          <p className="text-[11px] text-mist/40">
                            {row.bookmaker} · {row.source}
                          </p>
                        </td>
                        <td className={tdClass}>
                          {row.selection}
                          {row.line !== null ? ` ${row.line}` : ""}
                        </td>
                        <td className={`${tdClass} tabular-nums`}>{fmtOdds(row.price)}</td>
                        <td className={`${tdClass} tabular-nums text-mist/55`}>
                          {fmtPercent(row.implied_probability)}
                        </td>
                        <td className={`${tdClass} tabular-nums text-mist/55`}>
                          {fmtPercent(row.devigged_probability)}
                        </td>
                        <td className={tdClass}>
                          <ProbabilityBar
                            value={row.probability}
                            threshold={analysis.data?.threshold ?? 0.85}
                          />
                          {row.blended && row.probability_model !== null && (
                            <p className="mt-1 text-[11px] text-mist/40">
                              pure model {fmtPercent(row.probability_model)} · blend w{" "}
                              {((row.blend_weight ?? 0) * 100).toFixed(1)}%
                            </p>
                          )}
                        </td>
                        <td
                          className={`${tdClass} tabular-nums ${
                            row.ev !== null && row.ev > 0 ? "text-lime" : "text-mist/55"
                          }`}
                        >
                          {row.ev !== null ? `${(row.ev * 100).toFixed(1)}%` : "unavailable"}
                        </td>
                        <td className={`${tdClass} tabular-nums text-mist/60`}>
                          {fmtPercent(row.confidence)}
                        </td>
                        <td className={tdClass}>
                          <Badge tone={STATUS_TONES[row.model_status] ?? "default"}>
                            {row.model_status}
                          </Badge>
                          {row.qualified && <Badge tone="good">qualified</Badge>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {analysis.data && analysis.data.unmodelled.length > 0 && (
              <div className="mt-4">
                <Note tone="warn">
                  {analysis.data.unmodelled.length} priced market
                  {analysis.data.unmodelled.length === 1 ? " is" : "s are"} not modelled yet, so
                  no probability exists. They are shown as unmodelled rather than guessed.
                </Note>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
