import type { Metadata } from "next";

import { Nav } from "@/components/Nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "FIE — Football Intelligence Engine",
  description:
    "Uganda-first football intelligence and betting analysis platform: fixtures, markets, predictions, accumulators, performance and data sources.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen flex-col lg:flex-row">
          <Nav />
          <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
            <div className="mx-auto w-full max-w-[1400px]">{children}</div>
            <footer className="mx-auto mt-10 w-full max-w-[1400px] border-t border-edge pt-4 text-[11px] leading-relaxed text-mist/35">
              Paper trading and research only. Real prices carry bookmaker and capture-time
              attribution. The model does not beat the market until walk-forward calibration says
              so — verify before staking real money.
            </footer>
          </main>
        </div>
      </body>
    </html>
  );
}
