"use client";

import { Badge, Card, Note } from "@/components/ui";
import { fmtPercent } from "@/lib/format";
import type { LiveCalibrationReport } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export function LiveCalibrationPanel() {
  const { data, loading, error } = useApi<LiveCalibrationReport>("/live/calibration");

  return (
    <Card
      title="Live forecast calibration"
      actions={
        data ? <Badge tone="muted">{data.verdict.replaceAll("_", " ")}</Badge> : null
      }
    >
      {loading && <p className="text-sm text-mist/55">Reading settled snapshots…</p>}
      {error && <Note tone="warn">{error}</Note>}
      {data && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div>
              <p className="text-xs text-mist/45">Snapshots</p>
              <p className="tabular-nums text-white">{data.sample.snapshots}</p>
            </div>
            <div>
              <p className="text-xs text-mist/45">Matches</p>
              <p className="tabular-nums text-white">{data.sample.matches}</p>
            </div>
            <div>
              <p className="text-xs text-mist/45">Brier live</p>
              <p className="tabular-nums text-white">
                {data.overall.brier_live?.toFixed(4) ?? "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-mist/45">Brier pre-match</p>
              <p className="tabular-nums text-white">
                {data.overall.brier_prematch?.toFixed(4) ?? "—"}
              </p>
            </div>
          </div>
          {data.overall.n > 0 && (
            <table className="w-full text-left text-xs">
              <thead className="text-mist/40">
                <tr>
                  <th className="py-1">Window</th>
                  <th className="py-1">n</th>
                  <th className="py-1">Live</th>
                  <th className="py-1">Pre-match</th>
                </tr>
              </thead>
              <tbody>
                {data.buckets.map((row) => (
                  <tr key={row.bucket} className="border-t border-edge">
                    <td className="py-1">{row.bucket}</td>
                    <td className="py-1 tabular-nums">{row.n}</td>
                    <td className="py-1 tabular-nums">
                      {row.brier_live?.toFixed(4) ?? "—"}
                    </td>
                    <td className="py-1 tabular-nums">
                      {row.brier_prematch?.toFixed(4) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-[11px] text-mist/45">
            Intensity k = {data.intensity.k.toFixed(2)} ({data.intensity.source}). Below
            200 settled snapshots the verdict stays insufficient.
          </p>
        </div>
      )}
    </Card>
  );
}
