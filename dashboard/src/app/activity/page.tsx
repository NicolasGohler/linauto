"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import {
  Send, MessageSquare, UserCheck, AlertCircle, Zap, Search,
  RefreshCw, ChevronLeft, ChevronRight, Filter, X,
  StickyNote, AtSign, CheckCircle2, UserX
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAccounts, useCampaigns } from "@/hooks/use-queries";
import { fetchActivity, fetchUsers } from "@/lib/api";
import type { ActivityItem, ActivityPage, DashboardUser } from "@/lib/types";

// ── Event type metadata ───────────────────────────────────────────────────

const EVENT_GROUPS: Record<string, { label: string; types: string[] }> = {
  linkedin: {
    label: "LinkedIn",
    types: [
      "connection_request", "followup_message", "check_acceptance",
      "acceptance_check_summary", "limit_detected", "cooldown_started",
      "cooldown_ended", "cooldown_retry", "feed_view", "post_like",
      "profile_view", "daily_plan_generated", "invitation_withdrawn",
    ],
  },
  enrichment: {
    label: "Enrichment",
    types: ["tg_sweep_searched", "telegram_found", "phone_enriched"],
  },
  manual: {
    label: "Manual",
    types: ["note_added", "tg_contacted", "tg_contacted_cleared",
            "telegram_saved", "telegram_removed"],
  },
  system: {
    label: "System",
    types: ["fundraising_import", "error"],
  },
};

type EventMeta = { label: string; Icon: React.ElementType; color: string };
const EVENT_META: Record<string, EventMeta> = {
  connection_request:       { label: "Connection request",    Icon: Send,          color: "text-info" },
  followup_message:         { label: "Follow-up",             Icon: MessageSquare,  color: "text-info" },
  acceptance_check_summary: { label: "Acceptance check",      Icon: UserCheck,      color: "text-success" },
  check_acceptance:         { label: "Acceptance check",      Icon: UserCheck,      color: "text-success" },
  limit_detected:           { label: "Limit detected",        Icon: AlertCircle,    color: "text-warning" },
  cooldown_started:         { label: "Cooldown started",      Icon: AlertCircle,    color: "text-warning" },
  cooldown_ended:           { label: "Cooldown ended",        Icon: RefreshCw,      color: "text-success" },
  tg_sweep_searched:        { label: "TG sweep",              Icon: Search,         color: "text-plum" },
  telegram_found:           { label: "Telegram found",        Icon: Zap,            color: "text-plum" },
  phone_enriched:           { label: "Phone enriched",        Icon: Zap,            color: "text-plum" },
  fundraising_import:       { label: "Fundraising import",    Icon: RefreshCw,      color: "text-info" },
  error:                    { label: "Error",                 Icon: AlertCircle,    color: "text-danger" },
  // Manual (human) actions
  note_added:               { label: "Note",                  Icon: StickyNote,     color: "text-warning" },
  tg_contacted:             { label: "Telegram outreach",     Icon: CheckCircle2,   color: "text-success" },
  tg_contacted_cleared:     { label: "Outreach cleared",      Icon: UserX,          color: "text-muted-foreground" },
  telegram_saved:           { label: "Telegram handle set",   Icon: AtSign,         color: "text-success" },
  telegram_removed:         { label: "Telegram handle removed", Icon: AtSign,       color: "text-muted-foreground" },
};

function getEventMeta(type: string): EventMeta {
  return EVENT_META[type] ?? { label: type, Icon: Zap, color: "text-muted-foreground" };
}

// ── Relative time ─────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso.endsWith("Z") ? iso : iso + "Z").getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60)  return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function absoluteTime(iso: string): string {
  return new Date(iso.endsWith("Z") ? iso : iso + "Z").toLocaleString();
}

// ── Status color ──────────────────────────────────────────────────────────

function statusColor(status?: string | null): string {
  if (!status) return "";
  if (status === "success") return "text-success";
  if (status === "failed")  return "text-danger";
  return "text-muted-foreground";
}

// ── Single activity row ───────────────────────────────────────────────────

function ActivityRow({ item }: { item: ActivityItem }) {
  const [expanded, setExpanded] = useState(false);
  const { label, Icon, color } = getEventMeta(item.event_type);
  const noteBody =
    item.event_type === "note_added" && item.details && typeof item.details.body === "string"
      ? (item.details.body as string)
      : null;

  return (
    <div
      className="group flex items-start gap-3 px-4 py-3 border-b border-border/50 hover:bg-muted/30 transition-colors cursor-pointer"
      onClick={() => setExpanded((v) => !v)}
    >
      <div className={`mt-0.5 shrink-0 ${color}`}>
        <Icon className="h-4 w-4" />
      </div>

      <div className="flex-1 min-w-0 space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">{label}</span>
          {item.status && (
            <span className={`text-xs ${statusColor(item.status)}`}>
              {item.status}
            </span>
          )}
          {item.lead_name && (
            item.lead_id ? (
              <Link
                href={`/leads/${item.lead_id}`}
                onClick={(e) => e.stopPropagation()}
                className="text-xs bg-muted px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground truncate max-w-[160px] transition-colors"
              >
                {item.lead_name}
              </Link>
            ) : (
              <span className="text-xs bg-muted px-1.5 py-0.5 rounded text-muted-foreground truncate max-w-[160px]">
                {item.lead_name}
              </span>
            )
          )}
          {item.account_name && (
            <span className="text-xs bg-info-soft/70 text-info px-1.5 py-0.5 rounded truncate max-w-[120px]">
              {item.account_name}
            </span>
          )}
          {item.actor_name && (
            <span className="text-xs bg-success-soft/70 text-success px-1.5 py-0.5 rounded truncate max-w-[120px]">
              by {item.actor_name}
            </span>
          )}
          {item.campaign_name && (
            <span className="text-xs bg-plum-soft/70 text-plum px-1.5 py-0.5 rounded truncate max-w-[120px]">
              {item.campaign_name}
            </span>
          )}
        </div>

        {noteBody && (
          <p className={`text-xs text-muted-foreground ${expanded ? "" : "line-clamp-2"}`}>
            {noteBody}
          </p>
        )}

        {expanded && item.details && !noteBody && (
          <pre className="text-xs text-muted-foreground bg-black/30 rounded p-2 overflow-x-auto whitespace-pre-wrap mt-1">
            {JSON.stringify(item.details, null, 2)}
          </pre>
        )}
      </div>

      <span
        className="text-xs text-muted-foreground shrink-0"
        title={absoluteTime(item.created_at)}
      >
        {relativeTime(item.created_at)}
      </span>
    </div>
  );
}

// ── Filters bar ───────────────────────────────────────────────────────────

const DATE_PRESETS = [
  { label: "Today",    hours: 24 },
  { label: "7 days",   hours: 168 },
  { label: "30 days",  hours: 720 },
];

type Filters = {
  datePreset: number | null;  // hours
  groups: string[];           // selected group keys
  account_id: string;
  campaign_id: string;
  user_id: string;
};

// ── Page ──────────────────────────────────────────────────────────────────

export default function ActivityPage() {
  const { data: accounts } = useAccounts();
  const { data: campaigns } = useCampaigns();
  const [users, setUsers] = useState<DashboardUser[]>([]);

  const [data, setData] = useState<ActivityPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Filters>({
    datePreset: 168,  // last 7 days
    groups: [],
    account_id: "",
    campaign_id: "",
    user_id: "",
  });

  useEffect(() => {
    fetchUsers().then(setUsers).catch(() => {});
  }, []);

  const fetchData = async (pg: number, f: Filters) => {
    setLoading(true);
    setError(null);
    try {
      const since = f.datePreset
        ? new Date(Date.now() - f.datePreset * 3600 * 1000).toISOString()
        : undefined;

      const selectedTypes = f.groups.length
        ? f.groups.flatMap((g) => EVENT_GROUPS[g]?.types ?? [])
        : undefined;

      const result = await fetchActivity({
        page: pg,
        per_page: 50,
        since,
        event_types: selectedTypes?.join(","),
        account_id: f.account_id || undefined,
        campaign_id: f.campaign_id || undefined,
        user_id: f.user_id || undefined,
      });
      setData(result);
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "Failed to load activity");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData(page, filters);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, filters]);

  const setFilter = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    setPage(1);
    setFilters((f) => ({ ...f, [key]: value }));
  };

  const toggleGroup = (group: string) => {
    setFilter(
      "groups",
      filters.groups.includes(group)
        ? filters.groups.filter((g) => g !== group)
        : [...filters.groups, group]
    );
  };

  const hasFilters = filters.groups.length > 0 || filters.account_id || filters.campaign_id || filters.user_id || filters.datePreset !== 168;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Activity</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Unified feed across LinkedIn actions, enrichments, and imports
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => fetchData(page, filters)}
          disabled={loading}
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Date presets */}
        <div className="flex items-center gap-1 rounded-md border border-border p-1">
          <Filter className="h-3.5 w-3.5 ml-1 text-muted-foreground" />
          {DATE_PRESETS.map((p) => (
            <button
              key={p.hours}
              onClick={() => setFilter("datePreset", p.hours)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                filters.datePreset === p.hours
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {p.label}
            </button>
          ))}
          <button
            onClick={() => setFilter("datePreset", null)}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
              filters.datePreset === null
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            All
          </button>
        </div>

        {/* Event groups */}
        {Object.entries(EVENT_GROUPS).map(([key, g]) => (
          <button
            key={key}
            onClick={() => toggleGroup(key)}
            className={`px-2.5 py-1.5 rounded-md border text-xs font-medium transition-colors ${
              filters.groups.includes(key)
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {g.label}
          </button>
        ))}

        {/* Account filter */}
        {accounts && accounts.length > 1 && (
          <select
            value={filters.account_id}
            onChange={(e) => setFilter("account_id", e.target.value)}
            className="h-8 rounded-md border border-border bg-background px-2 text-xs text-muted-foreground"
          >
            <option value="">All accounts</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        )}

        {/* Campaign filter */}
        {campaigns && campaigns.length > 0 && (
          <select
            value={filters.campaign_id}
            onChange={(e) => setFilter("campaign_id", e.target.value)}
            className="h-8 rounded-md border border-border bg-background px-2 text-xs text-muted-foreground"
          >
            <option value="">All campaigns</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        )}

        {/* Person filter (manual actions) */}
        {users.length > 0 && (
          <select
            value={filters.user_id}
            onChange={(e) => setFilter("user_id", e.target.value)}
            className="h-8 rounded-md border border-border bg-background px-2 text-xs text-muted-foreground"
            title="Filter manual actions by person"
          >
            <option value="">Anyone</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.display_name || u.handle}</option>
            ))}
          </select>
        )}

        {/* Clear filters */}
        {hasFilters && (
          <button
            onClick={() => { setPage(1); setFilters({ datePreset: 168, groups: [], account_id: "", campaign_id: "", user_id: "" }); }}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" /> Reset
          </button>
        )}
      </div>

      {/* Feed */}
      <div className="rounded-lg border border-border overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
            <RefreshCw className="h-4 w-4 animate-spin mr-2" /> Loading…
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-16 text-sm text-danger">
            <AlertCircle className="h-5 w-5 mb-2" />
            Failed to load activity: {error}
          </div>
        ) : !data || data.items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground">
            No activity found for the selected filters
          </div>
        ) : (
          <>
            {data.items.map((item) => (
              <ActivityRow key={item.id} item={item} />
            ))}
          </>
        )}
      </div>

      {/* Pagination */}
      {data && data.pages > 1 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {data.total} events · page {data.page} of {data.pages}
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 1}
              onClick={() => setPage((p) => p - 1)}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= data.pages}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
