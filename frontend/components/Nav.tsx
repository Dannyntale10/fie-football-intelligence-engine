"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview", short: "OV" },
  { href: "/live", label: "Live", short: "LV" },
  { href: "/top-picks", label: "Top 5 Matches", short: "T5" },
  { href: "/high-odds", label: "High Odds 6.0", short: "H6" },
  { href: "/fixtures", label: "Global Fixtures", short: "GF" },
  { href: "/markets", label: "All Markets", short: "AM" },
  { href: "/predictions", label: "Predictions", short: "PR" },
  { href: "/accumulators", label: "Accumulators", short: "AC" },
  { href: "/bankroll", label: "Bankroll", short: "BK" },
  { href: "/analysis", label: "Match Analysis", short: "MA" },
  { href: "/performance", label: "Performance", short: "PA" },
  { href: "/sources", label: "Data Sources", short: "DS" },
  { href: "/settings", label: "Settings", short: "ST" },
];

export function Nav() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <nav className="flex shrink-0 flex-col border-b border-edge bg-panel/95 backdrop-blur lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:border-b-0 lg:border-r">
      <div className="flex items-center gap-3 px-4 py-4 lg:px-5 lg:py-6">
        <div className="grid h-10 w-10 place-items-center rounded-xl bg-lime text-sm font-black text-pitch">
          FIE
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-white">Football Intelligence</p>
          <p className="truncate text-[11px] text-mist/50">Uganda-first · paper trading</p>
        </div>
      </div>

      <div className="flex gap-1 overflow-x-auto px-2 pb-3 lg:flex-col lg:overflow-visible lg:px-3 lg:pb-0">
        {LINKS.map((link) => {
          const active = isActive(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`group flex shrink-0 items-center gap-3 rounded-xl px-3 py-2 text-sm ${
                active
                  ? "bg-lime/10 text-lime"
                  : "text-mist/65 hover:bg-pitch/60 hover:text-mist"
              }`}
            >
              <span
                className={`grid h-6 w-6 place-items-center rounded-md text-[10px] font-bold ${
                  active ? "bg-lime text-pitch" : "bg-pitch text-mist/50 group-hover:text-mist"
                }`}
              >
                {link.short}
              </span>
              <span className="whitespace-nowrap">{link.label}</span>
            </Link>
          );
        })}
      </div>

      <div className="mt-auto hidden space-y-2 border-t border-edge px-5 py-4 text-[11px] leading-relaxed text-mist/45 lg:block">
        <p className="font-semibold text-mist/70">BetPawa Uganda is the primary price source.</p>
        <p>No paid APIs required. No automatic bet placement. Estimates are never presented as bookmaker prices.</p>
      </div>
    </nav>
  );
}
