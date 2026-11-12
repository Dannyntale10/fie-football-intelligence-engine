"use client";

import { useEffect, useMemo, useState } from "react";

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
import { apiPost, apiPut } from "@/lib/api";
import { eatDateTime, titleCase } from "@/lib/format";
import type { NotificationRow, SettingsResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const LOCKED = new Set(["qualification_threshold", "auto_place_bets"]);

const DESCRIPTIONS: Record<string, string> = {
  timezone: "Scheduler timezone.",
  timezone_label: "Display label for the timezone.",
  qualification_threshold: "Locked at 0.85 by policy. It cannot be lowered to fill a report.",
  minimum_data_confidence: "Minimum evidence confidence before a selection can qualify.",
  paper_trading: "Paper mode keeps the platform from claiming real profit.",
  auto_place_bets: "Locked off. The platform never places bets automatically.",
  schedule_fixture_scan: "Daily fixture scan time (EAT).",
  schedule_statistics_sync: "Daily statistics sync time (EAT).",
  schedule_model_run: "Daily model run time (EAT).",
  schedule_shortlist: "Daily shortlist build time (EAT).",
  schedule_odds_refresh_minutes: "Minutes between odds refreshes.",
  schedule_lineups_minutes_before_kickoff: "How early to check lineups before kickoff.",
  telegram_allowlist: "Comma-separated chat IDs allowed to command the bot.",
};

export default function SettingsPage() {
  const settings = useApi<SettingsResponse>("/settings");
  const notifications = useApi<NotificationRow[]>("/notifications?limit=25");

  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  const [channel, setChannel] = useState("telegram");
  const [recipient, setRecipient] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

  useEffect(() => {
    if (settings.data) setDraft(settings.data.settings);
  }, [settings.data]);

  const editable = useMemo(
    () => settings.data?.editable ?? [],
    [settings.data],
  );

  async function save(key: string) {
    const value = draft[key] ?? "";
    setSaving(key);
    setMessage(null);
    try {
      await apiPut(`/settings/${key}`, { value });
      setMessage({ tone: "good", text: `${titleCase(key)} saved.` });
      settings.reload();
    } catch (error) {
      setMessage({
        tone: "bad",
        text: error instanceof Error ? error.message : "Could not save the setting.",
      });
    } finally {
      setSaving(null);
    }
  }

  async function sendNotification() {
    if (!body.trim()) {
      setMessage({ tone: "bad", text: "Write a message body first." });
      return;
    }
    setSaving("notification");
    setMessage(null);
    try {
      await apiPost("/notifications", {
        channel,
        recipient: recipient || null,
        subject: subject || null,
        body,
      });
      setBody("");
      setSubject("");
      setMessage({ tone: "good", text: "Notification queued." });
      notifications.reload();
    } catch (error) {
      setMessage({
        tone: "bad",
        text: error instanceof Error ? error.message : "Could not queue the notification.",
      });
    } finally {
      setSaving(null);
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Settings"
        title="Platform configuration"
        description="Schedule times use East Africa Time. Two values are locked by policy: the 85% qualification threshold and automatic bet placement."
      />

      {settings.loading && <Loading label="Loading settings" />}
      {settings.error && <ErrorNote message={settings.error} />}

      {message && (
        <div className="mb-4">
          <Note tone={message.tone === "good" ? "good" : "bad"}>{message.text}</Note>
        </div>
      )}

      {settings.data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat
              label="Timezone"
              value={draft.timezone ?? "—"}
              hint={draft.timezone_label ?? undefined}
            />
            <Stat
              label="Qualification gate"
              value={draft.qualification_threshold ?? "0.85"}
              hint="Locked by policy"
            />
            <Stat
              label="Paper trading"
              value={draft.paper_trading === "true" ? "On" : "Off"}
              tone={draft.paper_trading === "true" ? "good" : "warn"}
            />
            <Stat
              label="Auto place bets"
              value={draft.auto_place_bets === "true" ? "On" : "Off"}
              hint="Must remain off"
              tone="good"
            />
          </div>

          <div className="mt-6 grid gap-4 xl:grid-cols-3">
            <Card
              className="xl:col-span-2"
              title="Editable settings"
              subtitle="Each value saves individually. Locked values reject any change at the API."
            >
              <div className="space-y-3">
                {editable.map((key) => {
                  const locked = LOCKED.has(key);
                  const serverValue = settings.data?.settings[key] ?? "";
                  const dirty = (draft[key] ?? "") !== serverValue;
                  return (
                    <div
                      key={key}
                      className="rounded-xl border border-edge bg-pitch/30 p-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <p className="text-sm text-mist">{titleCase(key)}</p>
                          <p className="mt-0.5 text-[11px] text-mist/40">
                            {DESCRIPTIONS[key] ?? key}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          {locked && <Badge tone="warn">Locked</Badge>}
                          {dirty && !locked && <Badge tone="muted">Unsaved</Badge>}
                          <button
                            type="button"
                            disabled={locked || !dirty || saving === key}
                            onClick={() => save(key)}
                            className={`border ${
                              locked || !dirty
                                ? "cursor-not-allowed border-edge bg-panel text-mist/30"
                                : "border-lime/50 bg-lime/10 text-lime hover:bg-lime/20"
                            }`}
                          >
                            {saving === key ? "Saving…" : "Save"}
                          </button>
                        </div>
                      </div>
                      <input
                        value={draft[key] ?? ""}
                        disabled={locked}
                        onChange={(event) =>
                          setDraft((current) => ({ ...current, [key]: event.target.value }))
                        }
                        className={`mt-2 w-full ${locked ? "cursor-not-allowed opacity-60" : ""}`}
                      />
                    </div>
                  );
                })}
              </div>
            </Card>

            <div className="space-y-4">
              <Card title="Policy guardrails">
                <ul className="space-y-2 text-xs leading-relaxed text-mist/60">
                  <li>• The 85% probability gate cannot be edited.</li>
                  <li>• Automatic bet placement is refused by the API.</li>
                  <li>• No winning claim is made while paper trading.</li>
                  <li>• Telegram commands are refused unless the chat ID is allowlisted.</li>
                </ul>
              </Card>

              <Card
                title="Queue a notification"
                subtitle="Stored for the delivery worker; this form does not send directly."
              >
                <div className="space-y-2">
                  <label className="block text-xs text-mist/60">
                    Channel
                    <select
                      value={channel}
                      onChange={(event) => setChannel(event.target.value)}
                      className="mt-1 w-full"
                    >
                      <option value="telegram">Telegram</option>
                      <option value="whatsapp">WhatsApp</option>
                      <option value="email">Email</option>
                    </select>
                  </label>
                  <label className="block text-xs text-mist/60">
                    Recipient (optional)
                    <input
                      value={recipient}
                      onChange={(event) => setRecipient(event.target.value)}
                      className="mt-1 w-full"
                      placeholder="Chat ID or number"
                    />
                  </label>
                  <label className="block text-xs text-mist/60">
                    Subject (optional)
                    <input
                      value={subject}
                      onChange={(event) => setSubject(event.target.value)}
                      className="mt-1 w-full"
                    />
                  </label>
                  <label className="block text-xs text-mist/60">
                    Body
                    <textarea
                      value={body}
                      onChange={(event) => setBody(event.target.value)}
                      rows={3}
                      className="mt-1 w-full"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={sendNotification}
                    disabled={saving === "notification"}
                    className="border border-lime/50 bg-lime/10 text-lime hover:bg-lime/20"
                  >
                    {saving === "notification" ? "Queueing…" : "Queue notification"}
                  </button>
                </div>
              </Card>
            </div>
          </div>
        </>
      )}

      <div className="mt-6">
        <Card
          title="Notification queue"
          subtitle="Pending and sent messages recorded by the platform."
          actions={
            <button
              type="button"
              onClick={notifications.reload}
              className="border border-edge bg-panel text-mist/80 hover:border-lime/40 hover:text-lime"
            >
              Refresh
            </button>
          }
        >
          {notifications.loading && <Loading label="Loading notifications" />}
          {notifications.error && <ErrorNote message={notifications.error} />}
          {notifications.data && notifications.data.length === 0 && (
            <Empty>No notifications have been queued.</Empty>
          )}
          {notifications.data && notifications.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className={tableClass}>
                <thead className={theadClass}>
                  <tr>
                    <th className={thClass}>Created</th>
                    <th className={thClass}>Channel</th>
                    <th className={thClass}>Recipient</th>
                    <th className={thClass}>Subject</th>
                    <th className={thClass}>Body</th>
                    <th className={thClass}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {notifications.data.map((row) => (
                    <tr key={row.id} className={rowClass}>
                      <td className={`${tdClass} whitespace-nowrap text-mist/55`}>
                        {eatDateTime(row.created_at)}
                      </td>
                      <td className={tdClass}>{row.channel}</td>
                      <td className={`${tdClass} text-mist/55`}>{row.recipient ?? "—"}</td>
                      <td className={tdClass}>{row.subject ?? "—"}</td>
                      <td className={`${tdClass} max-w-md text-mist/65`}>{row.body}</td>
                      <td className={tdClass}>
                        <Badge tone={row.status === "sent" ? "good" : "muted"}>{row.status}</Badge>
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
          Telegram setup: create a bot with BotFather, put its token in the backend environment, and
          list allowed chat IDs in <span className="text-mist/80">telegram_allowlist</span>. An empty
          allowlist closes the bot to everyone.
        </Note>
      </div>
    </div>
  );
}
