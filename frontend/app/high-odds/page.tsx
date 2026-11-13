"use client";

import Link from "next/link";

import {
  Badge,
  Card,
  ErrorNote,
  Loading,
  Note,
  PageHeader,
  Stat,
  tableClass,
  tdClass,
  thClass,
  theadClass,
  rowClass,
} from "@/components/ui";
import { ageLabel, fmtOdds, fmtPercent, titleCase } from "@/lib/format";
import type { HighOddsLeg, HighOddsReport, HighOddsSlip } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function evLabel(leg: HighOddsLeg): string {
  const value = leg.expected_value * 100;
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function LegTable({ legs }: { legs: HighOddsLeg[] }) {
  return (
    <div className="overflow-x-auto">
      <table className={tableClass}>
        <thead className={theadClass}>
          <tr>
            <th className={thClass}>Match</th>
            <th className={thClass}>Selection</th>
            <th className={thClass}>Price</th>
            <th className={thClass}>Mkt margin</th>
            <th className={thClass}>Blended</th>
            <th className={thClass}>EV</th>
          </tr>
        </thead>
        <tbody>
          {legs.map((leg) => (
            <tr key={`${leg.fixture_id}-${leg.market_key}`} className={rowClass}>
              <td className={tdClass}>
                <p className="text-mist/90">{leg.match}</p>
                <p className="text-[11px] text-mist/45">
                  {leg.competition} · {leg.kickoff_eat}
                </p>
              </td>
              <td className={tdClass}>
                <p>{leg.selection}</p>
                <p className="text-[11px] text-mist/45">{titleCase(leg.market_type)}</p>
              </td>
              <td className={`${tdClass} tabular-nums`}>{fmtOdds(leg.odds)}</td>
              <td className={`${tdClass} tabular-nums text-mist/65`}>
                {((leg.market_overround - 1) * 100).toFixed(1)}%
              </td>
              <td className={`${tdClass} tabular-nums`}>{fmtPercent(leg.probability)}</td>
              <td
                className={`${tdClass} tabular-nums ${
                  leg.expected_value > 0 ? "text-lime" : "text-mist/55"
                }`}
              >
                {evLabel(leg)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SlipCard({ slip, rank }: { slip: HighOddsSlip; rank: number }) {
  const priceSource = slip.legs[0];
  return (
    <Card
      title={`Slip ${rank} · ${slip.size} legs`}
      subtitle={`${fmtOdds(slip.total_odds)} total · ${fmtPercent(
        slip.combined_probability,
      )} combined · ${slip.expected_value_percent.toFixed(1)}% EV`}
      actions={<Badge tone="good">every leg +EV</Badge>}
    >
      <LegTable legs={slip.legs} />
      <div className="mt-3 space-y-1 text-[11px] leading-relaxed text-mist/45">
        <p>
          Weakest leg {fmtPercent(slip.min_leg_probability)} · break-even strike for this price{" "}
          {fmtPercent(slip.implied_probability ?? null)}
        </p>
        {priceSource && (
          <p>
            Prices from {priceSource.bookmaker ?? "an unnamed bookmaker"}
            {priceSource.source ? ` (${priceSource.source})` : ""} · captured{" "}
            {ageLabel(priceSource.captured_at)}
            {priceSource.price_age_hours !== null
              ? ` · ${priceSource.price_age_hours.toFixed(1)} h old at analysis`
              : ""}
          </p>
        )}
      </div>
    </Card>
  );
}

export default function HighOddsPage() {
  const report = useApi<HighOddsReport>("/markets/high-odds");
  const data = report.data;
  const slips = data?.slips ?? [];
  const closest = data?.closest_legs ?? [];
  const rejected = Object.entries(data?.rejected ?? {}).filter(([, count]) => count > 0);

  return (
    <div>
      <PageHeader
        eyebrow="Near-6.00 tickets"
        title="High odds slips"
        description="Three to five result legs from different matches, combined to land between 5.50 and 6.50. A leg is only allowed here when its blended model-market probability beats the attributed price, so 10 staked returns 60 only if one ticket in six genuinely wins. The 85% gate is a separate mode and is untouched."
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
          label="Slips published"
          value={data ? data.count : "—"}
          hint={data ? `Shown only inside the band; cap is 3` : "Loading"}
          tone={data && data.count > 0 ? "good" : "default"}
        />
        <Stat
          label="Target odds"
          value={data ? fmtOdds(data.params.target_total_odds) : "—"}
          hint={
            data
              ? `Band ${fmtOdds(data.params.odds_band[0])}–${fmtOdds(data.params.odds_band[1])}`
              : "Loading"
          }
        />
        <Stat
          label="+EV legs found"
          value={data ? `${data.positive_ev_legs} / ${data.legs_evaluated}` : "—"}
          hint="Beats the price after the bookmaker margin"
          tone={data && data.positive_ev_legs > 0 ? "good" : "default"}
        />
        <Stat
          label="Break-even strike"
          value={data ? `${data.diagnostics.required_strike_rate_percent.toFixed(1)}%` : "—"}
          hint="Share of 6.00 tickets that must win"
        />
      </div>

      <div className="mt-4">
        {report.loading && <Loading label="Scanning today's slate for value legs" />}
        {report.error && <ErrorNote message={report.error} />}
      </div>

      {data && (
        <>
          <div className="mt-4">
            <Note>{data.note}</Note>
          </div>

          {slips.length === 0 ? (
            <div className="mt-4 space-y-4">
              <div className="rounded-xl border border-dashed border-edge bg-pitch/50 px-5 py-8 text-center">
                <p className="text-sm font-semibold tracking-wide text-mist/80">{data.message}</p>
                <p className="mt-2 text-xs text-mist/45">
                  No leg was priced above its blended probability, so there is no honest 6.00
                  ticket to publish. The expected-value rule is never relaxed to fill this page.
                </p>
                {(data.rejected.inconsistent_market_prices ?? 0) > 0 && (
                  <p className="mt-1 text-xs text-mist/35">
                    {data.rejected.inconsistent_market_prices} fixture
                    {data.rejected.inconsistent_market_prices === 1 ? "" : "s"} were excluded
                    because the three best prices came from different bookmakers and no longer
                    add up to a real market; de-vigging those would invent an edge.
                  </p>
                )}
              </div>

              <Card
                title="How close the best legs were"
                subtitle="Prices versus the blended probability, best first"
              >
                {closest.length === 0 ? (
                  <p className="text-sm text-mist/55">
                    No priced fixture made it far enough to compare. {data.scope.fixtures_on_slate}{" "}
                    fixture{data.scope.fixtures_on_slate === 1 ? "" : "s"} on the slate.
                  </p>
                ) : (
                  <>
                    {data.diagnostics.best_leg_match && (
                      <p className="mb-3 text-xs text-mist/60">
                        Best leg: {data.diagnostics.best_leg_match} ·{" "}
                        {data.diagnostics.best_leg_selection} at{" "}
                        {fmtOdds(data.diagnostics.best_leg_odds)} · EV{" "}
                        {data.diagnostics.best_leg_ev !== null
                          ? `${(data.diagnostics.best_leg_ev * 100).toFixed(1)}%`
                          : "—"}
                      </p>
                    )}
                    <LegTable legs={closest} />
                    <p className="mt-3 text-[11px] text-mist/40">
                      These legs are below the bar and are shown for context only — they are not
                      picks.
                    </p>
                  </>
                )}
              </Card>
            </div>
          ) : (
            <div className="mt-4 space-y-4">
              {slips.map((slip, index) => (
                <SlipCard key={index} slip={slip} rank={index + 1} />
              ))}
              {slips.length < 3 && (
                <p className="text-[11px] text-mist/45">
                  Only {slips.length} disjoint slip{slips.length === 1 ? "" : "s"} fit the band
                  with distinct matches, so the list is shorter than its cap of 3.
                </p>
              )}
            </div>
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

          <p className="mt-3 text-[11px] text-mist/40">
            Fixtures on the slate: {data.scope.fixtures_on_slate} · analysed{" "}
            {data.scope.fixtures_analysed} · candidate legs {data.scope.candidate_legs} ·{" "}
            {data.diagnostics.combos_in_band} combination
            {data.diagnostics.combos_in_band === 1 ? "" : "s"} inside the band before de-duplication.
          </p>

          <p className="mt-2 text-[11px] text-mist/45">
            Want the arithmetic both ways?{" "}
            <Link href="/bankroll" className="text-lime/80 hover:text-lime">
              Open the bankroll planner →
            </Link>
          </p>
        </>
      )}
    </div>
  );
}
