"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useLeads,
  useDeleteLead,
  useRestoreLead,
  useSkipLead,
  useRequeueLead,
  useBulkSkipLeads,
  useBulkRemoveLeads,
  useBulkRequeueLeads,
} from "@/hooks/use-queries";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { exportCampaignLeadsCSV } from "@/lib/api";
import {
  FastForward,
  RotateCcw,
  Trash2,
  Undo2,
  Mail,
  Check,
  MessageSquare,
  ChevronUp,
  ChevronDown,
  ChevronsUpDown,
  X,
  Send,
} from "lucide-react";

interface LeadsTableProps {
  campaignId: string;
  timezone?: string | null;
  assignedLists?: { id: string; name: string; total_leads: number }[];
  campaignName?: string;
}

type SortKey = "name" | "company" | "status" | "last_activity_at" | "created_at";

const ALL_ACTIVE = "__all_active__";

const STATUS_LABELS: Record<string, string> = {
  connection_requested: "requested",
};

const ERROR_LABELS: Record<string, string> = {
  skipped_manually: "Skipped manually",
  email_required: "Email verification required — LinkedIn requires their email to connect",
  send_button_disabled: "Send button was disabled by LinkedIn",
  no_connect_button: "No Connect button found on profile",
  pending_request: "Connection request already pending",
  preload_navigation_failed: "Failed to load invitation page",
  no_vanity_name: "Could not extract profile identifier",
  weekly_invitation_limit: "Weekly invitation limit reached",
  profile_not_found: "Profile no longer exists (deleted or URL changed)",
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

function relativeDate(dateStr: string, timezone?: string | null): { label: string; title: string } {
  const date = new Date(dateStr.endsWith("Z") ? dateStr : dateStr + "Z");
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  let label: string;
  if (diffDays === 0) label = "today";
  else if (diffDays === 1) label = "yesterday";
  else if (diffDays < 7) label = `${diffDays}d ago`;
  else if (diffDays < 30) label = `${Math.floor(diffDays / 7)}w ago`;
  else label = `${Math.floor(diffDays / 30)}mo ago`;

  const title = date.toLocaleString(undefined, {
    timeZone: timezone ?? undefined,
    dateStyle: "medium",
    timeStyle: "short",
  });

  return { label, title };
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

function ActionButton({ icon, label, onClick, disabled, destructive }: {
  icon: React.ReactNode; label: string; onClick: () => void; disabled?: boolean; destructive?: boolean;
}) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost" size="icon"
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

export function LeadsTable({ campaignId, timezone, assignedLists, campaignName }: LeadsTableProps) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>(ALL_ACTIVE);
  const [listFilter, setListFilter] = useState<string>("");
  const [skipReasonFilter, setSkipReasonFilter] = useState<string>("");
  const [lastActivityAfter, setLastActivityAfter] = useState<string>("");
  const [lastActivityBefore, setLastActivityBefore] = useState<string>("");
  const [sortBy, setSortBy] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exporting, setExporting] = useState(false);

  const skipLead = useSkipLead();
  const requeueLead = useRequeueLead();
  const deleteLead = useDeleteLead();
  const restoreLead = useRestoreLead();
  const bulkSkip = useBulkSkipLeads();
  const bulkRemove = useBulkRemoveLeads();
  const bulkRequeue = useBulkRequeueLeads();

  const isAllActive = statusFilter === ALL_ACTIVE;
  const { data, isLoading } = useLeads(campaignId, {
    page,
    per_page: 25,
    status: isAllActive ? undefined : statusFilter,
    search: search || undefined,
    excludeRemoved: isAllActive,
    leadListId: listFilter || undefined,
    sortBy: sortBy ?? undefined,
    sortDir,
    lastActivityAfter: lastActivityAfter || undefined,
    lastActivityBefore: lastActivityBefore || undefined,
    skipReason: skipReasonFilter || undefined,
  });

  function toggleSort(col: SortKey) {
    if (sortBy === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(col);
      setSortDir("asc");
    }
    setPage(1);
  }

  function resetFilters() {
    setSearch(""); setStatusFilter(ALL_ACTIVE); setListFilter(""); setSkipReasonFilter("");
    setLastActivityAfter(""); setLastActivityBefore(""); setSortBy(null); setSortDir("asc"); setPage(1);
    setSelected(new Set());
  }

  const hasActiveFilters = search || statusFilter !== ALL_ACTIVE || listFilter || skipReasonFilter || lastActivityAfter || lastActivityBefore || sortBy;

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (!data?.items) return;
    const allIds = data.items.map((l) => l.id);
    if (allIds.every((id) => selected.has(id))) {
      setSelected((prev) => { const next = new Set(prev); allIds.forEach((id) => next.delete(id)); return next; });
    } else {
      setSelected((prev) => { const next = new Set(prev); allIds.forEach((id) => next.add(id)); return next; });
    }
  }

  const selectedIds = Array.from(selected);
  const pageIds = data?.items.map((l) => l.id) ?? [];
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  async function handleExport() {
    setExporting(true);
    try {
      const safeName = (campaignName || "campaign").replace(/[^\w-]+/g, "_");
      const today = new Date().toISOString().slice(0, 10);
      await exportCampaignLeadsCSV(campaignId, `${safeName}_leads_${today}.csv`, {
        status: isAllActive ? undefined : statusFilter,
        search: search || undefined,
        excludeRemoved: isAllActive,
        leadListId: listFilter || undefined,
      });
      toast.success(`Exported ${data?.total ?? 0} leads`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  const statuses = [ALL_ACTIVE, "pending", "scheduled", "connection_requested", "connected", "error", "skipped", "invalid", "removed"];

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
      </div>
    );
  }

  const totalPages = data ? Math.ceil(data.total / data.per_page) : 1;

  return (
    <div className="space-y-4">
      {/* Filters row 1: search + list + skip reason + dates */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search name, company, title, URL…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="max-w-xs"
        />
        {assignedLists && assignedLists.length > 0 && (
          <select
            className="h-9 rounded-md border border-border bg-background px-2 text-sm"
            value={listFilter}
            onChange={(e) => { setListFilter(e.target.value); setPage(1); }}
          >
            <option value="">All lists</option>
            {assignedLists.map((ll) => <option key={ll.id} value={ll.id}>{ll.name}</option>)}
          </select>
        )}
        <select
          className="h-9 rounded-md border border-border bg-background px-2 text-sm"
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
          <Button variant="ghost" size="sm" onClick={resetFilters} className="h-9 gap-1 text-muted-foreground">
            <X className="h-3.5 w-3.5" /> Clear
          </Button>
        )}
        <Button
          variant="outline" size="sm" onClick={handleExport}
          disabled={exporting || !data || data.total === 0}
          className="ml-auto"
        >
          {exporting ? "Exporting…" : `Export CSV${data ? ` (${data.total})` : ""}`}
        </Button>
      </div>

      {/* Filters row 2: status buttons */}
      <div className="flex flex-wrap gap-1">
        {statuses.map((s) => (
          <Button
            key={s}
            variant={statusFilter === s ? "secondary" : "ghost"}
            size="sm"
            onClick={() => { setStatusFilter(s); setPage(1); }}
          >
            {s === ALL_ACTIVE ? "All" : (STATUS_LABELS[s] ?? s.replace(/_/g, " "))}
          </Button>
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

      {/* Table */}
      <div className="rounded-md border max-h-[600px] overflow-auto">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-background shadow-[0_1px_0_0_hsl(var(--border))]">
            <TableRow>
              <TableHead className="w-8">
                <input
                  type="checkbox"
                  checked={allPageSelected}
                  onChange={toggleSelectAll}
                  className="h-4 w-4 rounded border-border"
                />
              </TableHead>
              <TableHead className="cursor-pointer select-none" onClick={() => toggleSort("name")}>
                Name <SortIcon col="name" sortBy={sortBy} sortDir={sortDir} />
              </TableHead>
              <TableHead className="cursor-pointer select-none" onClick={() => toggleSort("company")}>
                Company <SortIcon col="company" sortBy={sortBy} sortDir={sortDir} />
              </TableHead>
              <TableHead>List</TableHead>
              <TableHead>Title</TableHead>
              <TableHead className="cursor-pointer select-none" onClick={() => toggleSort("status")}>
                Status <SortIcon col="status" sortBy={sortBy} sortDir={sortDir} />
              </TableHead>
              <TableHead className="cursor-pointer select-none" onClick={() => toggleSort("last_activity_at")}>
                Last Activity <SortIcon col="last_activity_at" sortBy={sortBy} sortDir={sortDir} />
              </TableHead>
              <TableHead className="w-8 text-center" title="Follow-up sent">
                <MessageSquare className="h-3.5 w-3.5 mx-auto text-muted-foreground" />
              </TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="py-12 text-center">
                  {statusFilter === "connected" ? (
                    <div className="space-y-1">
                      <p className="text-sm text-muted-foreground">No accepted connections yet</p>
                      <p className="text-xs text-muted-foreground/60">
                        The acceptance checker runs daily at 10 AM in the account timezone
                      </p>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No leads found</p>
                  )}
                </TableCell>
              </TableRow>
            ) : null}

            {data?.items.map((lead) => {
              const lastActivityAt = lead.last_activity_at
                ? relativeDate(lead.last_activity_at, timezone)
                : null;
              const isSelected = selected.has(lead.id);

              return (
                <TableRow key={lead.id} className={isSelected ? "bg-muted/30" : undefined}>
                  <TableCell>
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelect(lead.id)}
                      className="h-4 w-4 rounded border-border"
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-0.5">
                      <Link href={`/leads/${lead.id}`} className="text-sm font-medium hover:underline">
                        {[lead.first_name, lead.last_name].filter(Boolean).join(" ") || "—"}
                      </Link>
                      {lead.email && <EmailCopyButton email={lead.email} />}
                      {lead.twitter_url && (
                        <SocialIconLink href={lead.twitter_url} label={`X: ${lead.twitter_url}`}>
                          <XIcon className="h-3 w-3" />
                        </SocialIconLink>
                      )}
                      {lead.telegram_username && (
                        <SocialIconLink
                          href={`https://t.me/${lead.telegram_username}`}
                          label={`Telegram: @${lead.telegram_username}`}
                        >
                          <Send className="h-3 w-3" />
                        </SocialIconLink>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground max-w-[150px]">
                    <span className="block truncate" title={lead.company || undefined}>{lead.company || "—"}</span>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground max-w-[140px]">
                    <span className="block truncate" title={lead.lead_list_name || undefined}>{lead.lead_list_name || "—"}</span>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground max-w-[170px]">
                    <span className="block truncate" title={lead.title || undefined}>{lead.title || "—"}</span>
                  </TableCell>
                  <TableCell>
                    {lead.error_message || (lead.status === "scheduled" && lead.scheduled_at) ? (
                      <TooltipProvider delayDuration={200}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="cursor-help"><StatusBadge status={lead.status} /></span>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="max-w-xs">
                            <p className="text-xs">
                              {lead.error_message
                                ? formatErrorMessage(lead.error_message)
                                : `Scheduled for ${new Date(lead.scheduled_at! + "Z").toLocaleString(undefined, {
                                    timeZone: timezone ?? undefined,
                                    dateStyle: "medium",
                                    timeStyle: "short",
                                  })}`}
                            </p>
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : (
                      <StatusBadge status={lead.status} />
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
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
                  </TableCell>
                  <TableCell className="text-center">
                    {lead.followup_sent_at ? (
                      <TooltipProvider delayDuration={200}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex justify-center">
                              <MessageSquare className="h-3.5 w-3.5 text-emerald-500" />
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="text-xs">
                            Follow-up sent {relativeDate(lead.followup_sent_at, timezone).label}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : (
                      <span className="text-muted-foreground/30 text-xs">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-0.5">
                      {(lead.status === "pending" || lead.status === "scheduled") && (
                        <ActionButton icon={<FastForward className="h-3.5 w-3.5" />} label="Skip"
                          onClick={() => skipLead.mutate(lead.id, { onSuccess: () => toast.success("Lead skipped"), onError: (e) => toast.error(e.message) })}
                          disabled={skipLead.isPending} />
                      )}
                      {(lead.status === "error" || lead.status === "withdrawn" || lead.status === "skipped") && (
                        <ActionButton icon={<RotateCcw className="h-3.5 w-3.5" />} label="Re-queue"
                          onClick={() => requeueLead.mutate(lead.id, { onSuccess: () => toast.success("Lead re-queued"), onError: (e) => toast.error(e.message) })}
                          disabled={requeueLead.isPending} />
                      )}
                      {lead.status === "removed" ? (
                        <ActionButton icon={<Undo2 className="h-3.5 w-3.5" />} label="Restore"
                          onClick={() => restoreLead.mutate(lead.id, { onSuccess: () => toast.success("Lead restored"), onError: (e) => toast.error(e.message) })}
                          disabled={restoreLead.isPending} />
                      ) : (
                        <ActionButton icon={<Trash2 className="h-3.5 w-3.5" />} label="Remove"
                          onClick={() => deleteLead.mutate(lead.id, { onSuccess: () => toast.success("Lead removed"), onError: (e) => toast.error(e.message) })}
                          disabled={deleteLead.isPending} destructive />
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">{data?.total ?? 0} total leads</p>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
            <span className="text-sm text-muted-foreground">{page} / {totalPages}</span>
            <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</Button>
          </div>
        </div>
      )}
    </div>
  );
}
