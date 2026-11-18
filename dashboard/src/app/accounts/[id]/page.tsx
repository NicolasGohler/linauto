"use client";

import { use, useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  useAccount,
  useAccountStats,
  useAccountActivity,
  useAccountSchedule,
  useAccountHealth,
  useUpdateAccount,
  useUpdateCookie,
  useArchiveAccount,
  useCampaigns,
} from "@/hooks/use-queries";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { StatCard } from "@/components/stats/stat-card";
import { DailyChart } from "@/components/stats/daily-chart";
import { ActivityTimeline } from "@/components/activity-timeline";
import { LocalTimeCard } from "@/components/accounts/local-time-card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { startLoginSession, finishLoginSession, cancelLoginSession, startBrowseSession, closeBrowseSession, getBrowseSessionStatus, getAvatarUrl, checkConnection, replanAccount, fetchInvitationCount, startWithdrawal, pollWithdrawalStatus, recoverStuckLeads } from "@/lib/api";
import { ProxySettings, emptyProxyForm, type ProxyFormValue } from "@/components/proxy-settings";

export default function AccountDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: account, isLoading } = useAccount(id);
  const { data: stats } = useAccountStats(id);
  const { data: activity } = useAccountActivity(id);
  const { data: schedule } = useAccountSchedule(id);
  const { data: health } = useAccountHealth(id);
  const updateAccount = useUpdateAccount(id);
  const updateCookie = useUpdateCookie(id);
  const archiveAccount = useArchiveAccount();
  const { data: campaigns } = useCampaigns({ account_id: id });
  const [newCookie, setNewCookie] = useState("");
  const [editName, setEditName] = useState("");
  const [editTimezone, setEditTimezone] = useState("");
  const [editDailyLimit, setEditDailyLimit] = useState("");
  const [editDailyMessageLimit, setEditDailyMessageLimit] = useState("");
  const [editWeeklyLimit, setEditWeeklyLimit] = useState("");
  const [settingsInitialized, setSettingsInitialized] = useState(false);
  const [loginSessionActive, setLoginSessionActive] = useState(false);
  const [editWithdrawThreshold, setEditWithdrawThreshold] = useState("");
  const [editAutoWithdrawInterval, setEditAutoWithdrawInterval] = useState("30");
  const [proxyForm, setProxyForm] = useState<ProxyFormValue>(emptyProxyForm);
  const [passwordEditing, setPasswordEditing] = useState(false);
  const [loginLoading, setLoginLoading] = useState(false);
  const [browseSessionActive, setBrowseSessionActive] = useState(false);
  const [browseLoading, setBrowseLoading] = useState(false);

  useEffect(() => {
    getBrowseSessionStatus(id)
      .then(({ active }) => setBrowseSessionActive(active))
      .catch(() => {});
  }, [id]);
  const [connectionChecking, setConnectionChecking] = useState(false);
  const [connectionResult, setConnectionResult] = useState<{
    valid: boolean;
    reason?: string;
    error?: string;
    elapsed_ms?: number;
  } | null>(null);
  const [invCountLoading, setInvCountLoading] = useState(false);
  const [invCount, setInvCount] = useState<number | null>(null);
  const [withdrawCount, setWithdrawCount] = useState("20");
  const [withdrawOrder, setWithdrawOrder] = useState<"oldest" | "newest">("oldest");
  const [withdrawTaskId, setWithdrawTaskId] = useState<string | null>(null);
  const [withdrawStatus, setWithdrawStatus] = useState<{
    status: "running" | "done" | "error";
    withdrawn: string[];
    db_updated: number;
    error: string | null;
  } | null>(null);
  const [stuckLeadsCount, setStuckLeadsCount] = useState<number | null>(null);
  const [stuckLeadsLoading, setStuckLeadsLoading] = useState(false);
  const [stuckLeadsRecovered, setStuckLeadsRecovered] = useState<number | null>(null);

  if (account && !settingsInitialized) {
    setEditName(account.name);
    setEditTimezone(account.timezone ?? "");
    setEditDailyLimit(String(account.daily_limit));
    setEditDailyMessageLimit(String(account.daily_message_limit ?? 15));
    setEditWeeklyLimit(String(account.weekly_limit));
    setEditWithdrawThreshold(account.withdraw_threshold ? String(account.withdraw_threshold) : "");
    setEditAutoWithdrawInterval(String(account.auto_withdraw_interval_days ?? 30));
    setProxyForm({
      host: account.proxy_host ?? "",
      port: account.proxy_port != null ? String(account.proxy_port) : "",
      username: account.proxy_username ?? "",
      password: "",
      country: account.proxy_country ?? "",
    });
    setPasswordEditing(false);
    setSettingsInitialized(true);
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        {/* Header skeleton */}
        <div className="sticky top-0 z-20 bg-background border-b border-border -mx-6 px-6 py-3">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Skeleton className="h-9 w-9 rounded-full shrink-0" />
              <Skeleton className="h-6 w-44" />
              <Skeleton className="h-5 w-16 rounded-full" />
            </div>
            <div className="flex items-center gap-2">
              <Skeleton className="h-8 w-32" />
              <Skeleton className="h-8 w-24" />
              <Skeleton className="h-8 w-16" />
            </div>
          </div>
        </div>
        {/* Stat card skeletons */}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
        {/* Chart skeleton */}
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (!account) {
    return <p className="text-muted-foreground">Account not found</p>;
  }

  const totalSent = stats?.reduce((a, s) => a + s.connection_requests_sent, 0) ?? 0;
  const totalAccepted = stats?.reduce((a, s) => a + s.connections_accepted, 0) ?? 0;
  const totalErrors = stats?.reduce((a, s) => a + s.errors, 0) ?? 0;
  const acceptRate = totalSent > 0 ? Math.round((totalAccepted / totalSent) * 100) : 0;

  function handleSaveSettings() {
    const data: Record<string, unknown> = {};
    if (editName !== account!.name) data.name = editName;
    if (editTimezone !== (account!.timezone ?? "")) data.timezone = editTimezone;
    if (Number(editDailyLimit) !== account!.daily_limit) data.daily_limit = Number(editDailyLimit);
    if (Number(editDailyMessageLimit) !== (account!.daily_message_limit ?? 15)) data.daily_message_limit = Number(editDailyMessageLimit);
    if (Number(editWeeklyLimit) !== account!.weekly_limit) data.weekly_limit = Number(editWeeklyLimit);
    const newThreshold = editWithdrawThreshold ? Number(editWithdrawThreshold) : null;
    if (newThreshold !== (account!.withdraw_threshold ?? null)) data.withdraw_threshold = newThreshold;
    const newInterval = editAutoWithdrawInterval ? Number(editAutoWithdrawInterval) : 30;
    if (newInterval !== (account!.auto_withdraw_interval_days ?? 30)) data.auto_withdraw_interval_days = newInterval;
    const newCountry = proxyForm.country.trim() || null;
    if (newCountry !== (account!.proxy_country ?? null)) data.proxy_country = newCountry;

    const newHost = proxyForm.host.trim() || null;
    const newPort = proxyForm.port ? Number(proxyForm.port) : null;
    const newUsername = proxyForm.username.trim() || null;
    if (newHost !== (account!.proxy_host ?? null)) data.proxy_host = newHost;
    if (newPort !== (account!.proxy_port ?? null)) data.proxy_port = newPort;
    if (newUsername !== (account!.proxy_username ?? null)) data.proxy_username = newUsername;
    // Only send password when user explicitly rotated it (Change clicked)
    // or when this is a fresh entry (no stored password).
    if (passwordEditing || !account!.proxy_password_set) {
      data.proxy_password = proxyForm.password || null;
    }
    if (Object.keys(data).length === 0) {
      toast.info("No changes to save");
      return;
    }
    const needsReplan = "daily_limit" in data || "weekly_limit" in data || "timezone" in data;
    updateAccount.mutate(data as Parameters<typeof updateAccount.mutate>[0], {
      onSuccess: () => {
        if (needsReplan) {
          toast.promise(replanAccount(id), {
            loading: "Regenerating today's plan…",
            success: (res) => `Settings saved · ${res.scheduled} leads rescheduled`,
            error: "Settings saved, but replanning failed",
          });
        } else {
          toast.success("Account settings saved");
        }
      },
      onError: (err) => toast.error(err.message),
    });
  }

  return (
    <div>
      {/* Sticky header */}
      <div className="sticky top-0 z-20 bg-background border-b border-border -mx-6 mb-6">
        <div className="flex items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-9 w-9 shrink-0 overflow-hidden rounded-full bg-muted">
              <img
                src={getAvatarUrl(id)}
                alt=""
                className="h-full w-full object-cover"
                onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
              />
            </div>
            <h1 className="text-lg font-semibold truncate">{account.name}</h1>
            <StatusBadge status={account.status} />
            {campaigns && campaigns.length > 0 && (
              <div className="hidden sm:flex items-center gap-2">
                {campaigns.map((c) => (
                  <Link
                    key={c.id}
                    href={`/campaigns/${c.id}`}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                    </svg>
                    {c.name}
                  </Link>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {!browseSessionActive ? (
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={async () => {
                  const win = window.open("about:blank", "_blank");
                  setBrowseLoading(true);
                  try {
                    const res = await startBrowseSession(id);
                    setBrowseSessionActive(true);
                    const novncBase =
                      process.env.NEXT_PUBLIC_NOVNC_URL ||
                      `http://${window.location.hostname}:6080`;
                    const title = encodeURIComponent(`LinkedIn — ${account.name}`);
                    const url = `${novncBase}${res.novnc_url}&title=${title}`;
                    if (win) {
                      win.location.href = url;
                    } else {
                      window.open(url, "_blank");
                    }
                  } catch (err: unknown) {
                    win?.close();
                    toast.error(err instanceof Error ? err.message : "Failed to start session");
                  } finally {
                    setBrowseLoading(false);
                  }
                }}
                disabled={browseLoading || loginSessionActive}
              >
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
                </svg>
                {browseLoading ? "Opening…" : "Launch LinkedIn"}
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 border-warning text-warning hover:bg-warning-soft/70"
                onClick={async () => {
                  try { await closeBrowseSession(id); } catch { /* ignore */ }
                  setBrowseSessionActive(false);
                }}
              >
                <span className="h-2 w-2 rounded-full bg-warning animate-pulse" />
                Close LinkedIn Session
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={handleSaveSettings}
              disabled={updateAccount.isPending}
            >
              Save Settings
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground hover:text-destructive"
              onClick={() => {
                if (confirm("Archive this account? It will be paused and hidden from the main view.")) {
                  archiveAccount.mutate(id, {
                    onSuccess: () => {
                      toast.success("Account archived");
                      router.push("/accounts");
                    },
                    onError: (err) => toast.error(err.message),
                  });
                }
              }}
              disabled={archiveAccount.isPending}
            >
              Archive
            </Button>
          </div>
        </div>

        {/* Persistent banner — shown while a browse session is active */}
        {browseSessionActive && (
          <div className="flex items-center justify-between gap-4 border-t border-warning-line bg-warning-soft/70 px-6 py-2">
            <div className="flex items-center gap-2 text-sm text-warning">
              <span className="h-2 w-2 rounded-full bg-warning animate-pulse shrink-0" />
              LinkedIn session active — close the tab or click End Session when done
            </div>
            <button
              className="shrink-0 text-xs font-medium text-warning hover:text-warning transition-colors"
              onClick={async () => {
                try { await closeBrowseSession(id); } catch { /* ignore */ }
                setBrowseSessionActive(false);
              }}
            >
              End Session
            </button>
          </div>
        )}
      </div>

      <div className="space-y-6">
        {account.status === "cookie_expired" && (
          <div className="rounded-md border border-danger-line bg-danger-soft/70 px-4 py-3">
            <p className="text-sm text-danger">
              Login required — use the <span className="font-medium">Manual Login</span> button in the Settings tab to authenticate with LinkedIn.
            </p>
          </div>
        )}

        {account.paused_until && new Date(account.paused_until) > new Date() && (
          <div className="rounded-md border border-warning-line bg-warning-soft/70 px-4 py-3">
            <p className="text-sm text-warning">
              Account paused due to weekly limit — resumes{" "}
              {new Date(account.paused_until.endsWith("Z") ? account.paused_until : account.paused_until + "Z").toLocaleString(undefined, {
                timeZone: account.timezone ?? undefined,
                weekday: "short",
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </p>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
          <StatCard label="Sent (30d)" value={totalSent} />
          <StatCard label="Accepted" value={totalAccepted} badge={totalSent > 0 ? `${acceptRate}%` : undefined} badgeColor="green" />
          <StatCard label="Errors" value={totalErrors} />
          <StatCard label="Pending Requests" value={account.pending_requests ?? 0} sub={account.pending_requests != null && account.pending_requests > 1000 ? "Over 1K — risk of limits" : undefined} />
          <StatCard label="Daily Target" value={account.daily_limit} />
        </div>

        <Tabs defaultValue={account.status === "cookie_expired" ? "settings" : "stats"}>
          <TabsList>
            <TabsTrigger value="stats">Stats</TabsTrigger>
            <TabsTrigger value="schedule">Schedule</TabsTrigger>
            <TabsTrigger value="health">Health</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>

          <TabsContent value="stats" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle>Daily Activity</CardTitle>
              </CardHeader>
              <CardContent>
                <DailyChart accountId={id} />
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="schedule" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle>
                  Today&apos;s Schedule
                  {schedule && (
                    <span className="ml-2 text-sm font-normal text-muted-foreground">
                      {schedule.filter((s) => s.status === "sent").length} of {schedule.length} executed
                    </span>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {!schedule || schedule.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-4">No leads scheduled for today.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border text-left text-xs text-muted-foreground">
                          <th className="pb-2 font-medium">Time</th>
                          <th className="pb-2 font-medium">Lead</th>
                          <th className="pb-2 font-medium">Campaign</th>
                          <th className="pb-2 font-medium text-right">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {schedule.map((slot) => (
                          <tr key={slot.lead_id} className="border-b border-border/50 last:border-0">
                            <td className="py-2 text-muted-foreground tabular-nums">
                              {slot.scheduled_at
                                ? new Date(slot.scheduled_at.endsWith("Z") ? slot.scheduled_at : slot.scheduled_at + "Z").toLocaleTimeString(undefined, {
                                    timeZone: account.timezone ?? undefined,
                                    hour: "2-digit",
                                    minute: "2-digit",
                                  })
                                : "—"}
                            </td>
                            <td className="py-2">
                              {slot.linkedin_url ? (
                                <a
                                  href={slot.linkedin_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="font-medium hover:underline"
                                >
                                  {[slot.first_name, slot.last_name].filter(Boolean).join(" ") || "Unknown"}
                                </a>
                              ) : (
                                <span className="font-medium">
                                  {[slot.first_name, slot.last_name].filter(Boolean).join(" ") || "Unknown"}
                                </span>
                              )}
                            </td>
                            <td className="py-2 text-muted-foreground">{slot.campaign_name}</td>
                            <td className="py-2 text-right">
                              <StatusBadge status={slot.status === "sent" ? "connection_requested" : "scheduled"} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="health" className="mt-4 space-y-4">
            <LocalTimeCard
              timezone={account.timezone}
              workStartHour={account.work_start_hour}
              workEndHour={account.work_end_hour}
            />
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <Card>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">Cookie Status</p>
                  <div className="mt-1">
                    <StatusBadge status={account.status} />
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">Last Activity</p>
                  <p className={`mt-1 text-lg font-semibold ${health?.days_since_last_activity != null && health.days_since_last_activity > 1 ? "text-warning" : ""}`}>
                    {health?.last_action_at
                      ? health.days_since_last_activity === 0
                        ? "Today"
                        : health.days_since_last_activity === 1
                        ? "Yesterday"
                        : `${health.days_since_last_activity}d ago`
                      : "Never"}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">Error Rate (7d)</p>
                  <p className={`mt-1 text-lg font-semibold ${
                    (health?.error_rate_7d ?? 0) > 25 ? "text-danger" :
                    (health?.error_rate_7d ?? 0) > 10 ? "text-warning" : ""
                  }`}>
                    {health ? `${health.error_rate_7d}%` : "—"}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {health ? `${health.errors_7d} of ${health.total_actions_7d} actions` : ""}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">Proxy</p>
                  <p className="mt-1 text-lg font-semibold">
                    {account.proxy_country?.toUpperCase() || "None"}
                  </p>
                </CardContent>
              </Card>
            </div>
            {health?.last_error_message && (
              <Card className="mt-4">
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground mb-1">Last Error</p>
                  <p className="text-sm text-danger">{health.last_error_message}</p>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <ActivityTimeline logs={activity ?? []} timezone={account.timezone} />
          </TabsContent>

          <TabsContent value="settings" className="mt-4 space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Account Settings</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 max-w-lg">
                <div>
                  <label className="text-xs text-muted-foreground">Name</label>
                  <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Timezone</label>
                  <select
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    value={editTimezone}
                    onChange={(e) => setEditTimezone(e.target.value)}
                  >
                    <option value="America/New_York">US — EST (New York)</option>
                    <option value="Europe/London">UK — GMT (London)</option>
                    <option value="Europe/Berlin">Europe — CET (Berlin)</option>
                    <option value="Europe/Rome">Italy — CET (Rome)</option>
                    <option value="Europe/Athens">Greece — EET (Athens)</option>
                    <option value="Europe/Paris">France — CET (Paris)</option>
                    <option value="Europe/Madrid">Spain — CET (Madrid)</option>
                    <option value="Asia/Dubai">UAE — GST (Dubai)</option>
                    <option value="Asia/Singapore">Asia — SGT (Singapore)</option>
                    <option value="America/Toronto">Canada — EST (Toronto)</option>
                    <option value="America/Vancouver">Canada — PST (Vancouver)</option>
                  </select>
                </div>
                <div className="space-y-3">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground/70 pt-1">
                    Daily sending limits
                  </div>
                  <div className="grid grid-cols-3 gap-4">
                    <div>
                      <label className="text-xs text-muted-foreground">Connection requests</label>
                      <Input
                        type="number"
                        min={0}
                        value={editDailyLimit}
                        onChange={(e) => setEditDailyLimit(e.target.value)}
                      />
                      <p className="mt-1 text-[10px] text-muted-foreground/70">per day</p>
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Messages</label>
                      <Input
                        type="number"
                        min={0}
                        value={editDailyMessageLimit}
                        onChange={(e) => setEditDailyMessageLimit(e.target.value)}
                      />
                      <p className="mt-1 text-[10px] text-muted-foreground/70">
                        distinct leads / day (broadcasts + follow-ups)
                      </p>
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Weekly cap</label>
                      <Input
                        type="number"
                        min={0}
                        value={editWeeklyLimit}
                        onChange={(e) => setEditWeeklyLimit(e.target.value)}
                      />
                      <p className="mt-1 text-[10px] text-muted-foreground/70">connections / week</p>
                    </div>
                  </div>
                </div>
                <ProxySettings
                  value={proxyForm}
                  onChange={setProxyForm}
                  hasStoredPassword={account.proxy_password_set}
                  passwordEditing={passwordEditing}
                  onPasswordEditingChange={setPasswordEditing}
                  accountId={id}
                />
                <p className="text-xs text-muted-foreground">
                  Created {new Date(account.created_at.endsWith("Z") ? account.created_at : account.created_at + "Z").toLocaleDateString(undefined, { timeZone: account.timezone ?? undefined })}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Stuck Leads</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 max-w-lg">
                <p className="text-sm text-muted-foreground">
                  Leads that errored due to profile detection failures or proxy timeouts
                  before the Oct 4 connection reliability fix. Safe to requeue — no invite was sent.
                </p>
                <div className="flex items-center gap-3">
                  {stuckLeadsCount === null ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={stuckLeadsLoading}
                      onClick={async () => {
                        setStuckLeadsLoading(true);
                        try {
                          const res = await recoverStuckLeads(id, true);
                          setStuckLeadsCount(res.count);
                        } catch (err: unknown) {
                          toast.error(err instanceof Error ? err.message : "Failed to fetch count");
                        } finally {
                          setStuckLeadsLoading(false);
                        }
                      }}
                    >
                      {stuckLeadsLoading ? "Checking…" : "Check for stuck leads"}
                    </Button>
                  ) : stuckLeadsRecovered !== null ? (
                    <p className="text-sm text-success">
                      ✓ Requeued {stuckLeadsRecovered} leads back to pending
                    </p>
                  ) : stuckLeadsCount === 0 ? (
                    <p className="text-sm text-muted-foreground">No stuck leads found.</p>
                  ) : (
                    <>
                      <p className="text-sm">
                        <span className="font-semibold tabular-nums">{stuckLeadsCount}</span> recoverable leads found
                      </p>
                      <Button
                        size="sm"
                        disabled={stuckLeadsLoading}
                        onClick={async () => {
                          if (!confirm(`Requeue ${stuckLeadsCount} stuck leads back to pending?`)) return;
                          setStuckLeadsLoading(true);
                          try {
                            const res = await recoverStuckLeads(id, false);
                            setStuckLeadsRecovered(res.recovered);
                            toast.success(`Requeued ${res.recovered} leads`);
                          } catch (err: unknown) {
                            toast.error(err instanceof Error ? err.message : "Failed to recover leads");
                          } finally {
                            setStuckLeadsLoading(false);
                          }
                        }}
                      >
                        {stuckLeadsLoading ? "Recovering…" : `Recover ${stuckLeadsCount} leads`}
                      </Button>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Pending Connection Requests</CardTitle>
              </CardHeader>
              <CardContent className="space-y-5 max-w-lg">

                {/* Two counts side by side */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-md border border-border bg-muted/30 px-4 py-3">
                    <p className="text-xs text-muted-foreground mb-1">Sent via Releasi</p>
                    <p className="text-2xl font-semibold tabular-nums">
                      {account.pending_requests ?? 0}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">awaiting acceptance</p>
                  </div>
                  <div className="rounded-md border border-border bg-muted/30 px-4 py-3">
                    <p className="text-xs text-muted-foreground mb-1">Total on LinkedIn</p>
                    {invCount !== null ? (
                      <>
                        <p className="text-2xl font-semibold tabular-nums">{invCount.toLocaleString()}</p>
                        <button
                          className="text-xs text-muted-foreground hover:text-foreground mt-0.5 underline-offset-2 hover:underline"
                          onClick={async () => {
                            setInvCountLoading(true);
                            try {
                              const res = await fetchInvitationCount(id);
                              setInvCount(res.count);
                            } catch (err: unknown) {
                              toast.error(err instanceof Error ? err.message : "Failed to fetch count");
                            } finally {
                              setInvCountLoading(false);
                            }
                          }}
                          disabled={invCountLoading || withdrawStatus?.status === "running"}
                        >
                          {invCountLoading ? "Refreshing…" : "Refresh"}
                        </button>
                      </>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-1"
                        onClick={async () => {
                          setInvCountLoading(true);
                          try {
                            const res = await fetchInvitationCount(id);
                            setInvCount(res.count);
                          } catch (err: unknown) {
                            toast.error(err instanceof Error ? err.message : "Failed to fetch count");
                          } finally {
                            setInvCountLoading(false);
                          }
                        }}
                        disabled={invCountLoading || withdrawStatus?.status === "running"}
                      >
                        {invCountLoading ? (
                          <span className="flex items-center gap-2">
                            <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            Fetching…
                          </span>
                        ) : "Fetch live count"}
                      </Button>
                    )}
                  </div>
                </div>

                {/* Scheduled auto-withdraw */}
                <div className="space-y-3">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Scheduled Auto-Withdraw</p>
                  <div className="flex items-end gap-3">
                    <div className="flex-1">
                      <label className="text-xs text-muted-foreground">Threshold</label>
                      <Input
                        type="number"
                        value={editWithdrawThreshold}
                        onChange={(e) => setEditWithdrawThreshold(e.target.value)}
                        placeholder="e.g. 2000 — leave empty to disable"
                        className="mt-1"
                      />
                    </div>
                    <div className="w-28">
                      <label className="text-xs text-muted-foreground">Interval (days)</label>
                      <Input
                        type="number"
                        min={1}
                        value={editAutoWithdrawInterval}
                        onChange={(e) => setEditAutoWithdrawInterval(e.target.value)}
                        placeholder="30"
                        className="mt-1"
                      />
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    When the live count exceeds the threshold, the scheduler withdraws oldest requests down to a random target within 5% of the threshold (capped at 200/session). Set threshold to enable; interval controls how often it may run.
                  </p>
                  {account.auto_withdraw_last_run && (
                    <p className="text-xs text-muted-foreground">
                      Last run: {new Date(account.auto_withdraw_last_run).toLocaleString()}
                      {account.pending_invitations_count != null && ` · count at run: ${account.pending_invitations_count.toLocaleString()}`}
                    </p>
                  )}
                </div>

                {/* Manual withdraw — only shown once live count is known */}
                {invCount !== null && (
                  <div className="space-y-3 border-t border-border pt-4">
                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Withdraw a batch now</p>
                    <div className="flex items-end gap-3">
                      <div>
                        <label className="text-xs text-muted-foreground">Count</label>
                        <Input
                          type="number"
                          min={1}
                          max={50}
                          value={withdrawCount}
                          onChange={(e) => setWithdrawCount(e.target.value)}
                          className="w-24 mt-1"
                          disabled={withdrawStatus?.status === "running"}
                        />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">Order</label>
                        <select
                          className="mt-1 w-28 rounded-md border border-border bg-background px-3 py-2 text-sm"
                          value={withdrawOrder}
                          onChange={(e) => setWithdrawOrder(e.target.value as "oldest" | "newest")}
                          disabled={withdrawStatus?.status === "running"}
                        >
                          <option value="oldest">Oldest first</option>
                          <option value="newest">Newest first</option>
                        </select>
                      </div>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={withdrawStatus?.status === "running"}
                        onClick={async () => {
                          const n = parseInt(withdrawCount, 10);
                          if (!n || n < 1 || n > 50) {
                            toast.error("Enter a number between 1 and 50");
                            return;
                          }
                          if (!confirm(`Withdraw ${n} ${withdrawOrder === "oldest" ? "oldest" : "newest"} pending connection requests?`)) return;
                          setWithdrawStatus(null);
                          setWithdrawTaskId(null);
                          try {
                            const res = await startWithdrawal(id, n, withdrawOrder);
                            setWithdrawTaskId(res.task_id);
                            setWithdrawStatus({ status: "running", withdrawn: [], db_updated: 0, error: null });
                            const poll = async () => {
                              try {
                                const s = await pollWithdrawalStatus(id, res.task_id);
                                setWithdrawStatus(s);
                                if (s.status === "running") setTimeout(poll, 3000);
                                else if (s.status === "done") {
                                  setInvCount(prev => prev !== null ? Math.max(0, prev - s.withdrawn.length) : null);
                                  toast.success(`Withdrew ${s.withdrawn.length} requests · ${s.db_updated} leads updated`);
                                } else {
                                  toast.error(`Withdrawal failed: ${s.error}`);
                                }
                              } catch { setTimeout(poll, 3000); }
                            };
                            setTimeout(poll, 3000);
                          } catch (err: unknown) {
                            toast.error(err instanceof Error ? err.message : "Failed to start withdrawal");
                          }
                        }}
                      >
                        {withdrawStatus?.status === "running" ? (
                          <span className="flex items-center gap-2">
                            <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            Withdrawing…
                          </span>
                        ) : "Withdraw"}
                      </Button>
                    </div>

                    {withdrawStatus?.status === "done" && (
                      <div className="rounded-md border border-success-line bg-success-soft/70 px-4 py-3">
                        <p className="text-sm font-medium text-success">
                          Withdrew {withdrawStatus.withdrawn.length} requests
                          {withdrawStatus.db_updated > 0 && ` · ${withdrawStatus.db_updated} leads marked withdrawn`}
                        </p>
                      </div>
                    )}
                    {withdrawStatus?.status === "error" && (
                      <div className="rounded-md border border-danger-line bg-danger-soft/70 px-4 py-3">
                        <p className="text-sm text-danger">{withdrawStatus.error}</p>
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>LinkedIn Authentication</CardTitle>
              </CardHeader>
              <CardContent className="space-y-5 max-w-lg">

                {/* Primary: browser login */}
                {!loginSessionActive ? (
                  <div className="space-y-2">
                    <p className="text-sm text-muted-foreground">
                      Open a browser session on the server and log in to LinkedIn normally.
                      Your cookies are saved automatically.
                    </p>
                    <Button
                      className="w-full"
                      onClick={async () => {
                        const loginWindow = window.open("about:blank", "_blank");
                        setLoginLoading(true);
                        try {
                          const res = await startLoginSession(id);
                          setLoginSessionActive(true);
                          const novncBase =
                            process.env.NEXT_PUBLIC_NOVNC_URL ||
                            `http://${window.location.hostname}:6080`;
                          const title = encodeURIComponent(`LinkedIn Login — ${account.name}`);
                          const url = `${novncBase}${res.novnc_url}&title=${title}`;
                          if (loginWindow) {
                            loginWindow.location.href = url;
                          } else {
                            window.open(url, "_blank");
                          }
                        } catch (err: unknown) {
                          loginWindow?.close();
                          toast.error(err instanceof Error ? err.message : "Failed to start session");
                        } finally {
                          setLoginLoading(false);
                        }
                      }}
                      disabled={loginLoading}
                    >
                      {loginLoading ? "Starting..." : "Open Login Browser"}
                    </Button>
                  </div>
                ) : (
                  <div className="rounded-md border border-success-line bg-success-soft/70 p-4 space-y-3">
                    <p className="text-sm font-medium text-success">
                      Browser session active — complete the login in the new tab, then click Finish.
                    </p>
                    <div className="flex gap-2">
                      <Button
                        onClick={async () => {
                          setLoginLoading(true);
                          try {
                            const res = await finishLoginSession(id);
                            if (res.success) {
                              toast.success(res.message);
                            } else {
                              toast.error(res.message);
                            }
                          } catch (err: unknown) {
                            toast.error(err instanceof Error ? err.message : "Failed to finish session");
                          } finally {
                            setLoginSessionActive(false);
                            setLoginLoading(false);
                          }
                        }}
                        disabled={loginLoading}
                      >
                        {loginLoading ? "Saving..." : "Finish & Save Cookies"}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={async () => {
                          try { await cancelLoginSession(id); } catch { /* ignore */ }
                          setLoginSessionActive(false);
                        }}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}

                {/* Advanced: paste cookie */}
                <details className="group">
                  <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground list-none flex items-center gap-1 select-none">
                    <svg className="h-3 w-3 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                    Advanced: paste li_at cookie manually
                  </summary>
                  <div className="mt-3 space-y-2">
                    <p className="text-xs text-muted-foreground">
                      Extract your <code className="rounded bg-muted px-1 py-0.5">li_at</code> cookie from your browser&apos;s DevTools (Application → Cookies → linkedin.com).
                    </p>
                    <div className="flex gap-2">
                      <Input
                        value={newCookie}
                        onChange={(e) => setNewCookie(e.target.value)}
                        placeholder="Paste li_at cookie value"
                        type="password"
                      />
                      <Button
                        variant="outline"
                        onClick={() => {
                          if (!newCookie) return;
                          updateCookie.mutate(
                            { li_at_cookie: newCookie },
                            {
                              onSuccess: () => {
                                toast.success("Cookie saved");
                                setNewCookie("");
                              },
                              onError: (err) => toast.error(err.message),
                            }
                          );
                        }}
                        disabled={updateCookie.isPending}
                      >
                        Save
                      </Button>
                    </div>
                  </div>
                </details>

                {/* Verify section */}
                <div className="border-t border-border pt-4 space-y-3">
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Verify connection</p>

                  {connectionChecking && (
                    <div className="flex items-center gap-3 text-sm text-muted-foreground">
                      <svg className="h-4 w-4 shrink-0 animate-spin text-info" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Checking session via HTTP...
                      <div className="ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-muted">
                        <div className="h-full w-full origin-left animate-pulse rounded-full bg-info" />
                      </div>
                    </div>
                  )}

                  {!connectionChecking && connectionResult && (
                    <div className={`rounded-md border px-4 py-3 ${
                      connectionResult.valid
                        ? "border-success-line bg-success-soft/70"
                        : "border-danger-line bg-danger-soft/70"
                    }`}>
                      <p className={`text-sm font-medium ${connectionResult.valid ? "text-success" : "text-danger"}`}>
                        {connectionResult.valid
                          ? `Session valid (${connectionResult.elapsed_ms}ms)`
                          : connectionResult.reason === "redirected_to_login"
                          ? "Session expired — log in again or paste a fresh cookie"
                          : connectionResult.reason === "proxy_unreachable"
                          ? "Proxy unreachable — LinkedIn could not be reached via your proxy"
                          : connectionResult.reason === "no_cookie"
                          ? "No cookie saved — log in first"
                          : `Failed: ${connectionResult.error || connectionResult.reason}`}
                      </p>
                      {!connectionResult.valid && connectionResult.elapsed_ms != null && (
                        <p className="text-xs text-muted-foreground mt-0.5">{connectionResult.elapsed_ms}ms</p>
                      )}
                    </div>
                  )}

                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      setConnectionChecking(true);
                      setConnectionResult(null);
                      try {
                        const result = await checkConnection(id);
                        setConnectionResult(result);
                      } catch (err: unknown) {
                        setConnectionResult({ valid: false, error: err instanceof Error ? err.message : "Check failed" });
                      } finally {
                        setConnectionChecking(false);
                      }
                    }}
                    disabled={connectionChecking}
                  >
                    {connectionChecking ? "Checking..." : connectionResult ? "Check Again" : "Check Connection"}
                  </Button>
                </div>

              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
