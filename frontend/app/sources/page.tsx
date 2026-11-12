"use client";

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
import { ageLabel, eatDateTime } from "@/lib/format";
import type { SourcesResponse, SyncLog } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type ConnectorRow = {
  bookmaker: string;
  configured: boolean;
  fixtures_available: number;
};

export default function SourcesPage() {
  const sources = useApi<SourcesResponse>("/sources");
  const logs = useApi<SyncLog[]>("/sources/sync-logs?limit=50");
  const connectors = useApi<ConnectorRow[]>("/connectors/status");

  const configured = sources.data?.configured_count ?? 0;
  const total = sources.data?.providers.length ?? 0;

  return (
    <div>
      <PageHeader
        eyebrow="Data sources"
        title="Providers and provenance"
        description="Free-plan adapters only. Unconfigured providers are shown as unconfigured — never simulated. Quota limits are recorded for planning and must be re-verified with each provider before relying on them."
        actions={
          <button
            type="button"
            onClick={() => {
              sources.reload();
              logs.reload();
              connectors.reload();
            }}
            className="border border-edge bg-panel text-mist/80 hover:border-lime/40 hover:text-lime"
          >
            Refresh
          </button>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Providers configured"
          value={`${configured} / ${total || "—"}`}
          tone={configured > 0 ? "good" : "warn"}
          hint="A key is present in the environment"
        />
        <Stat
          label="Sync log entries"
          value={logs.data?.length ?? "—"}
          hint="Most recent 50 runs"
        />
        <Stat
          label="Bookmaker connectors"
          value={
            connectors.data
              ? `${connectors.data.filter((row) => row.configured).length} / ${connectors.data.length}`
              : "—"
          }
          hint="Configured adapters"
        />
        <Stat
          label="Paid APIs required"
          value="None"
          hint="Free plans only, by design"
        />
      </div>

      {sources.loading && <div className="mt-4"><Loading label="Loading providers" /></div>}
      {sources.error && <div className="mt-4"><ErrorNote message={sources.error} /></div>}

      {sources.data && (
        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {sources.data.providers.map((provider) => (
            <Card key={provider.name}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-mist">{provider.name}</h3>
                  <p className="mt-1 text-[11px] text-mist/45">
                    {provider.key_env ? `Key: ${provider.key_env}` : "No key required"}
                  </p>
                </div>
                <Badge tone={provider.configured ? "good" : "muted"}>
                  {provider.configured ? "Configured" : "Not configured"}
                </Badge>
              </div>
              <dl className="mt-3 space-y-1.5 text-xs">
                <div className="flex justify-between gap-2">
                  <dt className="text-mist/40">Free plan</dt>
                  <dd className="text-right text-mist/75">{provider.free_plan}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-mist/40">Daily quota</dt>
                  <dd className="text-right tabular-nums text-mist/75">
                    {provider.daily_quota ?? "—"}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-mist/40">Quota used</dt>
                  <dd className="text-right tabular-nums text-mist/75">
                    {provider.quota_used ?? "—"}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-mist/40">Last sync</dt>
                  <dd className="text-right text-mist/75">
                    {provider.last_sync_at ? ageLabel(provider.last_sync_at) : "never"}
                  </dd>
                </div>
                {provider.status && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-mist/40">Status</dt>
                    <dd className="text-right text-mist/75">{provider.status}</dd>
                  </div>
                )}
              </dl>
              {provider.notes && (
                <p className="mt-3 border-t border-edge/60 pt-3 text-[11px] leading-relaxed text-mist/50">
                  {provider.notes}
                </p>
              )}
            </Card>
          ))}
        </div>
      )}

      <div className="mt-4">
        <Note>
          {sources.data?.note ??
            "Provider availability changes. Verify limits and terms directly before depending on any feed."}
        </Note>
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-5">
        <Card
          className="xl:col-span-3"
          title="Bookmaker connectors"
          subtitle="BetPawa Uganda is the primary source; the rest are secondary."
        >
          {connectors.loading && <Loading label="Checking connectors" />}
          {connectors.error && <ErrorNote message={connectors.error} />}
          {connectors.data && connectors.data.length === 0 && (
            <Empty>No bookmaker connectors are registered.</Empty>
          )}
          {connectors.data && connectors.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className={tableClass}>
                <thead className={theadClass}>
                  <tr>
                    <th className={thClass}>Bookmaker</th>
                    <th className={thClass}>Configured</th>
                    <th className={thClass}>Fixtures available offline</th>
                  </tr>
                </thead>
                <tbody>
                  {connectors.data.map((row) => (
                    <tr key={row.bookmaker} className={rowClass}>
                      <td className={tdClass}>{row.bookmaker}</td>
                      <td className={tdClass}>
                        <Badge tone={row.configured ? "good" : "muted"}>
                          {row.configured ? "Yes" : "No"}
                        </Badge>
                      </td>
                      <td className={`${tdClass} tabular-nums`}>{row.fixtures_available}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          className="xl:col-span-2"
          title="Attribution policy"
          subtitle="Every price must be traceable."
        >
          <ul className="space-y-2 text-xs leading-relaxed text-mist/60">
            <li>• Each stored price keeps its bookmaker, source and capture timestamp.</li>
            <li>• Estimated prices are labelled as estimates and never shown as BetPawa prices.</li>
            <li>• Missing prices make EV unavailable — they are not filled with guesses.</li>
            <li>• Sync logs record request counts and records written for every run.</li>
            <li>• API keys stay server-side; they are never sent to this dashboard.</li>
          </ul>
        </Card>
      </div>

      <div className="mt-6">
        <Card title="Recent sync logs" subtitle="Newest first.">
          {logs.loading && <Loading label="Loading sync logs" />}
          {logs.error && <ErrorNote message={logs.error} />}
          {logs.data && logs.data.length === 0 && (
            <Empty>No sync has run yet. Trigger a provider sync on the backend.</Empty>
          )}
          {logs.data && logs.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className={tableClass}>
                <thead className={theadClass}>
                  <tr>
                    <th className={thClass}>Started</th>
                    <th className={thClass}>Entity</th>
                    <th className={thClass}>Status</th>
                    <th className={thClass}>Records</th>
                    <th className={thClass}>Requests</th>
                    <th className={thClass}>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.data.map((row) => (
                    <tr key={row.id} className={rowClass}>
                      <td className={`${tdClass} whitespace-nowrap text-mist/55`}>
                        {eatDateTime(row.started_at)}
                      </td>
                      <td className={tdClass}>{row.entity}</td>
                      <td className={tdClass}>
                        <Badge
                          tone={
                            row.status === "success"
                              ? "good"
                              : row.status === "partial"
                                ? "warn"
                                : "bad"
                          }
                        >
                          {row.status}
                        </Badge>
                      </td>
                      <td className={`${tdClass} tabular-nums`}>{row.records_written}</td>
                      <td className={`${tdClass} tabular-nums`}>{row.request_count}</td>
                      <td className={`${tdClass} text-mist/50`}>{row.message ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
