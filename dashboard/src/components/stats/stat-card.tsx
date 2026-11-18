import { Card, CardContent } from "@/components/ui/card";

const BADGE_STYLES: Record<string, string> = {
  green: "bg-success-soft text-success border border-success-line",
  amber: "bg-warning-soft text-warning border border-warning-line",
  red:   "bg-danger-soft text-danger border border-danger-line",
  blue:  "bg-info-soft text-info border border-info-line",
};

interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
  badge?: string;
  badgeColor?: "green" | "amber" | "red" | "blue";
}

export function StatCard({ label, value, sub, badge, badgeColor = "green" }: StatCardProps) {
  return (
    <Card>
      <CardContent className="px-5 py-4">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <div className="flex items-baseline gap-2 mt-2">
          <p className="text-[28px] leading-none font-semibold tracking-display tabular-nums">{value}</p>
          {badge && (
            <span className={`inline-block rounded-full px-1.5 py-0.5 text-[11px] font-medium leading-none ${BADGE_STYLES[badgeColor]}`}>
              {badge}
            </span>
          )}
        </div>
        {sub && <p className="text-xs text-muted-foreground mt-1.5">{sub}</p>}
      </CardContent>
    </Card>
  );
}
