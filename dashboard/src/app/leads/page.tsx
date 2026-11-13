"use client";

import { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";
import { isLinkedInProfileUrl } from "@/lib/query-cache";
import {
  useGlobalLeads,
  useLeadLists,
  useCampaigns,
  useDeleteLead,
  useRestoreLead,
  useSkipLead,
  useRequeueLead,
  useBulkSkipLeads,
  useBulkRemoveLeads,
  useBulkRequeueLeads,
  prefetchLeadPage,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { MultiSelectFilter } from "@/components/leads/multi-select-filter";
import { toast } from "sonner";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  FastForward, RotateCcw, Trash2, Undo2, Mail, Check,
  ChevronUp, ChevronDown, ChevronsUpDown, X, Send, CheckCircle2,
} from "lucide-react";

type SortKey = "name" | "company" | "status" | "last_activity_at" | "created_at";

const STATUS_LABELS: Record<string, string> = {
  connection_requested: "requested",
  followup_scheduled: "followup due",
  followup_sent: "followup sent",
};

const ALL_STATUSES = ["pending", "scheduled", "connection_requested", "connected", "followup_scheduled", "followup_sent", "completed", "skipped", "error", "removed"];
// "removed" leads are noise in the default view — start with them unchecked.
const DEFAULT_STATUSES = new Set(ALL_STATUSES.filter((s) => s !== "removed"));

function setsEqual(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

const ERROR_LABELS: Record<string, string> = {
  skipped_manually: "Skipped manually",
  email_required: "Email verification required",
  send_button_disabled: "Send button was disabled by LinkedIn",
  no_connect_button: "No Connect button found on profile",
  pending_request: "Connection request already pending",
  preload_navigation_failed: "Failed to load invitation page",
  no_vanity_name: "Could not extract profile identifier",
  weekly_invitation_limit: "Weekly invitation limit reached",
  profile_not_found: "Profile no longer exists",
};

const SKIP_REASON_OPTIONS = [
  { value: "", label: "All skip reasons" },
  { value: "skipped_manually", label: "Skipped manually" },
  { value: "email_required", label: "Email required" },
  { value: "send_button_disabled", label: "Send button disabled" },
  { value: "no_connect_button", label: "No connect button" },
  { value: "pending_request", label: "Already pending" },
  { value: "weekly_invitation_limit", label: "Weekly limit hit" },
  { value: "profile_not_found", label: "Profile not found" },
  { value: "filter_no_photo", label: "Filter: no photo" },
  { value: "filter_low_connections", label: "Filter: low connections" },
  { value: "filter_open_to_work", label: "Filter: open to work" },
  { value: "already_connected", label: "Already connected" },
];

function formatErrorMessage(msg: string): string {
  return ERROR_LABELS[msg] ?? msg.replace(/_/g, " ");
}

function relativeDate(dateStr: string): { label: string; title: string } {
  const date = new Date(dateStr.endsWith("Z") ? dateStr : dateStr + "Z");
  const diffDays = Math.floor((Date.now() - date.getTime()) / 86400000);
  const label =
    diffDays === 0 ? "today" :
    diffDays === 1 ? "yesterday" :
    diffDays < 7 ? `${diffDays}d ago` :
    diffDays < 30 ? `${Math.floor(diffDays / 7)}w ago` :
    `${Math.floor(diffDays / 30)}mo ago`;
  return { label, title: date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) };
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.742l7.736-8.849L1.254 2.25H8.08l4.253 5.622L18.244 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117L17.083 19.77z" />
    </svg>
  );
}

function SocialIconLink({ href, label, children }: { href: string; label: string; children: React.ReactNode }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="ml-1.5 inline-flex items-center text-muted-foreground/50 hover:text-muted-foreground transition-colors"
          >
            {children}
          </a>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function EmailCopyButton({ email }: { email: string }) {
  const [copied, setCopied] = useState(false);
  function handleCopy(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    navigator.clipboard.writeText(email).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button onClick={handleCopy} className="ml-1.5 inline-flex items-center text-muted-foreground/60 hover:text-muted-foreground transition-colors">
            {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Mail className="h-3 w-3" />}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">{copied ? "Copied!" : email}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function ActionIcon({ icon, label, onClick, disabled, destructive }: {
  icon: React.ReactNode; label: string; onClick: () => void; disabled?: boolean; destructive?: boolean;
}) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon"
            className={`h-7 w-7 ${destructive ? "text-muted-foreground hover:text-destructive hover:bg-destructive/10" : ""}`}
            onClick={onClick} disabled={disabled}
          >
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function SortIcon({ col, sortBy, sortDir }: { col: SortKey; sortBy: SortKey | null; sortDir: "asc" | "desc" }) {
  if (sortBy !== col) return <ChevronsUpDown className="ml-1 h-3 w-3 opacity-40 inline" />;
  return sortDir === "asc"
    ? <ChevronUp className="ml-1 h-3 w-3 inline" />
    : <ChevronDown className="ml-1 h-3 w-3 inline" />;
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

export default function GlobalLeadsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  // A pasted LinkedIn URL is a lookup, not typing: search right away.
  const debouncedSearch = useDebounce(search, isLinkedInProfileUrl(search) ? 0 : 300);
  const router = useRouter();
  const qc = useQueryClient();

  function openLead(id: string) {
    void prefetchLeadPage(qc, id);
    router.push(`/leads/${id}`);
  }

  // Pasting a LinkedIn profile URL opens that lead directly. The exact-URL
  // lookup is much cheaper than the full search, and the lead page draws from
  // the lookup result while its full record loads.
  async function handleSearchPaste(e: React.ClipboardEvent<HTMLInputElement>) {
    const text = e.clipboardData.getData("text").trim();
    if (!isLinkedInProfileUrl(text)) return;
    try {
      const lead = await api.lookupLeadByUrl(text);
      qc.setQueryData(["lead-lookup", lead.id], lead);
      openLead(lead.id);
    } catch {
      // Not in the database: the list search below shows the empty result.
    }
  }

  function handleSearchKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    const items = leadsData?.items ?? [];
    if (items.length === 1) openLead(items[0].id);
  }
  const [statusFilter, setStatusFilter] = useState<Set<string>>(new Set(DEFAULT_STATUSES));
  const [listFilter, setListFilter] = useState<Set<string>>(new Set());
  const [campaignFilter, setCampaignFilter] = useState<string | undefined>();
  const [skipReasonFilter, setSkipReasonFilter] = useState<string>("");
  const [lastActivityAfter, setLastActivityAfter] = useState<string>("");
  const [lastActivityBefore, setLastActivityBefore] = useState<string>("");
  const [sortBy, setSortBy] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Social / outreach filter chips
  const [hasTelegram, setHasTelegram] = useState<boolean | undefined>();
  const [hasTwitter, setHasTwitter] = useState<boolean | undefined>();
  const [hasEmail, setHasEmail] = useState<boolean | undefined>();
  const [tgContacted, setTgContacted] = useState<boolean | undefined>();
  const [xContacted, setXContacted] = useState<boolean | undefined>();

  useEffect(() => { setPage(1); }, [debouncedSearch]);

  const { data: lists } = useLeadLists();
  const { data: campaigns } = useCampaigns();

  const listOptions = useMemo(
    () => [
      { value: "__unassigned__", label: "— Unassigned —" },
      ...(lists?.map((ll) => ({ value: ll.id, label: ll.name })) ?? []),
    ],
    [lists]
  );

  // Sets convert to comma-joined query params. Empty status selection has to be sent as
  // a sentinel that matches no real status (rather than omitted, which would mean "no filter").
  const statusParam =
    statusFilter.size === ALL_STATUSES.length
      ? undefined
      : statusFilter.size === 0
      ? "__none__"
      : Array.from(statusFilter).join(",");
  const listParam = listFilter.size === 0 ? undefined : Array.from(listFilter).join(",");

  const { data: leadsData, isLoading } = useGlobalLeads({
    page, per_page: 50,
    search: debouncedSearch || undefined,
    status: statusParam,
    lead_list_id: listParam,
    campaign_id: campaignFilter,
    sort_by: sortBy ?? undefined,
    sort_dir: sortDir,
    last_activity_after: lastActivityAfter || undefined,
    last_activity_before: lastActivityBefore || undefined,
    skip_reason: skipReasonFilter || undefined,
    has_telegram: hasTelegram,
    has_twitter: hasTwitter,
    has_email: hasEmail,
    tg_contacted: tgContacted,
    x_contacted: xContacted,
  });
  const deleteLead = useDeleteLead();
  const restoreLead = useRestoreLead();
  const skipLead = useSkipLead();
  const requeueLead = useRequeueLead();
  const bulkSkip = useBulkSkipLeads();
  const bulkRemove = useBulkRemoveLeads();
  const bulkRequeue = useBulkRequeueLeads();

  const totalPages = leadsData ? Math.ceil(leadsData.total / leadsData.per_page) : 1;

  function toggleSort(col: SortKey) {
    if (sortBy === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(col);
      setSortDir("asc");
    }
    setPage(1);
  }

  const hasActiveFilters = Boolean(debouncedSearch) || !setsEqual(statusFilter, DEFAULT_STATUSES) || listFilter.size > 0 || campaignFilter || skipReasonFilter || lastActivityAfter || lastActivityBefore || sortBy || hasTelegram !== undefined || hasTwitter !== undefined || hasEmail !== undefined || tgContacted !== undefined || xContacted !== undefined;

  function resetFilters() {
    setSearch(""); setStatusFilter(new Set(DEFAULT_STATUSES)); setListFilter(new Set()); setCampaignFilter(undefined);
    setSkipReasonFilter(""); setLastActivityAfter(""); setLastActivityBefore(""); setSortBy(null); setSortDir("desc"); setPage(1);
    setSelected(new Set());
    setHasTelegram(undefined); setHasTwitter(undefined); setHasEmail(undefined); setTgContacted(undefined); setXContacted(undefined);
  }

  function toggleChip<T>(current: T | undefined, value: T, setter: (v: T | undefined) => void) {
    setter(current === value ? undefined : value);
    setPage(1);
  }

  function toggleSelect(id: string) {
    setSelected((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }

  function toggleSelectAll() {
    if (!leadsData?.items) return;
    const allIds = leadsData.items.map((l) => l.id);
    if (allIds.every((id) => selected.has(id))) {
      setSelected((prev) => { const next = new Set(prev); allIds.forEach((id) => next.delete(id)); return next; });
    } else {
      setSelected((prev) => { const next = new Set(prev); allIds.forEach((id) => next.add(id)); return next; });
    }
  }

  const selectedIds = Array.from(selected);
  const pageIds = leadsData?.items.map((l) => l.id) ?? [];
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  return (
    <div className="space-y-6">
      <PageHeader title="Lead Library" description="All leads across all lists and campaigns" />

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <Input
          placeholder="Search leads..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onPaste={handleSearchPaste}
          onKeyDown={handleSearchKeyDown}
          className="max-w-xs"
        />
        <MultiSelectFilter
          label="Status"
          options={ALL_STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] ?? s.replace(/_/g, " ") }))}
          selected={statusFilter}
          onChange={(next) => { setStatusFilter(next); setPage(1); }}
        />
        <MultiSelectFilter
          label="Lists"
          searchable
          emptyMeansAll
          options={listOptions}
          selected={listFilter}
          onChange={(next) => { setListFilter(next); setPage(1); }}
          renderTriggerLabel={(count, total) => (count === 0 || count === total ? "All lists" : `Lists (${count})`)}
        />
        <select
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={campaignFilter ?? ""}
          onChange={(e) => { setCampaignFilter(e.target.value || undefined); setPage(1); }}
        >
          <option value="">All campaigns</option>
          <option value="__unassigned__">— Unassigned —</option>
          {campaigns?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={skipReasonFilter}
          onChange={(e) => { setSkipReasonFilter(e.target.value); setPage(1); }}
        >
          {SKIP_REASON_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <div className="flex items-center gap-1">
          <Input
            type="date"
            value={lastActivityAfter}
            onChange={(e) => { setLastActivityAfter(e.target.value); setPage(1); }}
            className="h-9 w-36 text-sm"
            title="Last activity after"
          />
          <span className="text-muted-foreground text-xs">–</span>
          <Input
            type="date"
            value={lastActivityBefore}
            onChange={(e) => { setLastActivityBefore(e.target.value); setPage(1); }}
            className="h-9 w-36 text-sm"
            title="Last activity before"
          />
        </div>
        {hasActiveFilters && (
          <Button variant="ghost" size="sm" onClick={resetFilters} className="gap-1 text-muted-foreground">
            <X className="h-3.5 w-3.5" /> Clear
          </Button>
        )}
      </div>

      {/* Social filter chips */}
      <div className="flex flex-wrap gap-2">
        {(
          [
            { label: "Has Telegram", icon: <Send className="h-3 w-3" />, active: hasTelegram === true, onClick: () => toggleChip(hasTelegram, true, setHasTelegram) },
            { label: "No Telegram", icon: null, active: hasTelegram === false, onClick: () => toggleChip(hasTelegram, false, setHasTelegram) },
            { label: "Has X / Twitter", icon: <XIcon className="h-3 w-3" />, active: hasTwitter === true, onClick: () => toggleChip(hasTwitter, true, setHasTwitter) },
            { label: "Has Email", icon: <Mail className="h-3 w-3" />, active: hasEmail === true, onClick: () => toggleChip(hasEmail, true, setHasEmail) },
            { label: "TG Contacted", icon: <CheckCircle2 className="h-3 w-3" />, active: tgContacted === true, onClick: () => toggleChip(tgContacted, true, setTgContacted) },
            { label: "TG Not Contacted", icon: null, active: tgContacted === false, onClick: () => toggleChip(tgContacted, false, setTgContacted) },
            { label: "X Contacted", icon: <CheckCircle2 className="h-3 w-3" />, active: xContacted === true, onClick: () => toggleChip(xContacted, true, setXContacted) },
            { label: "X Not Contacted", icon: null, active: xContacted === false, onClick: () => toggleChip(xContacted, false, setXContacted) },
          ] as { label: string; icon: React.ReactNode; active: boolean; onClick: () => void }[]
        ).map((chip) => (
          <button
            key={chip.label}
            onClick={chip.onClick}
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium border transition-colors ${
              chip.active
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-background text-muted-foreground border-border hover:border-foreground/40 hover:text-foreground"
            }`}
          >
            {chip.icon}
            {chip.label}
            {chip.active && <X className="h-2.5 w-2.5 ml-0.5 opacity-70" />}
          </button>
        ))}
      </div>

      {/* Bulk action bar */}
      {selectedIds.length > 0 && (
        <div className="flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2">
          <span className="text-sm text-muted-foreground mr-2">{selectedIds.length} selected</span>
          <Button size="sm" variant="outline" onClick={() => {
            bulkSkip.mutate(selectedIds, {
              onSuccess: (r) => { toast.success(`Skipped ${r.updated} leads`); setSelected(new Set()); },
              onError: (e) => toast.error(e.message),
            });
          }} disabled={bulkSkip.isPending}>
            <FastForward className="mr-1 h-3.5 w-3.5" /> Skip
          </Button>
          <Button size="sm" variant="outline" onClick={() => {
            bulkRequeue.mutate(selectedIds, {
              onSuccess: (r) => { toast.success(`Re-queued ${r.updated} leads`); setSelected(new Set()); },
              onError: (e) => toast.error(e.message),
            });
          }} disabled={bulkRequeue.isPending}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> Re-queue
          </Button>
          <Button size="sm" variant="outline" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => {
            bulkRemove.mutate(selectedIds, {
              onSuccess: (r) => { toast.success(`Removed ${r.updated} leads`); setSelected(new Set()); },
              onError: (e) => toast.error(e.message),
            });
          }} disabled={bulkRemove.isPending}>
            <Trash2 className="mr-1 h-3.5 w-3.5" /> Remove
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} className="ml-auto text-muted-foreground">
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Leads {leadsData ? `(${leadsData.total})` : ""}</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
            </div>
          ) : !leadsData || leadsData.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No leads found</p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 w-8">
                        <input type="checkbox" checked={allPageSelected} onChange={toggleSelectAll} className="h-4 w-4 rounded border-border" />
                      </th>
                      <th className="pb-2 font-medium cursor-pointer select-none" onClick={() => toggleSort("name")}>
                        Name <SortIcon col="name" sortBy={sortBy} sortDir={sortDir} />
                      </th>
                      <th className="pb-2 font-medium cursor-pointer select-none" onClick={() => toggleSort("company")}>
                        Company <SortIcon col="company" sortBy={sortBy} sortDir={sortDir} />
                      </th>
                      <th className="pb-2 font-medium">Campaign</th>
                      <th className="pb-2 font-medium">List</th>
                      <th className="pb-2 font-medium cursor-pointer select-none" onClick={() => toggleSort("status")}>
                        Status <SortIcon col="status" sortBy={sortBy} sortDir={sortDir} />
                      </th>
                      <th className="pb-2 font-medium cursor-pointer select-none" onClick={() => toggleSort("last_activity_at")}>
                        Last Activity <SortIcon col="last_activity_at" sortBy={sortBy} sortDir={sortDir} />
                      </th>
                      <th className="pb-2 font-medium text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leadsData.items.map((lead) => {
                      const lastActivityAt = lead.last_activity_at ? relativeDate(lead.last_activity_at) : null;
                      const isSelected = selected.has(lead.id);
                      return (
                        <tr key={lead.id} className={`border-b last:border-0 ${isSelected ? "bg-muted/30" : ""}`}>
                          <td className="py-2">
                            <input type="checkbox" checked={isSelected} onChange={() => toggleSelect(lead.id)} className="h-4 w-4 rounded border-border" />
                          </td>
                          <td className="py-2">
                            <div className="flex items-center gap-0.5">
                              <Link href={`/leads/${lead.id}`} className="font-medium hover:underline">
                                {[lead.first_name, lead.last_name].filter(Boolean).join(" ") || "—"}
                              </Link>
                              {lead.email && <EmailCopyButton email={lead.email} />}
                              {lead.twitter_url && (
                                <SocialIconLink
                                  href={lead.twitter_url}
                                  label={lead.x_contacted_at ? `X: ${lead.twitter_url} · Contacted ${relativeDate(lead.x_contacted_at).label}` : `X: ${lead.twitter_url}`}
                                >
                                  <span className="relative inline-flex">
                                    <XIcon className={`h-3 w-3 ${lead.x_contacted_at ? "text-emerald-500" : ""}`} />
                                    {lead.x_contacted_at && (
                                      <span className="absolute -top-1 -right-1 h-2 w-2 rounded-full bg-emerald-500 flex items-center justify-center">
                                        <Check className="h-1.5 w-1.5 text-white" />
                                      </span>
                                    )}
                                  </span>
                                </SocialIconLink>
                              )}
                              {lead.telegram_username && (
                                <SocialIconLink
                                  href={`https://t.me/${lead.telegram_username}`}
                                  label={lead.tg_contacted_at ? `Telegram: @${lead.telegram_username} · Contacted ${relativeDate(lead.tg_contacted_at).label}` : `Telegram: @${lead.telegram_username}`}
                                >
                                  <span className="relative inline-flex">
                                    <Send className={`h-3 w-3 ${lead.tg_contacted_at ? "text-emerald-500" : ""}`} />
                                    {lead.tg_contacted_at && (
                                      <span className="absolute -top-1 -right-1 h-2 w-2 rounded-full bg-emerald-500 flex items-center justify-center">
                                        <Check className="h-1.5 w-1.5 text-white" />
                                      </span>
                                    )}
                                  </span>
                                </SocialIconLink>
                              )}
                            </div>
                          </td>
                          <td className="py-2 text-muted-foreground max-w-[160px]">
                            <span className="block truncate" title={lead.company ?? undefined}>{lead.company ?? "—"}</span>
                          </td>
                          <td className="py-2 text-muted-foreground">{lead.campaign_name ?? "—"}</td>
                          <td className="py-2 text-muted-foreground max-w-[140px]">
                            <span className="block truncate" title={lead.lead_list_name ?? undefined}>{lead.lead_list_name ?? "—"}</span>
                          </td>
                          <td className="py-2">
                            {lead.campaigns?.length || lead.error_message || (lead.status === "scheduled" && lead.scheduled_at) ? (
                              <TooltipProvider delayDuration={200}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <button
                                      type="button"
                                      className="inline-flex items-center gap-1.5 cursor-help rounded focus-visible:outline-2 focus-visible:outline-ring"
                                      aria-label={`${lead.campaign_name ?? "Lead"}: ${lead.status}${lead.campaigns?.length ? `, ${lead.campaigns.length} campaigns` : ""}`}
                                      onClick={(event) => event.stopPropagation()}
                                    >
                                      <StatusBadge status={lead.status} />
                                      {(lead.campaigns?.length ?? 0) > 1 && (
                                        <span className="text-xs text-muted-foreground tabular-nums">
                                          {Math.max(0, lead.campaigns!.findIndex((campaign) => campaign.id === lead.campaign_id)) + 1}/{lead.campaigns!.length}
                                        </span>
                                      )}
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent side="top" collisionPadding={8} className="max-w-xs space-y-2">
                                    {lead.campaigns?.map((campaign) => (
                                      <div key={campaign.id} className="flex items-start gap-3">
                                        <div className="min-w-0 flex-1">
                                          <p className="text-xs font-medium break-words">{campaign.name}</p>
                                          {campaign.account_name && <p className="text-xs opacity-75 break-words">{campaign.account_name}</p>}
                                        </div>
                                        <StatusBadge status={campaign.status} />
                                      </div>
                                    ))}
                                    {(lead.error_message || (lead.status === "scheduled" && lead.scheduled_at)) && <p className="text-xs">
                                      {lead.error_message
                                        ? formatErrorMessage(lead.error_message)
                                        : `Scheduled for ${new Date(lead.scheduled_at! + "Z").toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`}
                                    </p>}
                                  </TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            ) : (
                              <StatusBadge status={lead.status} />
                            )}
                          </td>
                          <td className="py-2 text-muted-foreground text-xs whitespace-nowrap">
                            {lastActivityAt ? (
                              <TooltipProvider delayDuration={200}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <span className="cursor-default">{lastActivityAt.label}</span>
                                  </TooltipTrigger>
                                  <TooltipContent side="top" className="text-xs">{lastActivityAt.title}</TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            ) : "—"}
                          </td>
                          <td className="py-2 text-right">
                            <div className="flex items-center justify-end gap-0.5">
                              {(lead.status === "pending" || lead.status === "scheduled") && (
                                <ActionIcon icon={<FastForward className="h-3.5 w-3.5" />} label="Skip"
                                  onClick={() => skipLead.mutate(lead.id, { onSuccess: () => toast.success("Lead skipped"), onError: (e) => toast.error(e.message) })}
                                  disabled={skipLead.isPending} />
                              )}
                              {(lead.status === "error" || lead.status === "withdrawn" || lead.status === "skipped") && (
                                <ActionIcon icon={<RotateCcw className="h-3.5 w-3.5" />} label="Re-queue"
                                  onClick={() => requeueLead.mutate(lead.id, { onSuccess: () => toast.success("Lead re-queued"), onError: (e) => toast.error(e.message) })}
                                  disabled={requeueLead.isPending} />
                              )}
                              {lead.status === "removed" ? (
                                <ActionIcon icon={<Undo2 className="h-3.5 w-3.5" />} label="Restore"
                                  onClick={() => restoreLead.mutate(lead.id, { onSuccess: () => toast.success("Lead restored"), onError: (e) => toast.error(e.message) })}
                                  disabled={restoreLead.isPending} />
                              ) : (
                                <ActionIcon icon={<Trash2 className="h-3.5 w-3.5" />} label="Remove"
                                  onClick={() => deleteLead.mutate(lead.id, { onSuccess: () => toast.success("Lead removed"), onError: (e) => toast.error(e.message) })}
                                  disabled={deleteLead.isPending} destructive />
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-4">
                  <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
                  <span className="text-sm text-muted-foreground">Page {page} of {totalPages}</span>
                  <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
