"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const TONES = {
  success: "bg-success-soft text-success border-success-line",
  warning: "bg-warning-soft text-warning border-warning-line",
  info: "bg-info-soft text-info border-info-line",
  neutral: "bg-neutral-soft text-neutral border-neutral-line",
  danger: "bg-danger-soft text-danger border-danger-line",
} as const;

const statusColors: Record<string, string> = {
  active: TONES.success,
  connected: TONES.success,
  completed: TONES.success,
  success: TONES.success,
  paused: TONES.warning,
  scheduled: TONES.info,
  connection_requested: TONES.info,
  pending: TONES.neutral,
  draft: TONES.neutral,
  error: TONES.danger,
  failed: TONES.danger,
  skipped: TONES.neutral,
  invalid: TONES.warning,
  limit_paused: TONES.warning,
  suspended: TONES.danger,
  cookie_expired: TONES.danger,
};

// Display labels — overrides the default underscore→space transform.
const statusLabels: Record<string, string> = {
  connection_requested: "requested",
  limit_paused: "limit paused",
  cookie_expired: "session expired",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("text-[11px] font-medium", statusColors[status] ?? TONES.neutral)}
    >
      {statusLabels[status] ?? status.replace(/_/g, " ")}
    </Badge>
  );
}
