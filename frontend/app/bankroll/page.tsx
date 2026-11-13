"use client";

import { useMemo, useState } from "react";

import {
  Badge,
  Card,
  ErrorNote,
  Loading,
  Note,
  PageHeader,
  Stat,
} from "@/components/ui";
import { fmtMoney, fmtOdds } from "@/lib/format";
import type { BankrollPresets, BankrollPlan, BankrollPlanResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const FIELDS = [
  { key: "stake", label: "Stake", hint: "What you would risk on one slip" },
  { key: "odds", label: "Total odds", hint: "Combined price, above 1.00" },
  { key: "bankroll", label: "Bankroll", hint: "Total paper balance" },
  { key: "targetProfit", label: "Target profit", hint: "Leave blank to skip" },
  { key: "slips", label: "Slips per day", hint: "For the daily exposure check" },
] as const;

type Draft = Record<(typeof FIELDS)[number]["key"], string>;

const DEFAULT_DRAFT: Draft = {
  stake: "50",
  odds: "1.20",
  bankroll: "1000",
  targetProfit: "10",
  slips: "1",
};

function parse(raw: string): number | null {
  if (raw.trim() === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function buildPlanPath(draft: Draft): string | null {
  const stake = parse(draft.stake);
  const odds = parse(draft.odds);
  const bankroll = parse(draft.bankroll);
  const profit = parse(draft.targetProfit);
  const slips = parse(draft.slips);
  if (stake === null || odds === null || stake <= 0 || odds <= 1) return null;
  if (bankroll === null || bankroll <= 0) return null;
  const params = new URLSearchParams({
    stake: String(stake),
    odds: String(odds),
    bankroll: String(bankroll),
    slips_per_day: String(slips !== null && slips >= 1 ? Math.round(slips) : 1),
  });
  if (profit !== null && profit >= 0) params.set("target_profit", String(profit));
  return `/bankroll/plan?${params.toString()}`;
}

function WarningList({ warnings }: { warnings: Array<{ code: string; message: string }> }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="space-y-2">
      {warnings.map((warning) => (
        <li
          key={warning.code}
          className="rounded-xl border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-xs text-amber-200"
        >
          {warning.message}
        </li>
      ))}
    </ul>
  );
}

function PlanSummary({ plan }: { plan: BankrollPlan }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Stat
        label="Returns"
        value={fmtMoney(plan.potential_return, plan.currency)}
        hint={`${fmtMoney(plan.stake, plan.currency)} at ${fmtOdds(plan.total_odds)}`}
      />
      <Stat
        label="Profit if it wins"
        value={fmtMoney(plan.potential_profit, plan.currency)}
        hint="Before any loss on the losing tickets"
        tone={plan.potential_profit > 0 ? "good" : "default"}
      />
      <Stat
        label="Break-even strike"
        value={`${plan.required_strike_rate_percent.toFixed(1)}%`}
        hint={`About ${plan.break_even_wins_per_10.toFixed(1)} wins in 10`}
      />
      <Stat
        label="Stake vs cap"
        value={
          plan.max_stake_by_policy === null
            ? "no bankroll"
            : `${fmtMoney(plan.stake, plan.currency)} / ${fmtMoney(plan.max_stake_by_policy, plan.currency)}`
        }
        hint={
          plan.max_stake_by_policy === null
            ? "Enter a bankroll to check the cap"
            : `${plan.max_stake_percent.toFixed(0)}% policy cap`
        }
        tone={plan.stake_within_cap ? "default" : "warn"}
      />
    </div>
  );
}

function PresetCard({ preset }: { preset: BankrollPresets["presets"][number] }) {
  const { plan } = preset;
  return (
    <Card
      title={preset.label}
      subtitle={preset.description}
      actions={
        <Badge tone={plan.stake_within_cap ? "good" : "warn"}>
          {plan.required_strike_rate_percent.toFixed(1)}% strike needed
        </Badge>
      }
    >
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-mist/40">Stake</dt>
          <dd className="mt-0.5 text-sm tabular-nums text-mist/85">
            {fmtMoney(plan.stake, plan.currency)}
          </dd>
        </div>
        <div>
          <dt className="text-mist/40">Total odds</dt>
          <dd className="mt-0.5 text-sm tabular-nums text-mist/85">
            {fmtOdds(plan.total_odds)}
          </dd>
        </div>
        <div>
          <dt className="text-mist/40">Returns</dt>
          <dd className="mt-0.5 text-sm tabular-nums text-mist/85">
            {fmtMoney(plan.potential_return, plan.currency)}
          </dd>
        </div>
        <div>
          <dt className="text-mist/40">Profit</dt>
          <dd className="mt-0.5 text-sm tabular-nums text-lime">
            {fmtMoney(plan.potential_profit, plan.currency)}
          </dd>
        </div>
        <div>
          <dt className="text-mist/40">Stake for the target</dt>
          <dd className="mt-0.5 text-sm tabular-nums text-mist/85">
            {fmtMoney(plan.stake_for_target_profit, plan.currency)}
          </dd>
        </div>
        <div>
          <dt className="text-mist/40">Share of bankroll</dt>
          <dd className="mt-0.5 text-sm tabular-nums text-mist/85">
            {plan.stake_percent_of_bankroll === null
              ? "—"
              : `${plan.stake_percent_of_bankroll.toFixed(1)}%`}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-[11px] leading-relaxed text-mist/45">
        {plan.required_strike_rate_percent.toFixed(1)}% is the break-even strike for this price —
        not a forecast that it will happen.
      </p>
      {plan.warnings.length > 0 && (
        <div className="mt-3">
          <WarningList warnings={plan.warnings} />
        </div>
      )}
    </Card>
  );
}

export default function BankrollPage() {
  const [draft, setDraft] = useState<Draft>(DEFAULT_DRAFT);
  const [applied, setApplied] = useState<Draft>(DEFAULT_DRAFT);

  const planPath = useMemo(() => buildPlanPath(applied), [applied]);
  const plan = useApi<BankrollPlanResponse>(planPath);

  const presetPath = useMemo(() => {
    const bankroll = parse(applied.bankroll);
    return bankroll !== null && bankroll > 0
      ? `/bankroll/presets?bankroll=${bankroll}`
      : "/bankroll/presets";
  }, [applied.bankroll]);
  const presets = useApi<BankrollPresets>(presetPath);

  const data = plan.data;
  const exposure = data?.daily_exposure ?? null;

  const update = (key: keyof Draft, value: string) =>
    setDraft((current) => ({ ...current, [key]: value }));

  return (
    <div>
      <PageHeader
        eyebrow="Paper staking"
        title="Bankroll planner"
        description="Both readings of 50 over 10 use the same arithmetic: at 1.20 you stake 50 to profit 10 and need 83.3% of tickets to win; at 6.00 you stake 10 to profit 50 and need 16.7%. Nothing here predicts a result, and no stake is ever placed."
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <Stat
          label="Low risk reading"
          value="50 → 10"
          hint="1.20 · 83.3% break-even strike"
        />
        <Stat
          label="High odds reading"
          value="10 → 50"
          hint="6.00 · 16.7% break-even strike"
        />
        <Stat
          label="Policy caps"
          value="5% / 20%"
          hint="Per stake and per day, of the bankroll"
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card
          title="Your plan"
          subtitle="Paper arithmetic only; nothing is staked"
          className="xl:col-span-1"
        >
          <div className="space-y-3">
            {FIELDS.map((field) => (
              <label key={field.key} className="block">
                <span className="text-[11px] uppercase tracking-wider text-mist/45">
                  {field.label}
                </span>
                <input
                  type="number"
                  inputMode="decimal"
                  min={0}
                  step="any"
                  value={draft[field.key]}
                  onChange={(event) => update(field.key, event.target.value)}
                  className="mt-1 w-full rounded-lg border border-edge bg-pitch px-3 py-2 text-sm tabular-nums text-mist focus:border-lime/50 focus:outline-none"
                />
                <span className="mt-1 block text-[11px] text-mist/35">{field.hint}</span>
              </label>
            ))}
            <button
              type="button"
              onClick={() => setApplied(draft)}
              className="w-full rounded-lg border border-lime/40 bg-lime/10 py-2 text-sm font-medium text-lime hover:bg-lime/15"
            >
              Calculate
            </button>
            {planPath === null && (
              <p className="text-xs text-amber-200">
                Enter a positive stake, total odds above 1.00 and a bankroll to calculate.
              </p>
            )}
          </div>
        </Card>

        <div className="space-y-4 xl:col-span-2">
          {plan.loading && <Loading label="Working out the paper arithmetic" />}
          {plan.error && <ErrorNote message={plan.error} />}

          {data && (
            <>
              <PlanSummary plan={data} />
              {data.warnings.length > 0 && <WarningList warnings={data.warnings} />}
              <Note>{data.note}</Note>

              {exposure && (
                <Card
                  title="Daily exposure"
                  subtitle={`${exposure.slips_per_day} slip${
                    exposure.slips_per_day === 1 ? "" : "s"
                  } per day at ${fmtMoney(exposure.stake_per_slip, exposure.currency)}`}
                  actions={
                    <Badge tone={exposure.exposure_within_cap ? "good" : "warn"}>
                      {exposure.exposure_within_cap ? "within the cap" : "above the cap"}
                    </Badge>
                  }
                >
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div>
                      <p className="text-[11px] uppercase tracking-wider text-mist/45">
                        Total staked
                      </p>
                      <p className="mt-1 text-lg tabular-nums text-mist/90">
                        {fmtMoney(exposure.total_stake, exposure.currency)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wider text-mist/45">
                        Daily cap
                      </p>
                      <p className="mt-1 text-lg tabular-nums text-mist/90">
                        {fmtMoney(exposure.max_daily_stake, exposure.currency)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-wider text-mist/45">
                        Policy
                      </p>
                      <p className="mt-1 text-lg tabular-nums text-mist/90">
                        {exposure.max_daily_exposure_percent.toFixed(0)}% of bankroll
                      </p>
                    </div>
                  </div>
                  {exposure.warnings.length > 0 && (
                    <div className="mt-3">
                      <WarningList warnings={exposure.warnings} />
                    </div>
                  )}
                </Card>
              )}
            </>
          )}
        </div>
      </div>

      <div className="mt-6">
        <h2 className="mb-3 text-sm font-semibold tracking-wide text-mist">
          The two readings of 50/10
        </h2>
        {presets.error && <ErrorNote message={presets.error} />}
        {presets.data && (
          <>
            <div className="grid gap-4 xl:grid-cols-2">
              {presets.data.presets.map((preset) => (
                <PresetCard key={preset.key} preset={preset} />
              ))}
            </div>
            <p className="mt-3 text-[11px] text-mist/45">{presets.data.note}</p>
          </>
        )}
      </div>
    </div>
  );
}
