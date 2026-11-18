"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import {
  useBroadcasts,
  useActivateBroadcast,
  usePauseBroadcast,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { AddCard } from "@/components/add-card";
import { toast } from "sonner";
import { Mail } from "lucide-react";

export default function BroadcastsPage() {
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [search, setSearch] = useState("");
  const { data: broadcasts, isLoading } = useBroadcasts({ status: statusFilter });
  const activate = useActivateBroadcast();
  const pause = usePauseBroadcast();

  const filtered = useMemo(() => {
    if (!broadcasts) return [];
    let list = broadcasts;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (b) =>
          b.name?.toLowerCase().includes(q) ||
          b.account_name?.toLowerCase().includes(q) ||
          b.source_list_name?.toLowerCase().includes(q)
      );
    }
    return [...list].sort((a, b) => {
      // Active first, then draft, then paused, then completed
      const order: Record<string, number> = {
        active: 0, draft: 1, paused: 2, completed: 3,
      };
      const oa = order[a.status] ?? 9;
      const ob = order[b.status] ?? 9;
      if (oa !== ob) return oa - ob;
      return (a.name ?? "").localeCompare(b.name ?? "");
    });
  }, [broadcasts, search]);

  function getProgress(b: import("@/lib/types").Broadcast) {
    const total = b.total_leads || 0;
    const counts = b.status_counts ?? {};
    const sent = counts["sent"] ?? 0;
    const complete = counts["sequence_complete"] ?? 0;
    const skipped = counts["skipped"] ?? 0;
    const errored = counts["error"] ?? 0;
    const pending = counts["pending"] ?? 0;
    const done = complete + skipped + errored + sent;
    const donePct = total ? Math.round((done / total) * 100) : 0;
    const completePct = total ? Math.round((complete / total) * 100) : 0;
    const sentPct = total ? Math.round((sent / total) * 100) : 0;
    const skippedPct = total ? Math.round((skipped / total) * 100) : 0;
    return { total, sent, complete, skipped, errored, pending, done, donePct, completePct, sentPct, skippedPct };
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Broadcasts"
        description="Send message sequences to your 1st-degree connections"
      >
        <Link href="/broadcasts/new">
          <Button>Create Broadcast</Button>
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
            <TabsTrigger value="draft">Draft</TabsTrigger>
            <TabsTrigger value="paused">Paused</TabsTrigger>
            <TabsTrigger value="completed">Done</TabsTrigger>
          </TabsList>
        </Tabs>
        <Input
          placeholder="Search broadcasts…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
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
            <Mail className="mb-3 h-8 w-8 text-muted-foreground/50" />
            <p className="text-muted-foreground">
              {broadcasts?.length === 0
                ? "No broadcasts yet. Send a message sequence to your 1st-degree connections."
                : "No broadcasts match your search"}
            </p>
            {broadcasts?.length === 0 && (
              <Link href="/broadcasts/new">
                <Button variant="outline" className="mt-4">
                  Create your first broadcast
                </Button>
              </Link>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((b) => {
            const p = getProgress(b);
            return (
              <Link key={b.id} href={`/broadcasts/${b.id}`}>
                <Card className="hover:border-muted-foreground/30 transition-colors cursor-pointer h-full">
                  <CardContent className="p-5 space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="font-medium truncate">{b.name}</h3>
                      <StatusBadge status={b.status} />
                    </div>
                    <p className="text-xs text-muted-foreground truncate">
                      {b.account_name ?? "—"}
                      {b.source_list_name && (
                        <span className="text-muted-foreground/60"> · from {b.source_list_name}</span>
                      )}
                    </p>
                    <div className="space-y-1">
                      <div className="flex justify-between text-xs text-muted-foreground">
                        <span>
                          {p.done} / {p.total} processed
                          {p.pending > 0 && (
                            <span className="ml-1 text-muted-foreground/60">· {p.pending} left</span>
                          )}
                        </span>
                        <span>{p.donePct}%</span>
                      </div>
                      <div className="flex h-2 w-full gap-px rounded-full bg-muted overflow-hidden">
                        {p.completePct > 0 && (
                          <div className="h-full bg-progress-done" style={{ width: `${p.completePct}%` }} />
                        )}
                        {p.sentPct > 0 && (
                          <div className="h-full bg-progress-active" style={{ width: `${p.sentPct}%` }} />
                        )}
                        {p.skippedPct > 0 && (
                          <div className="h-full bg-progress-other" style={{ width: `${p.skippedPct}%` }} />
                        )}
                      </div>
                    </div>
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>{b.messages_sent ?? 0} messages sent</span>
                      {b.message_2 || b.message_3 ? (
                        <span className="text-muted-foreground/60">
                          {[b.message_1, b.message_2, b.message_3].filter(Boolean).length}-step
                        </span>
                      ) : (
                        <span className="text-muted-foreground/60">Single message</span>
                      )}
                    </div>
                    <div className="flex gap-2 pt-1">
                      {b.status === "draft" || b.status === "paused" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={(e) => {
                            e.preventDefault();
                            activate.mutate(b.id, {
                              onSuccess: () => toast.success("Broadcast activated"),
                              onError: (err: Error) => toast.error(err.message),
                            });
                          }}
                        >
                          Activate
                        </Button>
                      ) : b.status === "active" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={(e) => {
                            e.preventDefault();
                            pause.mutate(b.id, {
                              onSuccess: () => toast.success("Broadcast paused"),
                            });
                          }}
                        >
                          Pause
                        </Button>
                      ) : null}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
          <AddCard href="/broadcasts/new" label="New broadcast" />
        </div>
      )}
    </div>
  );
}
