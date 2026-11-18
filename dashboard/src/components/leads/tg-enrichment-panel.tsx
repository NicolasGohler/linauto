"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchTgEnrichmentStatus, startTgEnrichment, type TgEnrichmentStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { toast } from "sonner";
import { Send, RefreshCw } from "lucide-react";

interface TgEnrichmentPanelProps {
  campaignId: string;
}

export function TgEnrichmentPanel({ campaignId }: TgEnrichmentPanelProps) {
  const queryClient = useQueryClient();
  const [triggering, setTriggering] = useState(false);

  const { data: status, isLoading, refetch } = useQuery<TgEnrichmentStatus>({
    queryKey: ["tg-enrichment-status", campaignId],
    queryFn: () => fetchTgEnrichmentStatus(campaignId),
    staleTime: 30_000,
  });

  const trigger = useMutation({
    mutationFn: () => startTgEnrichment(campaignId),
    onMutate: () => setTriggering(true),
    onSuccess: () => {
      toast.success("Enrichment started — leads will be updated in the background");
      queryClient.invalidateQueries({ queryKey: ["tg-enrichment-status", campaignId] });
    },
    onError: (err: Error) => {
      // Show the server message directly (useful for the 501 stub case)
      toast.error(err.message || "Failed to start enrichment");
    },
    onSettled: () => setTriggering(false),
  });

  const coveragePct = status?.coverage_pct ?? 0;
  const barColor =
    coveragePct >= 80 ? "bg-success" :
    coveragePct >= 40 ? "bg-warning" :
    "bg-danger";

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Send className="h-4 w-4 text-info" />
            <CardTitle className="text-base">Telegram Enrichment</CardTitle>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground"
            onClick={() => refetch()}
            disabled={isLoading}
            title="Refresh stats"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
          </Button>
        </div>
        <CardDescription>
          Telegram handles found for leads in this campaign
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="h-12 animate-pulse rounded bg-muted" />
        ) : status ? (
          <>
            {/* Coverage bar */}
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>{status.enriched} enriched</span>
                <span className="font-medium">{coveragePct}%</span>
              </div>
              <div className="h-2 rounded-full bg-muted overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${barColor}`}
                  style={{ width: `${Math.min(coveragePct, 100)}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {status.missing} lead{status.missing !== 1 ? "s" : ""} missing a Telegram handle
                {status.total > 0 ? ` out of ${status.total} total` : ""}
              </p>
            </div>

            {/* Stat pills */}
            <div className="flex gap-3">
              <div className="flex-1 rounded-lg border bg-muted/40 px-3 py-2 text-center">
                <p className="text-lg font-semibold">{status.enriched}</p>
                <p className="text-xs text-muted-foreground">With TG</p>
              </div>
              <div className="flex-1 rounded-lg border bg-muted/40 px-3 py-2 text-center">
                <p className="text-lg font-semibold text-muted-foreground">{status.missing}</p>
                <p className="text-xs text-muted-foreground">Missing</p>
              </div>
            </div>

            {/* Trigger button */}
            {status.missing > 0 && (
              <Button
                className="w-full gap-2"
                onClick={() => trigger.mutate()}
                disabled={triggering || trigger.isPending}
              >
                <Send className="h-3.5 w-3.5" />
                {triggering ? "Starting enrichment…" : `Enrich ${status.missing} missing lead${status.missing !== 1 ? "s" : ""}`}
              </Button>
            )}
            {status.missing === 0 && (
              <p className="text-center text-xs text-success font-medium">
                All leads are enriched ✓
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">No data available</p>
        )}
      </CardContent>
    </Card>
  );
}
