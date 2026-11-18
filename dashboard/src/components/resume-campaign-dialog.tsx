"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
  useActivateCampaign,
  useStartAcceptanceCatchup,
  useAcceptanceCatchupStatus,
} from "@/hooks/use-queries";
import type { Campaign } from "@/lib/types";

// Threshold (hours) above which we offer the catchup. Matches the daily
// checker's 72h window — pauses shorter than this are already covered by the
// normal once-daily run.
const CATCHUP_THRESHOLD_HOURS = 72;

// Above this (~14 days) we require an extra confirmation step (safety #6).
const LONG_PAUSE_THRESHOLD_HOURS = 14 * 24;

type Props = {
  campaign: Campaign;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

function formatDuration(hours: number): string {
  if (hours < 24) return `${Math.round(hours)} hours`;
  const days = hours / 24;
  if (days < 14) return `${Math.round(days)} days`;
  return `${Math.round(days)} days`;
}

export function ResumeCampaignDialog({ campaign, open, onOpenChange }: Props) {
  const activate = useActivateCampaign();
  const startCatchup = useStartAcceptanceCatchup();
  const [taskId, setTaskId] = useState<string | null>(null);
  const [confirmedLongPause, setConfirmedLongPause] = useState(false);
  const status = useAcceptanceCatchupStatus(campaign.id, taskId);

  // Reset state when dialog opens/closes
  useEffect(() => {
    if (!open) {
      setTaskId(null);
      setConfirmedLongPause(false);
    }
  }, [open]);

  const hoursPaused = campaign.paused_at
    ? (Date.now() -
        new Date(
          campaign.paused_at.endsWith("Z") ? campaign.paused_at : campaign.paused_at + "Z",
        ).getTime()) /
      (1000 * 60 * 60)
    : null;

  const isLongPause = hoursPaused !== null && hoursPaused > LONG_PAUSE_THRESHOLD_HOURS;
  const needsLongPauseConfirm = isLongPause && !confirmedLongPause;

  // Watch task status and surface completion
  useEffect(() => {
    if (!status.data) return;
    if (status.data.status === "done") {
      const { accepted_count, scanned_slugs, hit_iteration_cap } = status.data;
      toast.success(
        `Catchup complete: ${accepted_count} acceptance${accepted_count === 1 ? "" : "s"} found (scanned ${scanned_slugs} connections)`,
      );
      if (hit_iteration_cap) {
        toast.warning(
          "Reached scroll iteration cap — older acceptances may not have been scanned.",
        );
      }
      onOpenChange(false);
    } else if (status.data.status === "error") {
      toast.error(`Catchup failed: ${status.data.error ?? "unknown error"}`);
      setTaskId(null);
    }
  }, [status.data, onOpenChange]);

  const handleResumeWithCatchup = async () => {
    try {
      await activate.mutateAsync(campaign.id);
      const { task_id } = await startCatchup.mutateAsync({
        campaignId: campaign.id,
      });
      setTaskId(task_id);
      toast.info("Catchup scan started — this may take a few minutes.");
    } catch (e) {
      toast.error(`Failed to start catchup: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleResumeOnly = async () => {
    try {
      await activate.mutateAsync(campaign.id);
      toast.success("Campaign resumed");
      onOpenChange(false);
    } catch (e) {
      toast.error(`Failed to resume: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const isRunning = !!taskId && status.data?.status === "running";
  const pendingResume = activate.isPending || startCatchup.isPending;

  return (
    <Dialog open={open} onOpenChange={(o) => !isRunning && onOpenChange(o)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Resume campaign</DialogTitle>
          <DialogDescription>
            {hoursPaused === null ? (
              <>
                This campaign was paused, but the pause start time isn&apos;t recorded.
                Connections accepted during the pause may not be caught by the normal
                daily checker (72h window). Want to run a one-time catchup scan
                covering the last 30 days?
              </>
            ) : (
              <>
                This campaign was paused for{" "}
                <strong>{formatDuration(hoursPaused)}</strong>. Connections accepted
                during that window won&apos;t be caught by the normal daily checker,
                which only scans the last 72h. Want to run a one-time catchup scan
                now?
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        {isRunning && (
          <div className="rounded-md border bg-muted/40 p-3 text-sm">
            <div className="font-medium">Catchup running…</div>
            <div className="text-muted-foreground">
              Scanning the connections page. This page will close automatically when
              it&apos;s done. You can close this dialog — the scan continues in the
              background.
            </div>
          </div>
        )}

        {needsLongPauseConfirm && !isRunning && (
          <div className="rounded-md border border-warning-line bg-warning-soft p-3 text-sm text-warning">
            <div className="font-medium">Long pause detected</div>
            <div className="mt-1">
              This pause is longer than 14 days. A long catchup scan loads many pages
              on LinkedIn, which adds to the account&apos;s footprint. Confirm to
              proceed.
            </div>
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => setConfirmedLongPause(true)}
            >
              I understand — enable catchup
            </Button>
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={isRunning}
          >
            Cancel
          </Button>
          <Button
            variant="outline"
            onClick={handleResumeOnly}
            disabled={pendingResume || isRunning}
          >
            Resume only
          </Button>
          <Button
            onClick={handleResumeWithCatchup}
            disabled={pendingResume || isRunning || needsLongPauseConfirm}
          >
            {isRunning ? "Catchup running…" : "Resume & run catchup"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Returns true if a resume action on this campaign should show the catchup
 * dialog (i.e. paused for longer than the daily checker's window).
 * Returns false for short pauses — caller should resume directly.
 */
export function shouldOfferCatchup(campaign: Campaign): boolean {
  if (!campaign.paused_at) return false;
  const pausedAtIso = campaign.paused_at.endsWith("Z")
    ? campaign.paused_at
    : campaign.paused_at + "Z";
  const hours = (Date.now() - new Date(pausedAtIso).getTime()) / (1000 * 60 * 60);
  return hours > CATCHUP_THRESHOLD_HOURS;
}
