"use client";

import { useState, useMemo } from "react";
import type { ActionLog } from "@/lib/types";
import { StatusBadge } from "@/components/status-badge";

function formatTime(iso: string, tz?: string | null) {
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleString("en-US", {
    timeZone: tz ?? undefined,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function localDateKey(iso: string, tz?: string | null): string {
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleDateString("en-US", {
    timeZone: tz ?? undefined,
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function formatSkipReason(reason: string | undefined): string {
  if (!reason) return "";
  if (reason === "already_connected") return "Already connected";
  if (reason === "filter_connections_unknown") return "Connection count unreadable";
  if (reason === "profile_not_found") return "Profile not found";
  if (reason === "pending_request") return "Pending request already sent";
  if (reason.startsWith("filter_low_connections:")) {
    const count = reason.split(":")[1];
    return `Too few connections (${count})`;
  }
  if (reason.startsWith("filter_")) return reason.replace("filter_", "").replace(/_/g, " ");
  return reason;
}

function formatSkipReasonAggregate(reason: string | undefined): string {
  if (reason?.startsWith("filter_low_connections:")) return "Too few connections";
  return formatSkipReason(reason);
}

function formatActionType(type: string) {
  return type
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const TYPE_GROUPS: Record<string, string> = {
  CONNECTION_REQUEST: "Connection Requests",
  FOLLOWUP_MESSAGE: "Follow-up Messages",
  ACCEPTANCE_CHECK_SUMMARY: "Acceptance Checks",
  INVITATION_WITHDRAWN: "Withdrawals",
  DAILY_PLAN_GENERATED: "Daily Plans",
  LIMIT_DETECTED: "Limit Detected",
  COOLDOWN_STARTED: "Cooldown Started",
  COOLDOWN_ENDED: "Cooldown Ended",
  COOLDOWN_RETRY: "Cooldown Retry",
  FEED_VIEW: "Keepalive (Feed View)",
  POST_LIKE: "Keepalive (Post Like)",
  PROFILE_VIEW: "Keepalive (Profile View)",
  FUNDRAISING_IMPORT: "Fundraising Import",
  ERROR: "Errors",
};

// Types that have meaningful lead associations
const LEAD_LINKED_TYPES = new Set([
  "CONNECTION_REQUEST",
  "FOLLOWUP_MESSAGE",
  "INVITATION_WITHDRAWN",
]);

interface DaySummary {
  sent: number;
  skipped: number;
  failed: number;
  messages: number;
  accepted: number;
}

function computeDaySummary(dayLogs: ActionLog[]): DaySummary {
  const summary: DaySummary = { sent: 0, skipped: 0, failed: 0, messages: 0, accepted: 0 };
  for (const l of dayLogs) {
    const type = l.action_type.toUpperCase();
    const status = l.status.toUpperCase();
    if (type === "CONNECTION_REQUEST") {
      if (status === "SUCCESS") summary.sent++;
      else if (status === "SKIPPED") summary.skipped++;
      else if (status === "FAILED") summary.failed++;
    } else if (type === "FOLLOWUP_MESSAGE" && status === "SUCCESS") {
      summary.messages++;
    } else if (type === "ACCEPTANCE_CHECK_SUMMARY") {
      const d = (l.details ?? {}) as Record<string, unknown>;
      summary.accepted += (d.accepted as number) ?? 0;
    }
  }
  return summary;
}

function LeadLink({ log }: { log: ActionLog }) {
  const name =
    log.lead_first_name || log.lead_last_name
      ? [log.lead_first_name, log.lead_last_name].filter(Boolean).join(" ")
      : null;
  if (!name) return null;
  return (
    <>
      <span className="text-muted-foreground">·</span>
      {log.lead_url ? (
        <a
          href={log.lead_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-info hover:underline truncate"
        >
          {name}
        </a>
      ) : (
        <span className="text-muted-foreground truncate">{name}</span>
      )}
    </>
  );
}

function ActivityEntry({ log, timezone }: { log: ActionLog; timezone?: string | null }) {
  const type = log.action_type.toUpperCase();
  const details = (log.details ?? {}) as Record<string, unknown>;

  // ACCEPTANCE_CHECK_SUMMARY
  if (type === "ACCEPTANCE_CHECK_SUMMARY") {
    const accepted = (details.accepted as number) ?? 0;
    const scanned = (details.scanned as number) ?? null;
    const hitCutoff = details.hit_cutoff as boolean | undefined;
    return (
      <div className="flex items-start gap-3 rounded-md border border-border p-3 text-sm">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium">Check Acceptance</span>
            <StatusBadge status={log.status} />
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {accepted} connection{accepted !== 1 ? "s" : ""} accepted
            {scanned !== null ? ` · ${scanned} scanned` : ""}
            {hitCutoff === false ? " · reached end of window" : ""}
          </p>
        </div>
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          {formatTime(log.created_at, timezone)}
        </span>
      </div>
    );
  }

  // FOLLOWUP_MESSAGE
  if (type === "FOLLOWUP_MESSAGE") {
    const idx = details.message_index as number | undefined;
    const total = details.total as number | undefined;
    const reason = details.reason as string | undefined;
    return (
      <div className="flex items-start gap-3 rounded-md border border-border p-3 text-sm">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium">Follow-up Message</span>
            {idx != null && <span className="text-xs text-muted-foreground">Msg {idx}{total != null ? `/${total}` : ""}</span>}
            <LeadLink log={log} />
            <StatusBadge status={log.status} />
          </div>
          {reason && (
            <p className="text-xs text-muted-foreground mt-1">{reason}</p>
          )}
        </div>
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          {formatTime(log.created_at, timezone)}
        </span>
      </div>
    );
  }

  // CONNECTION_REQUEST
  if (type === "CONNECTION_REQUEST") {
    const reason = details.reason as string | undefined;
    const skipped = log.status.toUpperCase() === "SKIPPED";
    return (
      <div className="flex items-start gap-3 rounded-md border border-border p-3 text-sm">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium">Connection Request</span>
            <LeadLink log={log} />
            <StatusBadge status={log.status} />
          </div>
          {skipped && reason && (
            <p className="text-xs text-muted-foreground mt-1">{formatSkipReason(reason)}</p>
          )}
        </div>
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          {formatTime(log.created_at, timezone)}
        </span>
      </div>
    );
  }

  // Any other type with optional lead link
  const hasLeadLink = LEAD_LINKED_TYPES.has(type);
  return (
    <div className="flex items-start gap-3 rounded-md border border-border p-3 text-sm">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium">{formatActionType(log.action_type)}</span>
          {hasLeadLink && <LeadLink log={log} />}
          <StatusBadge status={log.status} />
        </div>
        {log.details && (
          <p className="text-xs text-muted-foreground mt-1 truncate">
            {JSON.stringify(log.details)}
          </p>
        )}
      </div>
      <span className="text-xs text-muted-foreground whitespace-nowrap">
        {formatTime(log.created_at, timezone)}
      </span>
    </div>
  );
}

export function ActivityTimeline({ logs, timezone }: { logs: ActionLog[]; timezone?: string | null }) {
  const [filterType, setFilterType] = useState("ALL");
  const [filterStatus, setFilterStatus] = useState("ALL");

  // Strip legacy per-lead CHECK_ACCEPTANCE — superseded by ACCEPTANCE_CHECK_SUMMARY
  const baseLogs = useMemo(
    () => logs.filter((l) => l.action_type.toUpperCase() !== "CHECK_ACCEPTANCE"),
    [logs]
  );

  const presentTypes = useMemo(
    () =>
      Array.from(new Set(baseLogs.map((l) => l.action_type.toUpperCase()))).sort(),
    [baseLogs]
  );

  const displayLogs = useMemo(
    () =>
      baseLogs.filter((l) => {
        if (filterType !== "ALL" && l.action_type.toUpperCase() !== filterType) return false;
        if (filterStatus !== "ALL" && l.status.toUpperCase() !== filterStatus) return false;
        return true;
      }),
    [baseLogs, filterType, filterStatus]
  );

  // Skip reason breakdown — only when connection requests are in view
  const skipBreakdown = useMemo(() => {
    const showingConnReqs =
      filterType === "ALL" || filterType === "CONNECTION_REQUEST";
    const showingSkips =
      filterStatus === "ALL" || filterStatus === "SKIPPED";
    if (!showingConnReqs || !showingSkips) return null;

    const counts: Record<string, number> = {};
    for (const l of displayLogs) {
      if (l.action_type.toUpperCase() !== "CONNECTION_REQUEST") continue;
      if (l.status.toUpperCase() !== "SKIPPED") continue;
      const reason = ((l.details ?? {}) as Record<string, unknown>).reason as string | undefined;
      const label = formatSkipReasonAggregate(reason) || "Unknown";
      counts[label] = (counts[label] ?? 0) + 1;
    }
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    return entries.length > 0 ? entries : null;
  }, [displayLogs, filterType, filterStatus]);

  // Group displayed logs by local date for day separators
  const groupedByDay = useMemo(() => {
    const groups: { dateKey: string; logs: ActionLog[] }[] = [];
    let currentKey = "";
    for (const l of displayLogs) {
      const key = localDateKey(l.created_at, timezone);
      if (key !== currentKey) {
        groups.push({ dateKey: key, logs: [] });
        currentKey = key;
      }
      groups[groups.length - 1].logs.push(l);
    }
    return groups;
  }, [displayLogs, timezone]);

  if (baseLogs.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8 text-center">
        No activity yet
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {/* Filter controls */}
      <div className="flex gap-2 flex-wrap items-center">
        <select
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
        >
          <option value="ALL">All types</option>
          {presentTypes.map((type) => (
            <option key={type} value={type}>
              {TYPE_GROUPS[type] ?? formatActionType(type)}
            </option>
          ))}
        </select>
        <select
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="ALL">All statuses</option>
          <option value="SUCCESS">Success</option>
          <option value="FAILED">Failed</option>
          <option value="SKIPPED">Skipped</option>
        </select>
        {(filterType !== "ALL" || filterStatus !== "ALL") && (
          <button
            className="text-xs text-muted-foreground hover:text-foreground transition-colors px-1"
            onClick={() => { setFilterType("ALL"); setFilterStatus("ALL"); }}
          >
            Clear
          </button>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {displayLogs.length} of {baseLogs.length}
        </span>
      </div>

      {/* Skip reason breakdown */}
      {skipBreakdown && (
        <div className="rounded-md border border-border bg-muted/40 px-4 py-3 space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Skip reasons</p>
          <div className="flex flex-wrap gap-x-5 gap-y-1">
            {skipBreakdown.map(([label, count]) => (
              <span key={label} className="text-xs text-foreground">
                <span className="font-medium">{count}</span>
                <span className="text-muted-foreground ml-1">{label}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {displayLogs.length === 0 && (
        <p className="text-sm text-muted-foreground py-8 text-center">
          No matching activity
        </p>
      )}

      {/* Timeline grouped by day */}
      {groupedByDay.map(({ dateKey, logs: dayLogs }) => {
        const summary = computeDaySummary(dayLogs);
        const parts: string[] = [];
        if (summary.sent > 0) parts.push(`${summary.sent} sent`);
        if (summary.messages > 0) parts.push(`${summary.messages} msg`);
        if (summary.accepted > 0) parts.push(`${summary.accepted} connected`);
        if (summary.skipped > 0) parts.push(`${summary.skipped} skipped`);
        if (summary.failed > 0) parts.push(`${summary.failed} failed`);

        return (
          <div key={dateKey} className="space-y-2">
            {/* Day separator */}
            <div className="flex items-center gap-3 py-1">
              <span className="text-xs font-medium text-muted-foreground whitespace-nowrap">{dateKey}</span>
              {parts.length > 0 && (
                <span className="text-xs text-muted-foreground/60">{parts.join(" · ")}</span>
              )}
              <div className="flex-1 h-px bg-border" />
            </div>

            {dayLogs.map((log) => (
              <ActivityEntry key={log.id} log={log} timezone={timezone} />
            ))}
          </div>
        );
      })}
    </div>
  );
}
