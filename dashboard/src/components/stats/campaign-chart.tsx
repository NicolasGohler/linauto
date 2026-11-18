"use client";

import { useState, useMemo } from "react";
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { useCampaignStats } from "@/hooks/use-queries";
import { Skeleton } from "@/components/ui/skeleton";

const TIMELINES = [
  { label: "24H", days: 1, granularity: "hour" as const },
  { label: "7D",  days: 7, granularity: "day"  as const },
  { label: "30D", days: 30, granularity: "day"  as const },
  { label: "All", days: 0, granularity: "day"  as const },
];

function formatBucket(raw: string, granularity: "day" | "hour"): string {
  if (granularity === "hour") {
    // "2026-03-24T17:00" → "17:00"
    return raw.slice(11, 16);
  }
  // "2026-03-24" → "Mar 24"
  const d = new Date(raw + "T12:00:00Z");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
  granularity: "day" | "hour";
}

function CustomTooltip({ active, payload, label, granularity }: CustomTooltipProps) {
  if (!active || !payload?.length || !label) return null;

  const displayLabel = granularity === "hour"
    ? `Today ${label.slice(11, 16)}`
    : (() => {
        const d = new Date(label + "T12:00:00Z");
        return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
      })();

  const sent     = payload.find(p => p.name === "Sent")?.value ?? 0;
  const accepted = payload.find(p => p.name === "Accepted")?.value ?? 0;
  const pending  = payload.find(p => p.name === "Pending")?.value ?? 0;
  const errors   = payload.find(p => p.name === "Errors")?.value ?? 0;
  const rate     = sent > 0 ? Math.round((accepted / sent) * 100) : 0;

  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3 shadow-lg text-xs space-y-1.5 min-w-[160px]">
      <p className="font-semibold text-foreground text-sm">{displayLabel}</p>
      <div className="space-y-1">
        <Row color="var(--chart-5)" label="Sent"     value={sent} />
        <Row color="var(--chart-1)" label="Accepted" value={accepted} extra={sent > 0 ? `${rate}% rate` : undefined} />
        {errors > 0 && <Row color="var(--chart-4)" label="Errors" value={errors} />}
        <div className="border-t border-border my-1" />
        <Row color="var(--chart-3)" label="Pending"  value={pending} />
      </div>
    </div>
  );
}

function Row({ color, label, value, extra }: { color: string; label: string; value: number; extra?: string }) {
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

interface CampaignActivityChartProps {
  campaignId: string;
  totalLeads: number;
}

export function CampaignActivityChart({ campaignId, totalLeads }: CampaignActivityChartProps) {
  const [selected, setSelected] = useState(1); // index into TIMELINES, default 7D
  const tl = TIMELINES[selected];

  const { data, isLoading } = useCampaignStats(campaignId, tl.days, tl.granularity);

  const chartData = useMemo(() => {
    if (!data?.daily) return [];
    let cumSent = 0;
    return data.daily.map(d => {
      cumSent += d.sent;
      return {
        raw: d.date,
        label: formatBucket(d.date, tl.granularity),
        sent: d.sent,
        accepted: d.accepted,
        errors: d.errors,
        pending: Math.max(0, totalLeads - cumSent),
      };
    });
  }, [data, tl.granularity, totalLeads]);

  const hasData = chartData.length > 0 && chartData.some(d => d.sent > 0 || d.accepted > 0);

  return (
    <div className="space-y-3">
      {/* Timeline selector */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">Connections requested &amp; accepted per {tl.granularity === "hour" ? "hour" : "day"}</p>
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

      {/* Chart */}
      {isLoading ? (
        <Skeleton className="h-72 w-full" />
      ) : !hasData ? (
        <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
          No activity in this period
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={288}>
          <ComposedChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              interval="preserveStartEnd"
            />
            {/* Left axis: sent / accepted / errors */}
            <YAxis
              yAxisId="left"
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              width={28}
              allowDecimals={false}
            />
            {/* Right axis: pending (larger scale) */}
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              width={42}
              allowDecimals={false}
            />
            <Tooltip
              content={(props) => (
                <CustomTooltip
                  active={props.active}
                  payload={props.payload as CustomTooltipProps["payload"]}
                  label={props.payload?.[0]?.payload?.raw}
                  granularity={tl.granularity}
                />
              )}
              cursor={{ fill: "var(--muted)", opacity: 0.4 }}
            />
            <Legend
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
              formatter={(value) => (
                <span style={{ color: "var(--muted-foreground)" }}>{value}</span>
              )}
            />
            <Bar yAxisId="left" dataKey="sent"     name="Sent"     fill="#3b82f6" radius={[3,3,0,0]} maxBarSize={32} activeBar={{ fillOpacity: 0.75 }} />
            <Bar yAxisId="left" dataKey="accepted" name="Accepted" fill="#22c55e" radius={[3,3,0,0]} maxBarSize={32} activeBar={{ fillOpacity: 0.75 }} />
            <Bar yAxisId="left" dataKey="errors"   name="Errors"   fill="#ef4444" radius={[3,3,0,0]} maxBarSize={32} activeBar={{ fillOpacity: 0.75 }} />
            <Line
              yAxisId="right"
              dataKey="pending"
              name="Pending"
              stroke="#f59e0b"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
