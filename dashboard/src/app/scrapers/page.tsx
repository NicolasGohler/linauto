"use client";

import { useEffect, useRef, useState } from "react";
import { Globe, RefreshCw, CheckCircle2, AlertCircle, Clock, Terminal, Search, Minus, Play } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useScrapers } from "@/hooks/use-queries";
import * as api from "@/lib/api";
import type { ScraperStatus, TgSweepStatus, TgSweepResult } from "@/lib/types";

const SITE_LABELS: Record<string, string> = {
  cryptorank: "CryptoRank",
  rootdata: "RootData",
  frontrun: "Frontrun",
};

const SITE_DESCRIPTIONS: Record<string, string> = {
  cryptorank: "Crypto fundraising rounds from cryptorank.io",
  rootdata: "Web3 project data from rootdata.com",
  frontrun: "VC signal feed from frontrun.vc",
};

function cookieAge(status: ScraperStatus): {
  label: string;
  color: "green" | "yellow" | "red";
} {
  if (!status.has_cookies || status.age_hours === null) {
    return { label: "No cookies yet", color: "red" };
  }
  const h = status.age_hours;
  if (h < 72) {
    const d = Math.floor(h / 24);
    const hrs = Math.round(h % 24);
    const label =
      d > 0
        ? `Fresh (${d}d ${hrs}h ago)`
        : `Fresh (${Math.round(h)}h ago)`;
    return { label, color: "green" };
  }
  if (h < 120) {
    const d = Math.floor(h / 24);
    return { label: `Stale (${d} days ago)`, color: "yellow" };
  }
  const d = Math.floor(h / 24);
  return { label: `Old (${d} days ago)`, color: "red" };
}

const colorClasses = {
  green: {
    badge: "bg-success-soft text-success border-success-line",
    icon: "text-success",
  },
  yellow: {
    badge: "bg-warning-soft text-warning border-warning-line",
    icon: "text-warning",
  },
  red: {
    badge: "bg-danger-soft text-danger border-danger-line",
    icon: "text-danger",
  },
};

function ScraperCard({ status }: { status: ScraperStatus }) {
  const qc = useQueryClient();
  const [sessionActive, setSessionActive] = useState(false);
  const [loading, setLoading] = useState(false);

  const site = status.site;
  const label = SITE_LABELS[site] ?? site;
  const description = SITE_DESCRIPTIONS[site] ?? "";
  const { label: ageLabel, color } = cookieAge(status);
  const Icon =
    color === "green" ? CheckCircle2 : color === "yellow" ? Clock : AlertCircle;

  const handleRefresh = async () => {
    const win = window.open("about:blank", "_blank");
    setLoading(true);
    try {
      const res = await api.startScraperLoginSession(site);
      setSessionActive(true);
      const novncBase =
        process.env.NEXT_PUBLIC_NOVNC_URL ||
        `http://${window.location.hostname}:6080`;
      const title = encodeURIComponent(`${label} Login`);
      const url = `${novncBase}${res.novnc_url}&title=${title}`;
      if (win) {
        win.location.href = url;
      } else {
        window.open(url, "_blank");
      }
    } catch (err: unknown) {
      win?.close();
      toast.error(err instanceof Error ? err.message : "Failed to start session");
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    try {
      const res = await api.finishScraperLoginSession(site);
      toast.success(`Saved ${res.cookie_count} cookies for ${label}`);
      qc.invalidateQueries({ queryKey: ["scrapers"] });
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to save cookies");
    } finally {
      setSessionActive(false);
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    try {
      await api.cancelScraperLoginSession(site);
    } catch {
      // ignore
    }
    setSessionActive(false);
  };

  return (
    <div className="rounded-lg border border-border bg-card p-6 space-y-4">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-muted">
            <Globe className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-tight">{label}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
          </div>
        </div>
      </div>

      <div
        className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 w-fit text-xs font-medium ${colorClasses[color].badge}`}
      >
        <Icon className={`h-3.5 w-3.5 ${colorClasses[color].icon}`} />
        {ageLabel}
      </div>

      {!sessionActive ? (
        <Button
          size="sm"
          variant="outline"
          className="w-full"
          onClick={handleRefresh}
          disabled={loading}
        >
          <RefreshCw className="mr-2 h-3.5 w-3.5" />
          {loading ? "Starting..." : "Refresh Login"}
        </Button>
      ) : (
        <div className="rounded-md border border-success-line bg-success-soft/70 p-3 space-y-2.5">
          <p className="text-xs font-medium text-success">
            Browser open — log in, then save cookies.
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSave} disabled={loading}>
              {loading ? "Saving..." : "Save Cookies"}
            </Button>
            <Button size="sm" variant="ghost" onClick={handleCancel}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Relative time helper ─────────────────────────────────────────────────

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso.endsWith("Z") ? iso : iso + "Z").getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// ── Fundraising Agent Panel ──────────────────────────────────────────────

type RunStatus = {
  running: boolean;
  started_at?: string;
  finished_at?: string;
  exit?: string;
  error?: string;
};

// A run is "stale running" if started_at is > 4 hours ago but still flagged running
// (happens when systemd kills the process before it can write the final status)
function isStale(status: RunStatus): boolean {
  if (!status.running || !status.started_at) return false;
  return Date.now() - new Date(status.started_at).getTime() > 4 * 60 * 60 * 1000;
}

function nextMonday(): string {
  const now = new Date();
  const day = now.getUTCDay(); // 0=Sun 1=Mon ... 6=Sat
  const daysUntil = day === 1 ? 7 : (8 - day) % 7;
  const next = new Date(now);
  next.setUTCDate(now.getUTCDate() + daysUntil);
  next.setUTCHours(9, 0, 0, 0);
  return next.toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function FundraisingRunPanel() {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [totalLines, setTotalLines] = useState(0);
  const [logOpen, setLogOpen] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = async () => {
    try {
      const [st, log] = await Promise.all([
        api.fetchFundraisingRunStatus(),
        api.fetchFundraisingRunLog(300),
      ]);
      setStatus(st);
      setLines(log.lines);
      setTotalLines(log.total_lines);
    } catch {
      // silently ignore — server may be unreachable
    }
  };

  const livelyRunning = !!(status?.running && !isStale(status));

  const handleTrigger = async () => {
    if (triggering || livelyRunning) return;
    setTriggering(true);
    try {
      await api.triggerFundraisingRun();
      toast.success("Run triggered — agent will start within a minute");
      await fetchData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(msg.includes("409") ? "Agent is already running" : `Failed to trigger: ${msg}`);
    } finally {
      setTriggering(false);
    }
  };

  // Auto-scroll to bottom when log is open and new lines arrive
  useEffect(() => {
    if (logOpen) {
      const el = logRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }
  }, [lines, logOpen]);

  useEffect(() => {
    fetchData();
    const schedule = () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = setInterval(async () => {
        await fetchData();
        schedule();
      }, livelyRunning ? 4000 : 60000);
    };
    schedule();
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [livelyRunning]);

  type BadgeVariant = "blue" | "green" | "red" | "yellow" | "gray";
  const stale = status ? isStale(status) : false;
  const effectiveExit = stale ? "stale" : status?.exit;

  const badgeVariant: BadgeVariant =
    livelyRunning ? "blue"
    : effectiveExit === "ok" ? "green"
    : effectiveExit === "error" ? "red"
    : effectiveExit === "timeout" || effectiveExit === "stale" ? "yellow"
    : "gray";

  const badgeLabel =
    livelyRunning ? "Running"
    : effectiveExit === "ok" ? "Completed"
    : effectiveExit === "error" ? "Failed"
    : effectiveExit === "timeout" ? "Timed out"
    : effectiveExit === "stale" ? "Stale"
    : status ? "Idle" : "Unknown";

  const badgeClasses: Record<BadgeVariant, string> = {
    blue: "bg-info-soft text-info border-info-line",
    green: "bg-success-soft text-success border-success-line",
    red: "bg-danger-soft text-danger border-danger-line",
    yellow: "bg-warning-soft text-warning border-warning-line",
    gray: "bg-muted text-muted-foreground border-border",
  };

  return (
    <div className="rounded-lg border border-border bg-card p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-muted">
            <Terminal className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-tight">Fundraising Agent</h3>
            <p className="text-xs text-muted-foreground mt-0.5">Weekly — Monday 09:00 UTC · CryptoRank + RootData + Apollo</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleTrigger}
            disabled={triggering || livelyRunning}
            title={livelyRunning ? "Already running" : "Run now"}
            className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:border-foreground/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {triggering ? (
              <RefreshCw className="h-3 w-3 animate-spin" />
            ) : (
              <Play className="h-3 w-3" />
            )}
            Run now
          </button>
          <button onClick={fetchData} className="text-xs text-muted-foreground hover:text-foreground transition-colors" title="Refresh">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Status badge + last-run timestamps */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium ${badgeClasses[badgeVariant]}`}>
          {livelyRunning && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-info opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-info" />
            </span>
          )}
          {badgeLabel}
        </span>
        {status?.started_at && (
          <span className="text-xs text-muted-foreground">Last run {relTime(status.started_at)}</span>
        )}
        {status?.finished_at && !livelyRunning && (
          <span className="text-xs text-muted-foreground">· finished {relTime(status.finished_at)}</span>
        )}
      </div>

      {/* Next run estimate */}
      {!livelyRunning && (
        <p className="text-xs text-muted-foreground">Next run: {nextMonday()}</p>
      )}

      {/* Error / timeout / stale banner */}
      {(effectiveExit === "error" || effectiveExit === "timeout" || effectiveExit === "stale") && status?.error && (
        <div className={`rounded-md border px-3 py-2 text-xs ${
          effectiveExit === "error"
            ? "border-danger-line bg-danger-soft/70 text-danger"
            : "border-warning-line bg-warning-soft/70 text-warning"
        }`}>
          {status.error}
        </div>
      )}

      {/* Collapsible log */}
      {lines.length > 0 ? (
        <div className="space-y-1.5">
          <button
            onClick={() => setLogOpen(o => !o)}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <Terminal className="h-3 w-3" />
            {logOpen ? "Hide" : "Show"} output log
            {totalLines > 0 && (
              <span className="text-muted-foreground/60">({totalLines.toLocaleString()} lines)</span>
            )}
          </button>
          {logOpen && (
            <div
              ref={logRef}
              className="rounded-md bg-black/60 border border-border p-3 h-72 overflow-y-auto font-mono text-xs leading-relaxed"
            >
              {lines.map((line, i) => (
                <div key={i} className={
                  line.includes("ERROR") || line.includes("FATAL") || line.includes("⚠")
                    ? "text-danger"
                    : line.includes("✓") || line.includes("success") || line.includes("complete")
                    ? "text-success"
                    : line.includes("Rate limit") || line.includes("waiting") || line.includes("⏳")
                    ? "text-warning"
                    : "text-success/80"
                }>
                  {line || " "}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          No run log yet — logs appear here after the first Monday run.
        </p>
      )}
    </div>
  );
}

function formatFloodWait(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m remaining`;
  if (m > 0) return `${m}m ${s}s remaining`;
  return `${s}s remaining`;
}

// ── Single result row ────────────────────────────────────────────────────

function SweepResultRow({ item }: { item: TgSweepResult }) {
  return (
    <div className="flex items-center gap-3 py-2 border-b border-border/40 last:border-0">
      <div className={`shrink-0 ${item.found ? "text-success" : "text-muted-foreground/40"}`}>
        {item.found
          ? <Search className="h-3.5 w-3.5" />
          : <Minus className="h-3.5 w-3.5" />
        }
      </div>
      <div className="flex-1 min-w-0">
        <span className="text-xs font-medium truncate block">
          {item.lead_name || "—"}
          {item.company && (
            <span className="text-muted-foreground font-normal"> · {item.company}</span>
          )}
        </span>
      </div>
      <div className="shrink-0 text-right">
        {item.found && item.telegram_username ? (
          <span className="text-xs text-success font-mono">@{item.telegram_username}</span>
        ) : (
          <span className="text-xs text-muted-foreground/50">Not found</span>
        )}
      </div>
      <span className="text-xs text-muted-foreground shrink-0 w-14 text-right">
        {relTime(item.searched_at)}
      </span>
    </div>
  );
}

// ── Telegram Enrichment Panel ─────────────────────────────────────────────

function TelegramSweepPanel() {
  const [status, setStatus] = useState<TgSweepStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = async () => {
    try {
      const data = await api.fetchTgSweepStatus();
      setStatus(data);
      setUnreachable(false);
      // Keep newest results at top
      if (containerRef.current) containerRef.current.scrollTop = 0;
    } catch {
      // Surface the failure instead of silently looking "Idle" — a down or
      // missing backend endpoint must not be indistinguishable from a healthy
      // but idle sweeper.
      setUnreachable(true);
    }
  };

  useEffect(() => {
    fetchData();
    const schedule = () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      const isActive = status?.locked || !!status?.flood_wait_until;
      intervalRef.current = setInterval(async () => {
        await fetchData();
        schedule();
      }, isActive ? 10_000 : 30_000);
    };
    schedule();
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.locked, status?.flood_wait_until]);

  const isFlooding = !!status?.flood_wait_remaining_seconds && status.flood_wait_remaining_seconds > 0;
  const isProcessing = !!status?.locked && !isFlooding;

  const badgeContent = unreachable
    ? (
      <span className="flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium bg-danger-soft text-danger border-danger-line">
        <AlertCircle className="h-3 w-3" />
        Backend unreachable
      </span>
    )
    : isProcessing
    ? (
      <span className="flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium bg-info-soft text-info border-info-line">
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-info opacity-75" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-info" />
        </span>
        Processing
      </span>
    )
    : isFlooding
    ? (
      <span className="flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium bg-warning-soft text-warning border-warning-line">
        <Clock className="h-3 w-3" />
        Flood Wait · {formatFloodWait(status!.flood_wait_remaining_seconds!)}
      </span>
    )
    : (
      <span className="flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium bg-muted text-muted-foreground border-border">
        <span className="inline-flex rounded-full h-2 w-2 bg-muted-foreground/40" />
        Idle
      </span>
    );

  return (
    <div className="rounded-lg border border-border bg-card p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-muted">
            <Search className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-tight">Telegram Enrichment</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Background sweeper — one lead every 20 minutes
            </p>
          </div>
        </div>
        <button
          onClick={fetchData}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          title="Refresh"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Status + last processed */}
      <div className="flex items-center gap-3 flex-wrap">
        {badgeContent}
        {!unreachable && status?.last_processed_at && (
          <span className="text-xs text-muted-foreground">
            Last processed {relTime(status.last_processed_at)}
          </span>
        )}
        {unreachable && (
          <span className="text-xs text-danger/80">
            Can&apos;t reach the status endpoint — the sweeper may still be running.
          </span>
        )}
      </div>

      {/* Stats chips */}
      {status && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="rounded-full border border-border bg-muted/50 px-3 py-1 text-xs text-muted-foreground">
            {status.pending_count.toLocaleString()} pending
          </span>
          <span className="rounded-full border border-info-line bg-info-soft/70 px-3 py-1 text-xs text-info">
            {status.searched_count.toLocaleString()} searched
          </span>
          <span className="rounded-full border border-success-line bg-success-soft/70 px-3 py-1 text-xs text-success flex items-center gap-1">
            <CheckCircle2 className="h-3 w-3" />
            {status.found_count.toLocaleString()} found
          </span>
        </div>
      )}

      {/* Recent results feed */}
      {status && status.recent_results.length > 0 ? (
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground font-medium">Recent results</p>
          <div
            ref={containerRef}
            className="rounded-md border border-border bg-background/50 px-3 max-h-64 overflow-y-auto"
          >
            {status.recent_results.map((item) => (
              <SweepResultRow key={`${item.lead_id}-${item.searched_at}`} item={item} />
            ))}
          </div>
        </div>
      ) : status ? (
        <p className="text-xs text-muted-foreground">
          No leads processed yet. The sweeper runs every 20 minutes when Telegram credentials are configured.
        </p>
      ) : null}
    </div>
  );
}

export default function ScrapersPage() {
  const { data: scrapers, isLoading } = useScrapers();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Scrapers</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Scraper cookies, fundraising runs, and background enrichment.
        </p>
      </div>

      {isLoading ? (
        <div className="text-sm text-muted-foreground">Loading...</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(scrapers ?? []).map((s) => (
            <ScraperCard key={s.site} status={s} />
          ))}
        </div>
      )}

      <FundraisingRunPanel />
      <TelegramSweepPanel />
    </div>
  );
}
