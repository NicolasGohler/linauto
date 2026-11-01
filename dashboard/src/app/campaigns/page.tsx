"use client";

import { useState, useMemo, useEffect } from "react";
import Link from "next/link";
import { useCampaigns, useActivateCampaign, usePauseCampaign, useTimedPauseCampaign } from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { AddCard } from "@/components/add-card";
import { toast } from "sonner";
import { ResumeCampaignDialog, shouldOfferCatchup } from "@/components/resume-campaign-dialog";
import type { Campaign } from "@/lib/types";

function useCountdown(targetIso: string | null) {
  const [remaining, setRemaining] = useState("");
  useEffect(() => {
    if (!targetIso) { setRemaining(""); return; }
    const tick = () => {
      const diff = new Date(targetIso.endsWith("Z") ? targetIso : targetIso + "Z").getTime() - Date.now();
      if (diff <= 0) { setRemaining(""); return; }
      const m = Math.floor(diff / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      setRemaining(m > 0 ? `${m}m ${s}s` : `${s}s`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [targetIso]);
  return remaining;
}

type SortOption = "name" | "progress" | "remaining" | "last_activity";

const SORT_LABELS: Record<SortOption, string> = {
  name: "Name A→Z",
  progress: "Progress ↓",
  remaining: "Remaining ↓",
  last_activity: "Last active",
};

export default function CampaignsPage() {
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortOption>("last_activity");
  const { data: campaigns, isLoading } = useCampaigns({ status: statusFilter });
  const activate = useActivateCampaign();
  const pause = usePauseCampaign();
  const timedPause = useTimedPauseCampaign();
  const [resumeDialogCampaign, setResumeDialogCampaign] = useState<Campaign | null>(null);

  function getProgress(counts: Record<string, number> | null | undefined) {
    if (!counts) return { total: 0, sent: 0, accepted: 0, other: 0, pending: 0, sentPct: 0, acceptedPct: 0, otherPct: 0 };
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    if (total === 0) return { total: 0, sent: 0, accepted: 0, other: 0, pending: 0, sentPct: 0, acceptedPct: 0, otherPct: 0 };
    const accepted = (counts["connected"] ?? 0) + (counts["followup_scheduled"] ?? 0) + (counts["followup_sent"] ?? 0) + (counts["completed"] ?? 0);
    const sent = counts["connection_requested"] ?? 0;
    const pending = (counts["pending"] ?? 0) + (counts["scheduled"] ?? 0);
    const other = total - pending - sent - accepted;
    return {
      total,
      sent,
      accepted,
      other,
      pending,
      sentPct: Math.round((sent / total) * 100),
      acceptedPct: Math.round((accepted / total) * 100),
      otherPct: Math.round((other / total) * 100),
    };
  }

  const filtered = useMemo(() => {
    if (!campaigns) return [];
    let list = campaigns;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((c) => c.name?.toLowerCase().includes(q) || c.account_name?.toLowerCase().includes(q));
    }
    return [...list].sort((a, b) => {
      if (sortBy === "name") return (a.name ?? "").localeCompare(b.name ?? "");
      if (sortBy === "progress") {
        const pa = getProgress(a.status_counts);
        const pb = getProgress(b.status_counts);
        const pctA = pa.total ? (pa.accepted + pa.sent + pa.other) / pa.total : 0;
        const pctB = pb.total ? (pb.accepted + pb.sent + pb.other) / pb.total : 0;
        return pctB - pctA;
      }
      if (sortBy === "remaining") {
        const pa = getProgress(a.status_counts);
        const pb = getProgress(b.status_counts);
        return pb.pending - pa.pending;
      }
      // last_activity: active first, then by name (no timestamp available client-side)
      if (a.status === "active" && b.status !== "active") return -1;
      if (b.status === "active" && a.status !== "active") return 1;
      return (a.name ?? "").localeCompare(b.name ?? "");
    });
  }, [campaigns, search, sortBy]);

  return (
    <div className="space-y-6">
      <PageHeader title="Campaigns" description="Manage your LinkedIn outreach campaigns">
        <Link href="/campaigns/new">
          <Button>Create Campaign</Button>
        </Link>
      </PageHeader>

      <div className="flex flex-wrap items-center gap-3">
        <Tabs
          defaultValue="all"
          onValueChange={(v) => setStatusFilter(v === "all" ? undefined : v)}
        >
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="active">Active</TabsTrigger>
            <TabsTrigger value="paused">Paused</TabsTrigger>
            <TabsTrigger value="draft">Draft</TabsTrigger>
          </TabsList>
        </Tabs>
        <Input
          placeholder="Search campaigns…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <select
          className="h-9 rounded-md border border-border bg-background px-2 text-sm"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortOption)}
        >
          {(Object.keys(SORT_LABELS) as SortOption[]).map((k) => (
            <option key={k} value={k}>{SORT_LABELS[k]}</option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <p className="text-muted-foreground">{campaigns?.length === 0 ? "No campaigns yet" : "No campaigns match your search"}</p>
            {campaigns?.length === 0 && (
              <Link href="/campaigns/new">
                <Button variant="outline" className="mt-4">Create your first campaign</Button>
              </Link>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <TooltipProvider delayDuration={300}>
            {filtered.map((c) => (
              <CampaignCard
                key={c.id}
                campaign={c}
                getProgress={getProgress}
                onActivate={(campaign) => {
                  if (campaign.status === "paused" && shouldOfferCatchup(campaign)) {
                    setResumeDialogCampaign(campaign);
                  } else {
                    activate.mutate(campaign.id, { onSuccess: () => toast.success("Campaign activated") });
                  }
                }}
                onPause={(id) => pause.mutate(id, { onSuccess: () => toast.success("Campaign paused") })}
                onTimedPause={(id, min) => timedPause.mutate({ id, minutes: min }, { onSuccess: () => toast.success(`Paused for ${min} min — will auto-resume`) })}
              />
            ))}
          </TooltipProvider>
          <AddCard href="/campaigns/new" label="New campaign" />
        </div>
      )}
      {resumeDialogCampaign && (
        <ResumeCampaignDialog
          campaign={resumeDialogCampaign}
          open={!!resumeDialogCampaign}
          onOpenChange={(o) => !o && setResumeDialogCampaign(null)}
        />
      )}
    </div>
  );
}

function CampaignCard({
  campaign: c,
  getProgress,
  onActivate,
  onPause,
  onTimedPause,
}: {
  campaign: Campaign;
  getProgress: (counts: Record<string, number> | null | undefined) => ReturnType<typeof Object>;
  onActivate: (c: Campaign) => void;
  onPause: (id: string) => void;
  onTimedPause: (id: string, minutes: number) => void;
}) {
  const progress = getProgress(c.status_counts) as any;
  const countdown = useCountdown(c.resume_at);
  const isTimedPause = c.status === "paused" && !!c.resume_at && !!countdown;

  return (
    <Link href={`/campaigns/${c.id}`}>
      <Card className="hover:border-muted-foreground/30 transition-colors cursor-pointer">
        <CardContent className="p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-medium truncate">{c.name}</h3>
            <StatusBadge status={c.status} />
          </div>
          <p className="text-xs text-muted-foreground">{c.account_name ?? "—"}</p>
          {isTimedPause ? (
            <p className="text-xs text-amber-400">
              Resuming in {countdown}
            </p>
          ) : c.account_status === "cookie_expired" ? (
            <p className="text-xs text-red-400">
              Cookie expired — update in{" "}
              <Link href={`/accounts/${c.account_id}`} className="underline hover:text-red-300" onClick={(e) => e.stopPropagation()}>
                account settings
              </Link>
            </p>
          ) : c.account_paused_until && new Date(c.account_paused_until) > new Date() ? (
            <p className="text-xs text-amber-400">
              Paused — resumes{" "}
              {new Date(c.account_paused_until.endsWith("Z") ? c.account_paused_until : c.account_paused_until + "Z").toLocaleString(undefined, {
                timeZone: c.account_timezone ?? undefined,
                weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
              })}
            </p>
          ) : null}
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                {progress.accepted + progress.sent + progress.other} / {progress.total} processed
                {progress.pending > 0 && (
                  <span className="ml-1 text-muted-foreground/60">· {progress.pending} left</span>
                )}
              </span>
              <span>{progress.acceptedPct + progress.sentPct + progress.otherPct}%</span>
            </div>
            <div className="flex h-1.5 w-full rounded-full bg-muted overflow-hidden">
              {progress.acceptedPct > 0 && <div className="h-full bg-emerald-500 transition-all" style={{ width: `${progress.acceptedPct}%` }} />}
              {progress.sentPct > 0 && <div className="h-full bg-blue-500 transition-all" style={{ width: `${progress.sentPct}%` }} />}
              {progress.otherPct > 0 && <div className="h-full bg-zinc-500 transition-all" style={{ width: `${progress.otherPct}%` }} />}
            </div>
          </div>
          <div className="flex gap-2 pt-1">
            {(c.status === "draft" || c.status === "paused") ? (
              <Button size="sm" variant="outline" onClick={(e) => {
                e.preventDefault();
                onActivate(c);
              }}>Activate</Button>
            ) : c.status === "active" ? (
              <>
                <Button size="sm" variant="outline" onClick={(e) => {
                  e.preventDefault();
                  onPause(c.id);
                }}>Pause</Button>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" variant="outline" onClick={(e) => {
                      e.preventDefault();
                      onTimedPause(c.id, 60);
                    }}>
                      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mr-1"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                      1h
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Pause for 1 hour, then auto-resume</TooltipContent>
                </Tooltip>
              </>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
