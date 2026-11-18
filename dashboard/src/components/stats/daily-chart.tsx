"use client";

import { useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";
import { useAccountStats } from "@/hooks/use-queries";
import { Skeleton } from "@/components/ui/skeleton";

interface DailyChartProps {
  accountId: string;
}

const TIMELINES = [
  { label: "7D",  days: 7  },
  { label: "30D", days: 30 },
  { label: "90D", days: 90 },
  { label: "All", days: 0  },
];

const COLORS = {
  sent:      "var(--chart-5)", // slate blue
  accepted:  "var(--chart-1)", // forest
  followups: "var(--chart-3)", // ochre
  errors:    "var(--chart-4)", // clay
};

const LEGEND_LABELS: Record<string, string> = {
  sent: "Sent", accepted: "Accepted", followups: "Follow-ups", errors: "Errors",
};

interface TooltipProps {
  active?: boolean;
  payload?: Array<{ name: string; value: number }>;
  label?: string;
}

function CustomTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length || !label) return null;

  const get = (name: string) => payload.find(p => p.name === name)?.value ?? 0;
  const sent      = get("sent");
  const accepted  = get("accepted");
  const followups = get("followups");
  const errors    = get("errors");
  const rate      = sent > 0 ? Math.round((accepted / sent) * 100) : 0;

  const [month, day] = label.split("-");
  const displayLabel = new Date(2000, Number(month) - 1, Number(day))
    .toLocaleDateString("en-US", { month: "short", day: "numeric" });

  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3 shadow-lg text-xs space-y-1.5 min-w-[160px]">
      <p className="font-semibold text-foreground text-sm">{displayLabel}</p>
      <div className="space-y-1">
        <TooltipRow color={COLORS.sent}      label="Sent"       value={sent} />
        <TooltipRow color={COLORS.accepted}  label="Accepted"   value={accepted} extra={sent > 0 ? `${rate}% rate` : undefined} />
        {followups > 0 && <TooltipRow color={COLORS.followups} label="Follow-ups" value={followups} />}
        {errors > 0    && <TooltipRow color={COLORS.errors}    label="Errors"     value={errors} />}
      </div>
    </div>
  );
}

function TooltipRow({ color, label, value, extra }: { color: string; label: string; value: number; extra?: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="flex items-center gap-1.5">
        <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
        <span className="text-muted-foreground">{label}</span>
      </div>
      <div className="flex items-center gap-1.5 font-medium tabular-nums">
        <span>{value}</span>
        {extra && <span className="text-muted-foreground font-normal">({extra})</span>}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-64 flex-col items-center justify-center gap-3 text-muted-foreground">
      <svg className="h-10 w-10 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
      </svg>
      <p className="text-sm">No activity in this period</p>
    </div>
  );
}

export function DailyChart({ accountId }: DailyChartProps) {
  const [selected, setSelected] = useState(1); // default 30D
  const tl = TIMELINES[selected];
  const { data: stats, isLoading } = useAccountStats(accountId, tl.days);

  const chartData = (stats ?? []).map((d) => ({
    date:      d.date.slice(5),
    sent:      d.connection_requests_sent,
    accepted:  d.connections_accepted,
    followups: d.followup_messages_sent,
    errors:    d.errors,
  }));

  const hasData      = chartData.some(d => d.sent > 0 || d.accepted > 0);
  const hasFollowups = chartData.some(d => d.followups > 0);

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="flex rounded-md border border-border overflow-hidden text-xs">
          {TIMELINES.map((t, i) => (
            <button
              key={t.label}
              onClick={() => setSelected(i)}
              className={[
                "px-3 py-1.5 font-medium transition-colors",
                i === selected
                  ? "bg-primary text-primary-foreground border-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted",
                i > 0 ? "border-l border-border" : "",
              ].join(" ")}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : !hasData ? (
        <EmptyState />
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              width={28}
              allowDecimals={false}
            />
            <Tooltip
              content={(props) => (
                <CustomTooltip
                  active={props.active}
                  payload={props.payload as TooltipProps["payload"]}
                  label={props.label as string | undefined}
                />
              )}
              cursor={{ fill: "var(--muted)", opacity: 0.4 }}
            />
            <Legend
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
              formatter={(value) => (
                <span style={{ color: "var(--muted-foreground)" }}>
                  {LEGEND_LABELS[value] ?? value}
                </span>
              )}
            />
            <Bar dataKey="sent"     fill={COLORS.sent}     radius={[3,3,0,0]} maxBarSize={32} name="sent"
              activeBar={{ fillOpacity: 0.75 }} />
            <Bar dataKey="accepted" fill={COLORS.accepted} radius={[3,3,0,0]} maxBarSize={32} name="accepted"
              activeBar={{ fillOpacity: 0.75 }} />
            {hasFollowups && (
              <Bar dataKey="followups" fill={COLORS.followups} radius={[3,3,0,0]} maxBarSize={32} name="followups"
                activeBar={{ fillOpacity: 0.75 }} />
            )}
            <Bar dataKey="errors"   fill={COLORS.errors}   radius={[3,3,0,0]} maxBarSize={32} name="errors"
              activeBar={{ fillOpacity: 0.75 }} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
