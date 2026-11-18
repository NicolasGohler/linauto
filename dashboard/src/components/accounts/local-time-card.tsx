"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";

interface LocalTimeCardProps {
  timezone: string | null;
  workStartHour: number | null;
  workEndHour: number | null;
  weekendEnabled?: boolean;
}

/**
 * Shows the account's current local time and whether it's currently inside its
 * scheduler send window. Updates every second so the user can see it tick.
 *
 * Window logic mirrors the scheduler (`runner.py`):
 *   - inside window  = work_start_hour <= local_hour < work_end_hour AND
 *                      (weekend_enabled OR Mon–Fri)
 *   - otherwise      = scheduler won't dispatch new leads right now
 *
 * `weekendEnabled` is a per-campaign flag, not per-account. We don't know it
 * at the account level, so the card just notes whether today is a weekend
 * and lets the user reconcile with their campaign settings.
 */
export function LocalTimeCard({
  timezone,
  workStartHour,
  workEndHour,
}: LocalTimeCardProps) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const tz = timezone ?? undefined;

  // Format local time string for the account's timezone.
  const timeStr = new Intl.DateTimeFormat(undefined, {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(now);

  const dayStr = new Intl.DateTimeFormat(undefined, {
    timeZone: tz,
    weekday: "long",
    month: "short",
    day: "numeric",
  }).format(now);

  // Extract hour + weekday in the target timezone to compute window state
  // without having to parse string output.
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour: "numeric",
    weekday: "short",
    hour12: false,
  }).formatToParts(now);
  const hourPart = parts.find((p) => p.type === "hour")?.value;
  const weekdayPart = parts.find((p) => p.type === "weekday")?.value;
  const localHour = hourPart != null ? Number(hourPart) : null;
  // JS getDay() semantics: 0=Sun .. 6=Sat. formatToParts gives "Sat"/"Sun" etc.
  const isWeekend = weekdayPart === "Sat" || weekdayPart === "Sun";

  let windowState: "in" | "before" | "after" | "weekend" | "unknown" = "unknown";
  let nextOpenHint: string | null = null;
  if (
    localHour != null &&
    workStartHour != null &&
    workEndHour != null
  ) {
    if (isWeekend) {
      windowState = "weekend";
      nextOpenHint = `Monday ${String(workStartHour).padStart(2, "0")}:00 local`;
    } else if (localHour < workStartHour) {
      windowState = "before";
      nextOpenHint = `Today ${String(workStartHour).padStart(2, "0")}:00 local`;
    } else if (localHour >= workEndHour) {
      windowState = "after";
      nextOpenHint = `Tomorrow ${String(workStartHour).padStart(2, "0")}:00 local`;
    } else {
      windowState = "in";
    }
  }

  const badge = (() => {
    switch (windowState) {
      case "in":
        return {
          label: "In work window",
          className: "bg-success-soft text-success border-success-line",
        };
      case "before":
        return {
          label: "Before work start",
          className: "bg-neutral-soft text-neutral border-neutral-line",
        };
      case "after":
        return {
          label: "After work end",
          className: "bg-neutral-soft text-neutral border-neutral-line",
        };
      case "weekend":
        return {
          label: "Weekend",
          className: "bg-warning-soft text-warning border-warning-line",
        };
      default:
        return {
          label: "Timezone not set",
          className: "bg-danger-soft text-danger border-danger-line",
        };
    }
  })();

  return (
    <Card>
      <CardContent className="pt-6 space-y-2">
        <div className="flex items-baseline justify-between gap-3">
          <div>
            <p className="text-xs text-muted-foreground">Local time</p>
            <p className="mt-0.5 text-2xl font-semibold tabular-nums">
              {timeStr}
            </p>
            <p className="text-xs text-muted-foreground">
              {dayStr}
              {timezone ? ` · ${timezone}` : ""}
            </p>
          </div>
          <span
            className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${badge.className}`}
          >
            {badge.label}
          </span>
        </div>
        {workStartHour != null && workEndHour != null && (
          <p className="text-xs text-muted-foreground">
            Send window: {String(workStartHour).padStart(2, "0")}:00 –{" "}
            {String(workEndHour).padStart(2, "0")}:00 local · Mon–Fri
            {nextOpenHint && windowState !== "in" && (
              <span className="block mt-0.5">Opens again: {nextOpenHint}</span>
            )}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
