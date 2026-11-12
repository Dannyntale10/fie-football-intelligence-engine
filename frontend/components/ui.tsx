import type { ReactNode } from "react";

type Tone = "default" | "good" | "warn" | "bad" | "muted";

const toneClasses: Record<Tone, string> = {
  default: "border-edge bg-panel/80 text-mist",
  good: "border-lime/40 bg-lime/10 text-lime",
  warn: "border-amber-400/40 bg-amber-400/10 text-amber-200",
  bad: "border-rose-400/40 bg-rose-400/10 text-rose-200",
  muted: "border-edge bg-pitch/60 text-mist/60",
};

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-2xl border border-edge bg-panel/80 p-5 shadow-[0_1px_0_0_rgba(255,255,255,0.03)_inset] ${className}`}
    >
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-wide text-mist">{title}</h2>}
            {subtitle && <p className="mt-1 text-xs text-mist/55">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        {eyebrow && (
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-lime/70">
            {eyebrow}
          </p>
        )}
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-white sm:text-3xl">
          {title}
        </h1>
        {description && <p className="mt-2 max-w-3xl text-sm text-mist/60">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className={`rounded-2xl border p-4 ${toneClasses[tone]}`}>
      <p className="text-[11px] font-medium uppercase tracking-wider opacity-70">{label}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums leading-none">{value}</p>
      {hint && <p className="mt-2 text-xs opacity-70">{hint}</p>}
    </div>
  );
}

export function Badge({
  children,
  tone = "default",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${toneClasses[tone]}`}
    >
      {children}
    </span>
  );
}

export function Note({ children, tone = "muted" }: { children: ReactNode; tone?: Tone }) {
  return (
    <div className={`rounded-xl border px-4 py-3 text-xs leading-relaxed ${toneClasses[tone]}`}>
      {children}
    </div>
  );
}

export function NoQualified({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-edge bg-pitch/50 px-5 py-8 text-center">
      <p className="text-sm font-semibold tracking-wide text-mist/80">{message}</p>
      <p className="mt-2 text-xs text-mist/45">
        Nothing met the 85% probability gate with sufficient data confidence. The gate is never
        lowered to fill a report.
      </p>
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-edge bg-panel/60 px-4 py-6 text-sm text-mist/60">
      <span className="h-3 w-3 animate-pulse rounded-full bg-lime" />
      {label}…
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-rose-400/30 bg-rose-400/5 px-4 py-3 text-sm text-rose-200">
      {message}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-edge px-4 py-8 text-center text-sm text-mist/50">
      {children}
    </div>
  );
}

export function ProbabilityBar({
  value,
  threshold = 0.85,
}: {
  value: number | null;
  threshold?: number;
}) {
  if (value === null || value === undefined) {
    return <span className="text-xs text-mist/40">—</span>;
  }
  const width = Math.max(0, Math.min(1, value)) * 100;
  const qualifies = value >= threshold;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-pitch">
        <div
          className={`h-full rounded-full ${qualifies ? "bg-lime" : "bg-emerald-600/70"}`}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className={`tabular-nums ${qualifies ? "text-lime" : "text-mist/80"}`}>
        {(value * 100).toFixed(1)}%
      </span>
    </div>
  );
}

export const tableClass = "w-full border-collapse text-left text-sm";
export const theadClass =
  "border-b border-edge text-[11px] uppercase tracking-wider text-mist/45";
export const thClass = "px-3 py-2 font-medium whitespace-nowrap";
export const tdClass = "px-3 py-2.5 align-top text-mist/85";
export const rowClass = "border-b border-edge/60 last:border-0 hover:bg-pitch/40";
