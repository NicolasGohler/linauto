"use client";

import { use, useState, useCallback, useTransition, useMemo, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Mail, Check, RefreshCw, Trash2, Send, X } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useLeadList,
  useLeadListLeads,
  useImportCSVToList,
  useCampaigns,
  useAssignListToCampaign,
  useUnassignListFromCampaign,
  useUpdateLeadList,
  useReScrapeList,
  useScrapeStatus,
  useBulkRemoveLeads,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useDropzone } from "react-dropzone";
import { exportLeadListCSV } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

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

export default function LeadListDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const qc = useQueryClient();
  const { data: list, isLoading } = useLeadList(id);
  const [page, setPage] = useState(1);
  const { data: leadsData } = useLeadListLeads(id, { page, per_page: 50 });
  const importCSV = useImportCSVToList(id);
  const { data: campaigns } = useCampaigns();
  const assign = useAssignListToCampaign();
  const unassign = useUnassignListFromCampaign();
  const updateList = useUpdateLeadList(id);
  const reScrape = useReScrapeList(id);
  const bulkRemove = useBulkRemoveLeads();
  const [scrapingListId, setScrapingListId] = useState<string | null>(null);
  const { data: scrapeStatus } = useScrapeStatus(scrapingListId);
  const [selectedCampaign, setSelectedCampaign] = useState("");
  const [isExporting, startExport] = useTransition();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  // Clear selection whenever the page changes (rows shown differ).
  useEffect(() => {
    setSelectedIds(new Set());
  }, [page, id]);

  const pageIds = useMemo(
    () => (leadsData?.items ?? []).map((l) => l.id),
    [leadsData]
  );
  const allOnPageSelected =
    pageIds.length > 0 && pageIds.every((lid) => selectedIds.has(lid));
  const someOnPageSelected =
    pageIds.some((lid) => selectedIds.has(lid)) && !allOnPageSelected;

  function toggleAllOnPage() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) {
        pageIds.forEach((lid) => next.delete(lid));
      } else {
        pageIds.forEach((lid) => next.add(lid));
      }
      return next;
    });
  }

  function toggleOne(lid: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(lid)) next.delete(lid);
      else next.add(lid);
      return next;
    });
  }

  function handleBulkDelete() {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    bulkRemove.mutate(ids, {
      onSuccess: (data) => {
        toast.success(`Removed ${data.updated} leads`);
        setSelectedIds(new Set());
        setConfirmingDelete(false);
        qc.invalidateQueries({ queryKey: ["lead-list-leads", id] });
        qc.invalidateQueries({ queryKey: ["lead-lists", id] });
        qc.invalidateQueries({ queryKey: ["lead-lists"] });
      },
      onError: (err) => {
        toast.error(`Delete failed: ${err.message}`);
        setConfirmingDelete(false);
      },
    });
  }

  function handleStartBroadcast() {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    // Cap URL length — huge selections use sessionStorage as fallback.
    if (ids.length > 200) {
      try {
        sessionStorage.setItem("broadcast_lead_ids", JSON.stringify(ids));
        router.push(`/broadcasts/new?from=selection&list_id=${id}`);
      } catch {
        toast.error("Selection too large — please pick fewer leads");
      }
      return;
    }
    router.push(`/broadcasts/new?lead_ids=${ids.join(",")}&list_id=${id}`);
  }

  const onDrop = useCallback(
    (files: File[]) => {
      const file = files[0];
      if (!file) return;
      importCSV.mutate(file, {
        onSuccess: (data) =>
          toast.success(
            `Imported ${data.imported} leads (${data.duplicates_skipped} duplicates skipped)`
          ),
        onError: (err) => toast.error(`Import failed: ${err.message}`),
      });
    },
    [importCSV]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "text/csv": [".csv"] },
    maxFiles: 1,
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  if (!list) {
    return <p className="text-muted-foreground">Lead list not found</p>;
  }

  const assignedIds = new Set(list.campaigns.map((c) => c.id));
  const availableCampaigns =
    campaigns?.filter((c) => !assignedIds.has(c.id)) ?? [];

  const totalPages = leadsData ? Math.ceil(leadsData.total / leadsData.per_page) : 1;

  const stats = list.stats;
  const lastImported = new Date(list.updated_at);
  const lastImportedLabel = lastImported.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

  return (
    <div className="space-y-6">
      <PageHeader title={list.name}>
        <span className="text-sm text-muted-foreground">
          {list.total_leads} leads · Last imported {lastImportedLabel}
        </span>
        {list.source_url && (
          <Button
            variant="outline"
            size="sm"
            disabled={reScrape.isPending || scrapeStatus?.status === "running" || scrapeStatus?.status === "enriching" as string}
            onClick={() => {
              reScrape.mutate(
                {},
                {
                  onSuccess: (updated) => {
                    toast.success("Re-scrape started — collecting new attendees...");
                    setScrapingListId(updated.id);
                  },
                  onError: (err) => toast.error(`Re-scrape failed: ${err.message}`),
                }
              );
            }}
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${reScrape.isPending || scrapeStatus?.status === "running" ? "animate-spin" : ""}`} />
            {scrapeStatus?.status === "running"
              ? `Scraping… ${scrapeStatus.collected}`
              : scrapeStatus?.status === "done"
              ? `Done — ${scrapeStatus.collected} found`
              : "Re-scrape"}
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={isExporting || list.total_leads === 0}
          onClick={() =>
            startExport(async () => {
              try {
                await exportLeadListCSV(id, `${list.name}.csv`);
              } catch (err) {
                toast.error("Export failed");
              }
            })
          }
        >
          {isExporting ? "Exporting..." : "Export CSV"}
        </Button>
      </PageHeader>

      {/* Quality stat cards */}
      {stats && stats.total > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            {
              label: "Acceptance Rate",
              value: stats.acceptance_rate,
              sub: `${Math.round((stats.acceptance_rate / 100) * stats.total)} connected`,
              color: stats.acceptance_rate >= 30 ? "text-emerald-500" : stats.acceptance_rate >= 15 ? "text-amber-500" : "text-red-400",
            },
            {
              label: "Email Coverage",
              value: stats.email_coverage,
              sub: `${Math.round((stats.email_coverage / 100) * stats.total)} leads`,
              color: stats.email_coverage >= 50 ? "text-emerald-500" : stats.email_coverage >= 20 ? "text-amber-500" : "text-muted-foreground",
            },
            {
              label: "Telegram Coverage",
              value: stats.tg_coverage,
              sub: stats.tg_contacted_rate > 0 ? `${stats.tg_contacted_rate}% contacted` : `${Math.round((stats.tg_coverage / 100) * stats.total)} leads`,
              color: stats.tg_coverage >= 30 ? "text-emerald-500" : stats.tg_coverage >= 10 ? "text-amber-500" : "text-muted-foreground",
            },
            {
              label: "Twitter/X Coverage",
              value: stats.twitter_coverage,
              sub: stats.x_contacted_rate > 0 ? `${stats.x_contacted_rate}% contacted` : `${Math.round((stats.twitter_coverage / 100) * stats.total)} leads`,
              color: stats.twitter_coverage >= 30 ? "text-emerald-500" : stats.twitter_coverage >= 10 ? "text-amber-500" : "text-muted-foreground",
            },
          ].map((card) => (
            <div key={card.label} className="rounded-xl border bg-card px-4 py-3">
              <p className="text-xs text-muted-foreground">{card.label}</p>
              <p className={`text-2xl font-semibold tabular-nums mt-1 ${card.color}`}>{card.value}%</p>
              <p className="text-xs text-muted-foreground mt-0.5">{card.sub}</p>
            </div>
          ))}
        </div>
      )}

      {/* CSV Upload */}
      <div
        {...getRootProps()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 transition-colors ${
          isDragActive
            ? "border-primary bg-primary/5"
            : "border-border hover:border-muted-foreground/50"
        }`}
      >
        <input {...getInputProps()} />
        {importCSV.isPending ? (
          <p className="text-sm text-muted-foreground">Uploading...</p>
        ) : (
          <div className="text-center">
            <p className="text-sm font-medium">
              Drop a CSV to add leads, or click to browse
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Auto-detects LinkedIn URLs and maps columns
            </p>
          </div>
        )}
      </div>

      {/* Telegram enrichment toggle */}
      <Card>
        <CardHeader>
          <CardTitle>Settings</CardTitle>
        </CardHeader>
        <CardContent>
          <label className="flex items-start gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={list.tg_enrich_enabled}
              onChange={(e) => {
                const enabled = e.target.checked;
                updateList.mutate(
                  { tg_enrich_enabled: enabled },
                  {
                    onSuccess: () => toast.success(
                      enabled
                        ? "Telegram enrichment enabled"
                        : "Telegram enrichment disabled"
                    ),
                    onError: (err) => toast.error(err.message),
                  }
                );
              }}
              className="mt-0.5 h-4 w-4 rounded border-border accent-primary"
            />
            <div>
              <p className="text-sm font-medium">Telegram enrichment</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                When enabled, the background sweeper will look up Telegram handles for leads in this list.
                Disable for lists where Telegram outreach doesn&apos;t make sense.
              </p>
            </div>
          </label>
          <label className="flex items-start gap-3 cursor-pointer select-none mt-4">
            <input
              type="checkbox"
              checked={list.x_enrich_enabled}
              onChange={(e) => {
                const enabled = e.target.checked;
                updateList.mutate(
                  { x_enrich_enabled: enabled },
                  {
                    onSuccess: () => toast.success(enabled ? "X enrichment enabled" : "X enrichment disabled"),
                    onError: (err) => toast.error(err.message),
                  }
                );
              }}
              className="mt-0.5 h-4 w-4 rounded border-border accent-primary"
            />
            <div>
              <p className="text-sm font-medium">X enrichment</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                When enabled, the background sweeper looks up personal X handles for leads in this list that
                don&apos;t have one yet. Confident matches are saved, weaker ones are kept as candidates on the lead.
              </p>
            </div>
          </label>
        </CardContent>
      </Card>

      {/* Campaigns using this list */}
      <Card>
        <CardHeader>
          <CardTitle>Assigned Campaigns</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {list.campaigns.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Not assigned to any campaigns
            </p>
          ) : (
            <div className="space-y-2">
              {list.campaigns.map((c) => (
                <div
                  key={c.id}
                  className="flex items-center justify-between rounded-md bg-muted px-3 py-2"
                >
                  <Link href={`/campaigns/${c.id}`} className="text-sm hover:underline">{c.name}</Link>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      unassign.mutate(
                        { listId: id, campaignId: c.id },
                        {
                          onSuccess: (data) =>
                            toast.success(
                              `Unassigned — ${data.leads_removed} leads removed`
                            ),
                          onError: (err) => toast.error(err.message),
                        }
                      )
                    }
                  >
                    Unassign
                  </Button>
                </div>
              ))}
            </div>
          )}

          {availableCampaigns.length > 0 && (
            <div className="flex gap-2 pt-2">
              <select
                className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={selectedCampaign}
                onChange={(e) => setSelectedCampaign(e.target.value)}
              >
                <option value="">Select campaign...</option>
                {availableCampaigns.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <Button
                disabled={!selectedCampaign || assign.isPending}
                onClick={() => {
                  assign.mutate(
                    { listId: id, campaignId: selectedCampaign },
                    {
                      onSuccess: (data) => {
                        toast.success(`Assigned — ${data.leads_added} leads added`);
                        setSelectedCampaign("");
                      },
                      onError: (err) => toast.error(err.message),
                    }
                  );
                }}
              >
                Assign
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Sticky bulk-action bar (appears when ≥1 selected) */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2">
          <div className="flex items-center gap-2 rounded-full border border-border bg-background/95 px-4 py-2 shadow-xl backdrop-blur">
            <span className="text-sm font-medium">
              {selectedIds.size} selected
            </span>
            <span className="text-muted-foreground">·</span>
            <Button
              size="sm"
              variant="default"
              onClick={handleStartBroadcast}
              className="h-8"
            >
              <Send className="h-3.5 w-3.5 mr-1.5" />
              Start broadcast
            </Button>
            {confirmingDelete ? (
              <>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={handleBulkDelete}
                  disabled={bulkRemove.isPending}
                  className="h-8"
                >
                  {bulkRemove.isPending
                    ? "Deleting…"
                    : `Confirm delete ${selectedIds.size}`}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setConfirmingDelete(false)}
                  className="h-8"
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setConfirmingDelete(true)}
                className="h-8"
              >
                <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                Delete
              </Button>
            )}
            <button
              type="button"
              onClick={() => {
                setSelectedIds(new Set());
                setConfirmingDelete(false);
              }}
              className="ml-1 rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Clear selection"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Leads table */}
      <Card>
        <CardHeader>
          <CardTitle>Leads ({leadsData?.total ?? 0})</CardTitle>
        </CardHeader>
        <CardContent>
          {!leadsData || leadsData.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No leads in this list</p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 w-8 pr-2">
                        <input
                          type="checkbox"
                          aria-label="Select all on page"
                          className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                          checked={allOnPageSelected}
                          ref={(el) => {
                            if (el) el.indeterminate = someOnPageSelected;
                          }}
                          onChange={toggleAllOnPage}
                        />
                      </th>
                      <th className="pb-2 font-medium">Name</th>
                      <th className="pb-2 font-medium">Company</th>
                      <th className="pb-2 font-medium">Title</th>
                      <th className="pb-2 font-medium">LinkedIn</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leadsData.items.map((lead) => {
                      const checked = selectedIds.has(lead.id);
                      return (
                        <tr
                          key={lead.id}
                          className={`border-b last:border-0 ${checked ? "bg-primary/5" : ""}`}
                        >
                          <td className="py-2 pr-2">
                            <input
                              type="checkbox"
                              aria-label={`Select ${lead.first_name ?? "lead"}`}
                              className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                              checked={checked}
                              onChange={() => toggleOne(lead.id)}
                            />
                          </td>
                          <td className="py-2">
                            <div className="flex items-center gap-0.5">
                              <Link href={`/leads/${lead.id}`} className="hover:underline">
                                {[lead.first_name, lead.last_name].filter(Boolean).join(" ") || "—"}
                              </Link>
                              {lead.email && <EmailCopyButton email={lead.email} />}
                            </div>
                          </td>
                          <td className="py-2 max-w-[160px]">
                            <span className="block truncate" title={lead.company ?? undefined}>{lead.company ?? "—"}</span>
                          </td>
                          <td className="py-2 max-w-[180px]">
                            <span className="block truncate" title={lead.title ?? undefined}>{lead.title ?? "—"}</span>
                          </td>
                          <td className="py-2">
                            {lead.linkedin_url ? (
                              <a
                                href={lead.linkedin_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-500 hover:underline truncate block max-w-[200px] text-xs"
                              >
                                {lead.linkedin_url.replace("https://www.linkedin.com/in/", "")}
                              </a>
                            ) : (
                              <span className="text-muted-foreground text-xs">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-4">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={page <= 1}
                    onClick={() => setPage(page - 1)}
                  >
                    Previous
                  </Button>
                  <span className="text-sm text-muted-foreground">
                    Page {page} of {totalPages}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={page >= totalPages}
                    onClick={() => setPage(page + 1)}
                  >
                    Next
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
