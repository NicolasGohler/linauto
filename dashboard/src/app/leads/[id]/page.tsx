"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  useLead,
  useUpdateLead,
  useLeadActivity,
  useSkipLead,
  useRequeueLead,
  useDeleteLead,
  useRestoreLead,
  useFindTelegram,
  useFindTelegramStatus,
  useFindX,
  useFindXStatus,
  useEnrichLeadPhone,
  useLeadNotes,
  useCreateLeadNote,
  useUpdateLeadNote,
  useDeleteLeadNote,
} from "@/hooks/use-queries";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import type { Lead, LeadActivity, LeadNote, FindTelegramTask, FindXTask, XCandidate, LeadListRef, CampaignRef } from "@/lib/types";
import {
  ArrowLeft, ExternalLink, Pencil, Check, X,
  Copy, Send, RotateCcw, FastForward, Trash2, Undo2,
  Clock, Zap, MessageSquare, UserCheck, AlertCircle,
  Link2, Loader2, Search, Sparkles, NotebookPen, Plus, ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ── helpers ──────────────────────────────────────────────────────────────────

function relativeDate(dateStr: string): { label: string; full: string } {
  const d = new Date(dateStr.endsWith("Z") ? dateStr : dateStr + "Z");
  const diffMs = Date.now() - d.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  const label =
    diffDays === 0 ? "today" :
    diffDays === 1 ? "yesterday" :
    diffDays < 7 ? `${diffDays}d ago` :
    diffDays < 30 ? `${Math.floor(diffDays / 7)}w ago` :
    `${Math.floor(diffDays / 30)}mo ago`;
  const full = d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return { label, full };
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.742l7.736-8.849L1.254 2.25H8.08l4.253 5.622L18.244 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117L17.083 19.77z" />
    </svg>
  );
}

const ACTION_ICON: Record<string, React.ReactNode> = {
  connection_request: <UserCheck className="h-3.5 w-3.5" />,
  followup_message: <MessageSquare className="h-3.5 w-3.5" />,
  check_acceptance: <UserCheck className="h-3.5 w-3.5" />,
  acceptance_check_summary: <UserCheck className="h-3.5 w-3.5" />,
  invitation_withdrawn: <X className="h-3.5 w-3.5" />,
  error: <AlertCircle className="h-3.5 w-3.5" />,
  feed_view: <Zap className="h-3.5 w-3.5" />,
  profile_view: <Zap className="h-3.5 w-3.5" />,
  // lead_event types
  telegram_found: <Search className="h-3.5 w-3.5" />,
  telegram_saved: <Send className="h-3.5 w-3.5" />,
  telegram_removed: <X className="h-3.5 w-3.5" />,
  tg_contacted: <Check className="h-3.5 w-3.5" />,
  tg_contacted_cleared: <X className="h-3.5 w-3.5" />,
  twitter_found: <Search className="h-3.5 w-3.5" />,
  x_found: <Search className="h-3.5 w-3.5" />,
  x_sweep_searched: <Search className="h-3.5 w-3.5" />,
  x_saved: <Check className="h-3.5 w-3.5" />,
  x_removed: <X className="h-3.5 w-3.5" />,
  x_contacted: <Check className="h-3.5 w-3.5" />,
  x_contacted_cleared: <X className="h-3.5 w-3.5" />,
  phone_enriched: <Sparkles className="h-3.5 w-3.5" />,
};

const ACTION_COLOUR: Record<string, string> = {
  success: "text-success bg-success-soft/70 border-success-line",
  failed: "text-danger bg-danger-soft/70 border-danger-line",
  skipped: "text-warning bg-warning-soft/70 border-warning-line",
};

// Pretty labels for lead_event types shown in the activity timeline
const EVENT_LABELS: Record<string, string> = {
  telegram_found: "Telegram search",
  telegram_saved: "Telegram handle saved",
  telegram_removed: "Telegram handle removed",
  tg_contacted: "TG outreach done",
  tg_contacted_cleared: "TG outreach cleared",
  twitter_found: "Twitter backfilled",
  x_found: "X search",
  x_sweep_searched: "X auto-search",
  x_saved: "X handle saved",
  x_removed: "X handle removed",
  x_contacted: "X outreach done",
  x_contacted_cleared: "X outreach cleared",
  phone_enriched: "Phone enriched",
};

// ── Inline editable field ────────────────────────────────────────────────────

interface InlineFieldProps {
  label: string;
  value: string | null | undefined;
  onSave: (val: string | null) => Promise<void>;
  placeholder?: string;
  prefix?: string;
  hint?: string;
  transform?: (raw: string) => string;
  /** When provided, the displayed value renders as a link opening this URL. */
  href?: (value: string) => string;
}

function InlineField({
  label, value, onSave, placeholder = "—", prefix, hint, transform, href,
}: InlineFieldProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function startEdit() {
    setDraft(value ?? "");
    setEditing(true);
  }

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  async function handleSave() {
    setSaving(true);
    try {
      const cleaned = draft.trim().replace(/^@/, "");
      await onSave(cleaned || null);
      setEditing(false);
    } catch {
      toast.error(`Failed to save ${label.toLowerCase()}`);
    } finally {
      setSaving(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") setEditing(false);
  }

  const displayValue = value ? (transform ? transform(value) : value) : null;

  return (
    <div className="group flex flex-col gap-0.5 py-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {editing ? (
        <div className="flex items-center gap-1.5">
          {prefix && <span className="text-sm text-muted-foreground">{prefix}</span>}
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-ring"
            disabled={saving}
          />
          <button onClick={handleSave} disabled={saving}
            className="rounded p-1 text-success hover:bg-success-soft/70 transition-colors">
            <Check className="h-3.5 w-3.5" />
          </button>
          <button onClick={() => setEditing(false)}
            className="rounded p-1 text-muted-foreground hover:bg-muted transition-colors">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-1.5 min-h-[1.75rem]">
          {displayValue && href ? (
            <a
              href={href(displayValue)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-primary hover:underline inline-flex items-center gap-1"
            >
              {prefix ? `${prefix}${displayValue}` : displayValue}
              <ExternalLink className="h-3 w-3 flex-shrink-0" />
            </a>
          ) : (
            <span className={`text-sm ${displayValue ? "" : "text-muted-foreground/50 italic"}`}>
              {displayValue ? (prefix ? `${prefix}${displayValue}` : displayValue) : placeholder}
            </span>
          )}
          <button onClick={startEdit}
            className="opacity-0 group-hover:opacity-100 rounded p-0.5 text-muted-foreground/60 hover:text-muted-foreground transition-all"
            title={`Edit ${label.toLowerCase()}`}>
            <Pencil className="h-3 w-3" />
          </button>
          {hint && displayValue && (
            <span className="text-xs text-muted-foreground/50">{hint}</span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Shared bits for the X and Telegram blocks ────────────────────────────────

const SOCIAL_SHELL = "rounded-[12px] border border-border/80 bg-card/70 px-3 py-2.5 space-y-2";
const SOCIAL_LABEL = "inline-flex items-center gap-1.5 text-xs font-semibold text-foreground/80";
const FIND_BTN = "inline-flex items-center gap-1 rounded-[6px] px-1.5 py-0.5 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40 transition-colors";
const HANDLE_INPUT = "flex-1 rounded-[7px] border border-border bg-card px-2 py-1 text-sm outline-none focus:border-ring/60 focus:ring-[3px] focus:ring-ring/15";

function ContactedRow({ at, onToggle }: { at: string | null | undefined; onToggle: () => void }) {
  return (
    <div className="flex items-center justify-between gap-2 border-t border-border/70 pt-2">
      {at ? (
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-success" title={relativeDate(at).full}>
          <Check className="h-3.5 w-3.5" /> Contacted {relativeDate(at).label}
        </span>
      ) : (
        <span className="text-xs text-muted-foreground">Not contacted yet</span>
      )}
      {at ? (
        <button onClick={onToggle} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
          <Undo2 className="h-3 w-3" /> Undo
        </button>
      ) : (
        <button onClick={onToggle} className="inline-flex items-center gap-1 rounded-[7px] border border-border bg-card px-2 py-0.5 text-xs font-medium hover:bg-primary hover:text-primary-foreground hover:border-primary transition-colors">
          <Check className="h-3 w-3" /> Mark contacted
        </button>
      )}
    </div>
  );
}

// ── Telegram section (prominent inline-edit + find button + candidates) ───────

interface TelegramSectionProps {
  lead: Lead;
  onSave: (v: string | null) => Promise<void>;
  onToggleContacted: (v: boolean) => Promise<void>;
  activity?: LeadActivity[];
}

function TelegramSection({ lead, onSave, onToggleContacted, activity }: TelegramSectionProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // True if a prior search ran and found nothing, and no username is saved yet.
  const previousSearchFoundNothing = !lead.telegram_username && (() => {
    if (!activity) return false;
    const last = [...activity]
      .filter(e => e.source === "lead_event" && e.action_type === "telegram_found")
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    return !!last && !last.details?.best_match;
  })();

  const findMutation = useFindTelegram(lead.id);
  const taskQuery = useFindTelegramStatus(lead.id, taskId);

  const taskData = taskQuery.data as FindTelegramTask | undefined;
  const isSearching = !!taskId && taskData?.status === "running";
  const searchDone = !!taskId && (taskData?.status === "done" || taskData?.status === "error");

  // Candidates to show: from active task result, or from DB-persisted alternatives
  const liveCandidates: string[] = taskId && taskData
    ? [
        ...(taskData.telegram_username ? [taskData.telegram_username] : []),
        ...(taskData.telegram_alternatives ?? []),
      ]
    : [];
  const storedCandidates: string[] = !taskId && lead.telegram_alternatives
    ? lead.telegram_alternatives
    : [];
  const candidates = taskId ? liveCandidates : storedCandidates;

  // Auto-open edit field with best match when search completes
  useEffect(() => {
    if (taskData?.status === "done" && taskData.telegram_username && !editing) {
      setDraft(taskData.telegram_username);
      setEditing(true);
    }
  }, [taskData?.status, taskData?.telegram_username]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  function startEdit() {
    setDraft(lead.telegram_username ?? "");
    setEditing(true);
  }

  function pickCandidate(handle: string) {
    setDraft(handle);
    setEditing(true);
    setTimeout(() => inputRef.current?.focus(), 50);
  }

  async function handleSave() {
    setSaving(true);
    try {
      const cleaned = draft.trim().replace(/^@/, "");
      await onSave(cleaned || null);
      setEditing(false);
      setTaskId(null); // clear search state after save
    } catch {
      toast.error("Failed to save Telegram username");
    } finally {
      setSaving(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") setEditing(false);
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await onSave(null);
      setTaskId(null);
    } catch {
      toast.error("Failed to remove Telegram username");
    } finally {
      setDeleting(false);
    }
  }

  async function handleFind(force = false) {
    setTaskId(null);
    try {
      const result = await findMutation.mutateAsync(force);
      setTaskId(result.task_id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to start search";
      toast.error(msg);
    }
  }

  const displayValue = lead.telegram_username;

  return (
    <div className={SOCIAL_SHELL}>
      {/* Label row */}
      <div className="flex items-center justify-between gap-2">
        <span className={SOCIAL_LABEL}><Send className="h-3.5 w-3.5" /> Telegram</span>
        {previousSearchFoundNothing && !taskId ? (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            No match
            <button
              onClick={() => handleFind(true)}
              disabled={findMutation.isPending}
              className={FIND_BTN}
              title="Re-run Telegram search"
            >
              <RotateCcw className="h-3 w-3" /> Retry
            </button>
          </span>
        ) : (
          <button
            onClick={() => handleFind()}
            disabled={findMutation.isPending || isSearching}
            className={FIND_BTN}
            title="Search Telegram for this lead"
          >
            {isSearching ? (
              <><Loader2 className="h-3 w-3 animate-spin" /> Searching…</>
            ) : (
              <><Search className="h-3 w-3" /> Find</>
            )}
          </button>
        )}
      </div>

      {/* Editable field */}
      {editing ? (
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-muted-foreground">@</span>
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="username"
            className={HANDLE_INPUT}
            disabled={saving}
          />
          <button onClick={handleSave} disabled={saving}
            className="rounded-[6px] p-1 text-success hover:bg-success-soft transition-colors">
            <Check className="h-3.5 w-3.5" />
          </button>
          <button onClick={() => setEditing(false)}
            className="rounded-[6px] p-1 text-muted-foreground hover:bg-accent transition-colors">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div className="group flex items-center gap-1.5 min-h-[1.75rem]">
          {displayValue ? (
            <>
              <a
                href={`https://t.me/${displayValue}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm font-semibold hover:underline underline-offset-2"
                title="Open in Telegram"
              >
                @{displayValue}
              </a>
              <a
                href={`https://t.me/${displayValue}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-muted-foreground hover:text-foreground transition-colors"
                title="Open in Telegram"
              >
                <ExternalLink className="h-3 w-3" />
              </a>
              <CopyButton text={displayValue} label="Copy handle" />
            </>
          ) : (
            <span className="text-sm text-muted-foreground/70">
              {isSearching ? "Searching…" : "No handle yet"}
            </span>
          )}
          <button onClick={startEdit}
            className="opacity-0 group-hover:opacity-100 rounded p-0.5 text-muted-foreground/60 hover:text-foreground transition-all ml-0.5"
            title="Edit Telegram handle">
            <Pencil className="h-3 w-3" />
          </button>
          {displayValue && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="opacity-0 group-hover:opacity-100 rounded p-0.5 text-muted-foreground/60 hover:text-destructive transition-all"
              title="Delete Telegram handle"
            >
              {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
            </button>
          )}
        </div>
      )}

      {/* Outreach done toggle, only when there's a handle */}
      {lead.telegram_username && (
        <ContactedRow at={lead.tg_contacted_at} onToggle={() => onToggleContacted(!lead.tg_contacted_at)} />
      )}

      {/* Candidates picker */}
      {candidates.length > 0 && (
        <div className="border-t border-border/70 pt-2 space-y-1.5">
          <p className="text-xs text-muted-foreground">
            {taskId ? "Found, pick the right one" : "Candidates"}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {candidates.map((handle) => (
              <button
                key={handle}
                onClick={() => pickCandidate(handle)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors",
                  lead.telegram_username === handle
                    ? "border-success-line bg-success-soft text-success"
                    : "border-border bg-card hover:bg-accent"
                )}
              >
                {lead.telegram_username === handle && <Check className="h-3 w-3" />}
                @{handle}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Search logs (collapsed by default) */}
      {searchDone && taskData?.logs && taskData.logs.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground/70 hover:text-muted-foreground select-none">
            Search log ({taskData.logs.length} lines)
          </summary>
          <div className="mt-1 rounded-[7px] bg-secondary/70 p-2 font-mono text-xs leading-relaxed max-h-40 overflow-y-auto space-y-0.5">
            {taskData.logs.map((line, i) => (
              <p key={i} className="text-muted-foreground">{line}</p>
            ))}
          </div>
        </details>
      )}

      {/* Error state */}
      {taskData?.status === "error" && (
        <p className="text-xs text-danger">{taskData.error || "Search failed"}</p>
      )}
    </div>
  );
}

// ── Copy button ──────────────────────────────────────────────────────────────

// ── X section (same UX as Telegram: inline edit + Find + scored candidates) ──

interface XSectionProps {
  lead: Lead;
  onSave: (handle: string | null) => Promise<void>;
  onToggleContacted: (v: boolean) => Promise<void>;
}

function xHandleFromUrl(url: string | null): string | null {
  if (!url) return null;
  const m = url.match(/(?:x|twitter)\.com\/@?([A-Za-z0-9_]+)/i);
  return m ? m[1] : url.replace(/^@/, "");
}

function XSection({ lead, onSave, onToggleContacted }: XSectionProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const findMutation = useFindX(lead.id);
  const taskQuery = useFindXStatus(lead.id, taskId);
  const taskData = taskQuery.data as FindXTask | undefined;
  const isSearching = !!taskId && taskData?.status === "running";
  const searchDone = !!taskId && (taskData?.status === "done" || taskData?.status === "error");

  const handle = xHandleFromUrl(lead.twitter_url);
  const candidates: XCandidate[] = taskId && taskData
    ? taskData.x_alternatives ?? []
    : lead.x_alternatives ?? [];
  const [showAll, setShowAll] = useState(false);
  // Candidates start collapsed once a handle is chosen; open when there's a choice to make.
  const [listOpen, setListOpen] = useState(() => !handle);
  useEffect(() => { if (taskId) setListOpen(true); }, [taskId]);
  // Selected handle first, then best score, then open DMs.
  const sortedCandidates = [...candidates].sort((a, b) => {
    const sel = (c: XCandidate) => (handle?.toLowerCase() === c.username.toLowerCase() ? 1 : 0);
    return sel(b) - sel(a) || b.score - a.score || Number(!!b.can_dm) - Number(!!a.can_dm);
  });
  const visibleCandidates = showAll ? sortedCandidates : sortedCandidates.slice(0, COLLAPSED_COUNT);

  useEffect(() => {
    if (taskData?.status === "done" && taskData.x_username && !editing && !handle) {
      setDraft(taskData.x_username);
      setEditing(true);
    }
  }, [taskData?.status, taskData?.x_username]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  async function handleSave(value?: string) {
    setSaving(true);
    try {
      const cleaned = (value ?? draft).trim().replace(/^@/, "");
      await onSave(cleaned || null);
      setEditing(false);
      setTaskId(null);
    } catch {
      toast.error("Failed to save X handle");
    } finally {
      setSaving(false);
    }
  }

  async function handleFind() {
    setTaskId(null);
    try {
      const result = await findMutation.mutateAsync();
      setTaskId(result.task_id);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to start search");
    }
  }

  const strongCount = sortedCandidates.filter((c) => c.confident || c.score >= 5).length;

  return (
    <div className={SOCIAL_SHELL}>
      <div className="flex items-center justify-between gap-2">
        <span className={SOCIAL_LABEL}><XIcon className="h-3 w-3" /> X</span>
        <button
          onClick={handleFind}
          disabled={findMutation.isPending || isSearching}
          className={FIND_BTN}
          title="Search X for this lead's personal account"
        >
          {isSearching ? <><Loader2 className="h-3 w-3 animate-spin" /> Searching…</> : <><Search className="h-3 w-3" /> Find</>}
        </button>
      </div>

      {editing ? (
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-muted-foreground">@</span>
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSave(); if (e.key === "Escape") setEditing(false); }}
            placeholder="handle"
            className={HANDLE_INPUT}
            disabled={saving}
          />
          <button onClick={() => handleSave()} disabled={saving} className="rounded-[6px] p-1 text-success hover:bg-success-soft transition-colors">
            <Check className="h-3.5 w-3.5" />
          </button>
          <button onClick={() => setEditing(false)} className="rounded-[6px] p-1 text-muted-foreground hover:bg-accent transition-colors">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div className="group flex items-center gap-1.5 min-h-[1.75rem]">
          {handle ? (
            <>
              <a href={`https://x.com/${handle}`} target="_blank" rel="noopener noreferrer" className="text-sm font-semibold hover:underline underline-offset-2" title="Open on X">
                @{handle}
              </a>
              <a href={`https://x.com/${handle}`} target="_blank" rel="noopener noreferrer" className="text-muted-foreground hover:text-foreground transition-colors" title="Open on X">
                <ExternalLink className="h-3 w-3" />
              </a>
              <CopyButton text={`https://x.com/${handle}`} label="Copy X URL" />
            </>
          ) : (
            <span className="text-sm text-muted-foreground/70">{isSearching ? "Searching…" : "No handle yet"}</span>
          )}
          <button onClick={() => { setDraft(handle ?? ""); setEditing(true); }}
            className="opacity-0 group-hover:opacity-100 rounded p-0.5 text-muted-foreground/60 hover:text-foreground transition-all ml-0.5" title="Edit X handle">
            <Pencil className="h-3 w-3" />
          </button>
          {handle && (
            <button
              onClick={() => { if (window.confirm(`Remove @${handle}? It won't be suggested again for this lead.`)) handleSave(""); }}
              disabled={saving}
              className="opacity-0 group-hover:opacity-100 rounded p-0.5 text-muted-foreground/60 hover:text-destructive transition-all" title="Remove X handle (it won't be suggested again)">
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
            </button>
          )}
        </div>
      )}

      {handle && (
        <ContactedRow at={lead.x_contacted_at} onToggle={() => onToggleContacted(!lead.x_contacted_at)} />
      )}

      {sortedCandidates.length > 0 && (
        <div className="border-t border-border/70 pt-2">
          <button
            onClick={() => setListOpen((v) => !v)}
            className="flex w-full items-center justify-between gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
            title="Score 5+ means the name and company both match"
          >
            <span className="inline-flex items-center gap-1">
              <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", listOpen && "rotate-90")} />
              {taskId ? "Search results" : "Candidates"}
              <span className="tabular-nums">({sortedCandidates.length})</span>
            </span>
            {strongCount > 0 && (
              <span className="tabular-nums">{strongCount} strong</span>
            )}
          </button>
          {listOpen && (
            <div className="mt-2 overflow-hidden rounded-[9px] border border-border/80 bg-card divide-y divide-border/70">
              {visibleCandidates.map((c) => (
                <XCandidateRow
                  key={c.username}
                  c={c}
                  selected={handle?.toLowerCase() === c.username.toLowerCase()}
                  busy={saving}
                  onUse={() => handleSave(c.username)}
                />
              ))}
              {sortedCandidates.length > COLLAPSED_COUNT && (
                <button
                  onClick={() => setShowAll((v) => !v)}
                  className="w-full py-1.5 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground transition-colors"
                >
                  {showAll ? "Show fewer" : `Show ${sortedCandidates.length - COLLAPSED_COUNT} more`}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {searchDone && taskData?.logs && taskData.logs.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground/70 hover:text-muted-foreground select-none">Search log ({taskData.logs.length} lines)</summary>
          <div className="mt-1 rounded-[7px] bg-secondary/70 p-2 font-mono text-xs leading-relaxed max-h-40 overflow-y-auto space-y-0.5">
            {taskData.logs.map((line, i) => <p key={i} className="text-muted-foreground">{line}</p>)}
          </div>
        </details>
      )}
      {taskData?.status === "error" && <p className="text-xs text-danger">{taskData.error || "Search failed"}</p>}
    </div>
  );
}

const COLLAPSED_COUNT = 5;

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  return String(n);
}

function XCandidateRow({ c, selected, busy, onUse }: { c: XCandidate; selected: boolean; busy: boolean; onUse: () => void }) {
  const url = `https://x.com/${c.username}`;
  const strong = c.confident || c.score >= 5;
  return (
    <div className={cn("flex items-start gap-2.5 px-2.5 py-2 text-xs transition-colors", selected ? "bg-success-soft/60" : "hover:bg-accent/40")}>
      <span
        className={cn(
          "mt-px w-8 shrink-0 rounded-[5px] py-0.5 text-center text-[10px] font-semibold tabular-nums",
          strong ? "bg-success-soft text-success" : "bg-warning-soft text-warning"
        )}
        title="Match score (5+ = name and company match)"
      >
        {c.score.toFixed(1)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 min-w-0">
          <a href={url} target="_blank" rel="noopener noreferrer" className="shrink-0 font-semibold hover:underline underline-offset-2" title="Open profile on X">
            @{c.username}
          </a>
          {c.name && <span className="truncate text-muted-foreground">{c.name}</span>}
        </div>
        {c.bio && (
          <p className="mt-0.5 line-clamp-2 leading-snug text-foreground/70 break-words" title={c.bio}>{c.bio}</p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground">
        {c.followers != null && <span className="tabular-nums" title="Followers">{formatCount(c.followers)}</span>}
        {c.can_dm != null && (
          <span
            className={cn("rounded-[5px] px-1 py-px font-medium", c.can_dm ? "bg-info-soft text-info" : "text-muted-foreground/60 line-through")}
            title={c.can_dm ? "Accepts DMs" : "DMs closed"}
          >
            DM
          </span>
        )}
        <CopyButton text={url} label="Copy X URL" />
        {selected ? (
          <span className="inline-flex w-12 items-center justify-center gap-0.5 font-medium text-success" title="Selected">
            <Check className="h-3 w-3" />
          </span>
        ) : (
          <button
            onClick={onUse}
            disabled={busy}
            className="w-12 rounded-[6px] border border-border bg-card py-0.5 font-medium text-foreground/80 hover:bg-primary hover:text-primary-foreground hover:border-primary disabled:opacity-40 transition-colors"
          >
            Use
          </button>
        )}
      </div>
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
      className="rounded p-0.5 text-muted-foreground/60 hover:text-muted-foreground transition-colors"
      title={label ?? "Copy"}
    >
      {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
    </button>
  );
}

// ── Activity timeline item ───────────────────────────────────────────────────

function ActivityItem({ entry }: { entry: LeadActivity }) {
  const { label, full } = relativeDate(entry.created_at);
  const isLeadEvent = entry.source === "lead_event";
  const colour = isLeadEvent
    ? "text-info bg-info-soft/70 border-info-line"
    : (ACTION_COLOUR[entry.status] ?? ACTION_COLOUR.skipped);
  const icon = ACTION_ICON[entry.action_type] ?? <Zap className="h-3.5 w-3.5" />;
  const label_ = EVENT_LABELS[entry.action_type] ?? entry.action_type.replace(/_/g, " ");
  const details = entry.details as { reason?: string; best_match?: string; alternatives?: string[]; username?: string; phone?: string; source?: string; at?: string } | null;

  return (
    <div className="flex gap-3 items-start py-2 border-b last:border-0">
      <div className={`mt-0.5 flex-shrink-0 rounded-full border p-1 ${colour}`}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-1.5 flex-wrap">
          <span className="text-sm font-medium capitalize">{label_}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium ${colour}`}>
            {entry.status}
          </span>
        </div>
        {details?.reason && (
          <p className="text-xs text-muted-foreground mt-0.5">
            {(details.reason as string).replace(/_/g, " ")}
          </p>
        )}
        {details?.username && (
          <p className="text-xs text-muted-foreground mt-0.5">@{details.username}</p>
        )}
        {details?.best_match && (
          <p className="text-xs text-muted-foreground mt-0.5">
            Found: @{details.best_match}
            {details.alternatives && details.alternatives.length > 0 &&
              ` (+${details.alternatives.length} alt)`}
          </p>
        )}
        {details?.phone && (
          <p className="text-xs text-muted-foreground mt-0.5">{details.phone}{details.source ? ` · via ${details.source}` : ""}</p>
        )}
        {entry.actor_name && (
          <p className="text-xs text-success/80 mt-0.5">by {entry.actor_name}</p>
        )}
        {entry.account_name && (
          <p className="text-xs text-muted-foreground/60 mt-0.5">via {entry.account_name}</p>
        )}
      </div>
      <span className="text-xs text-muted-foreground/60 whitespace-nowrap" title={full}>{label}</span>
    </div>
  );
}

// ── Milestone row ────────────────────────────────────────────────────────────

function MilestoneRow({ icon, label, dateStr }: { icon: React.ReactNode; label: string; dateStr: string | null | undefined }) {
  if (!dateStr) return null;
  const { label: rel, full } = relativeDate(dateStr);
  return (
    <div className="flex items-center gap-2 py-1.5 border-b last:border-0">
      <span className="text-muted-foreground/60">{icon}</span>
      <span className="text-sm flex-1">{label}</span>
      <span className="text-xs text-muted-foreground" title={full}>{rel}</span>
    </div>
  );
}

// ── Notes (HubSpot-style multi-note) ─────────────────────────────────────────

function NoteItem({ note, leadId }: { note: LeadNote; leadId: string }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.body);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const updateNote = useUpdateLeadNote(leadId);
  const deleteNote = useDeleteLeadNote(leadId);

  useEffect(() => {
    if (editing) {
      taRef.current?.focus();
      taRef.current?.setSelectionRange(draft.length, draft.length);
    }
  }, [editing]);

  const created = relativeDate(note.created_at);
  const edited =
    note.updated_at && note.updated_at !== note.created_at
      ? relativeDate(note.updated_at)
      : null;

  async function handleSave() {
    const body = draft.trim();
    if (!body) return;
    try {
      await updateNote.mutateAsync({ noteId: note.id, body });
      setEditing(false);
    } catch {
      toast.error("Failed to update note");
    }
  }

  async function handleDelete() {
    try {
      await deleteNote.mutateAsync(note.id);
    } catch {
      toast.error("Failed to delete note");
    }
  }

  if (editing) {
    return (
      <div className="rounded-lg border border-border bg-background p-3 space-y-2">
        <textarea
          ref={taRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSave();
            if (e.key === "Escape") { setDraft(note.body); setEditing(false); }
          }}
          rows={3}
          className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
        />
        <div className="flex items-center justify-end gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => { setDraft(note.body); setEditing(false); }}>
            Cancel
          </Button>
          <Button size="sm" disabled={updateNote.isPending || !draft.trim()} onClick={handleSave}>
            {updateNote.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="group rounded-lg border border-border bg-background p-3">
      <p className="text-sm whitespace-pre-wrap break-words">{note.body}</p>
      <div className="mt-2 flex items-center gap-2">
        <span className="text-xs text-muted-foreground/60" title={created.full}>
          {created.full}
          {edited && <span className="ml-1 italic">· edited {edited.label}</span>}
        </span>
        <div className="ml-auto flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={() => { setDraft(note.body); setEditing(true); }}
            className="rounded p-1 text-muted-foreground/60 hover:text-muted-foreground hover:bg-muted transition-colors"
            title="Edit note"
          >
            <Pencil className="h-3 w-3" />
          </button>
          <button
            onClick={handleDelete}
            disabled={deleteNote.isPending}
            className="rounded p-1 text-muted-foreground/60 hover:text-destructive hover:bg-destructive/10 transition-colors"
            title="Delete note"
          >
            {deleteNote.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Merged Notes & Activity feed (composer on top, interleaved by time) ───────

function tsMillis(dateStr: string): number {
  return new Date(dateStr.endsWith("Z") ? dateStr : dateStr + "Z").getTime();
}

interface ActivityFeedProps {
  lead: Lead;
  notes: LeadNote[] | undefined;
  notesLoading: boolean;
  activity: LeadActivity[] | undefined;
  activityLoading: boolean;
}

type FeedItem =
  | { kind: "note"; created_at: string; note: LeadNote }
  | { kind: "activity"; created_at: string; entry: LeadActivity };

function ActivityFeed({ lead, notes, notesLoading, activity, activityLoading }: ActivityFeedProps) {
  const leadId = lead.id;
  const createNote = useCreateLeadNote(leadId);
  const [draft, setDraft] = useState("");

  async function handleAdd() {
    const body = draft.trim();
    if (!body) return;
    try {
      await createNote.mutateAsync(body);
      setDraft("");
    } catch {
      toast.error("Failed to add note");
    }
  }

  const loading = notesLoading || activityLoading;

  // Interleave notes + activity, newest-first
  const items: FeedItem[] = [
    ...(notes ?? []).map((n): FeedItem => ({ kind: "note", created_at: n.created_at, note: n })),
    ...(activity ?? []).map((e): FeedItem => ({ kind: "activity", created_at: e.created_at, entry: e })),
  ].sort((a, b) => tsMillis(b.created_at) - tsMillis(a.created_at));

  return (
    <div className="rounded-[14px] border border-border/80 bg-card shadow-soft p-4">
      {/* Campaign status summary */}
      <div className="mb-4 pb-4 border-b">
        <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Campaign Status</h2>
        {/* Campaign assignments — prefer the normalised campaigns[] array over
            the legacy denormalized lead.campaign_name / lead.status fields,
            which are NULL for de-duped leads re-imported into a new list. */}
        {lead.campaigns && lead.campaigns.length > 0 ? (
          <div className="space-y-2 mb-3">
            {lead.campaigns.map((c: CampaignRef) => (
              <div key={c.id} className="rounded-md border bg-muted/30 px-3 py-2">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-xs font-medium truncate">{c.name}</span>
                  {c.account_name && (
                    <span className="text-xs text-muted-foreground shrink-0">{c.account_name}</span>
                  )}
                </div>
                <StatusBadge status={c.status} />
              </div>
            ))}
          </div>
        ) : (
          <>
            {lead.campaign_name && (
              <p className="text-sm font-medium mb-1">{lead.campaign_name}</p>
            )}
            <div className="mb-3">
              <StatusBadge status={lead.status} />
            </div>
          </>
        )}
        {lead.error_message && (
          <div className="rounded-md bg-danger-soft/70 border border-danger-line px-3 py-2 mb-3">
            <p className="text-xs text-danger font-medium">Error</p>
            <p className="text-xs text-danger/80 mt-0.5">
              {lead.error_message.replace(/_/g, " ")}
            </p>
            {lead.retry_count > 0 && (
              <p className="text-xs text-danger/60 mt-0.5">{lead.retry_count} retry attempt{lead.retry_count !== 1 ? "s" : ""}</p>
            )}
          </div>
        )}

        <div className="space-y-0">
          <MilestoneRow icon={<Clock className="h-3.5 w-3.5" />} label="Imported" dateStr={lead.created_at} />
          <MilestoneRow icon={<UserCheck className="h-3.5 w-3.5" />} label="Connection requested" dateStr={lead.connection_requested_at} />
          <MilestoneRow icon={<UserCheck className="h-3.5 w-3.5" />} label="Connection accepted" dateStr={lead.connection_accepted_at} />
          <MilestoneRow icon={<MessageSquare className="h-3.5 w-3.5" />} label="Follow-up sent" dateStr={lead.followup_sent_at} />
          {lead.scheduled_at && lead.status === "scheduled" && (
            <MilestoneRow icon={<Clock className="h-3.5 w-3.5" />} label="Scheduled for" dateStr={lead.scheduled_at} />
          )}
          {lead.updated_at && (
            <div className="pt-2 mt-1 border-t">
              <p className="text-xs text-muted-foreground/50">
                Last updated {relativeDate(lead.updated_at).label}
              </p>
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 mb-3">
        <NotebookPen className="h-3.5 w-3.5 text-muted-foreground" />
        <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Notes & Activity</h2>
      </div>

      {/* Composer */}
      <div className="space-y-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleAdd();
          }}
          placeholder="Add a note… (⌘/Ctrl+Enter to post)"
          rows={3}
          className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground/50"
        />
        <div className="flex justify-end">
          <Button size="sm" disabled={createNote.isPending || !draft.trim()} onClick={handleAdd}>
            {createNote.isPending
              ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              : <Plus className="mr-1.5 h-3.5 w-3.5" />}
            Add note
          </Button>
        </div>
      </div>

      {/* Merged feed */}
      <div className="mt-4">
        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
          </div>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground/60 text-center py-4">No notes or activity yet</p>
        ) : (
          <div className="max-h-[32rem] overflow-y-auto -mx-1 px-1 space-y-2">
            {items.map((item) =>
              item.kind === "note" ? (
                <NoteItem key={`note-${item.note.id}`} note={item.note} leadId={leadId} />
              ) : (
                <ActivityItem key={`act-${item.entry.id}`} entry={item.entry} />
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function LeadDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const { data: lead, isLoading } = useLead(id);
  const { data: activity, isLoading: activityLoading } = useLeadActivity(id);
  const { data: notes, isLoading: notesLoading } = useLeadNotes(id);
  const update = useUpdateLead(id);
  const skip = useSkipLead();
  const requeue = useRequeueLead();
  const remove = useDeleteLead();
  const restore = useRestoreLead();
  const enrichPhone = useEnrichLeadPhone(id);

  const save = useCallback(async (field: string, value: string | null) => {
    await update.mutateAsync({ [field]: value });
    toast.success("Saved");
  }, [update]);

  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (!lead) {
    return (
      <div className="p-6 text-center">
        <p className="text-muted-foreground">Lead not found</p>
        <Link href="/leads" className="text-sm text-primary hover:underline mt-2 inline-block">← Back to leads</Link>
      </div>
    );
  }

  const displayName = [lead.first_name, lead.last_name].filter(Boolean).join(" ") || "Unnamed lead";
  const canSkip = lead.status === "pending" || lead.status === "scheduled";
  const canRequeue = lead.status === "error" || lead.status === "skipped" || lead.status === "withdrawn";
  const isRemoved = lead.status === "removed";

  const companyXRaw = lead.extra_data?.company_x_url;
  const companyX = typeof companyXRaw === "string" && companyXRaw ? companyXRaw : null;
  const extraEntries = Object.entries(lead.extra_data ?? {}).filter(([k]) => k !== "company_x_url");
  const outreachMessage = buildOutreachMessage(lead.extra_data);

  const twitterHandle = lead.twitter_url
    ? lead.twitter_url.replace(/.*x\.com\//, "").replace(/.*twitter\.com\//, "")
    : null;

  return (
    <div className="max-w-7xl mx-auto px-6 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-start gap-4">
        <Link
          href="/leads"
          className="mt-1 flex-shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-muted transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-semibold tracking-tight truncate">{displayName}</h1>
            <StatusBadge status={lead.status} />
          </div>
          {lead.title && (
            <p className="text-sm text-muted-foreground mt-0.5">
              {lead.title}{lead.company ? ` · ${lead.company}` : ""}
            </p>
          )}
        </div>

        {/* Quick links */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {lead.linkedin_url && (
          <a
            href={lead.linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md p-2 text-muted-foreground hover:bg-muted transition-colors"
            title="Open LinkedIn profile"
          >
            <Link2 className="h-4 w-4" />
          </a>
          )}
          {lead.twitter_url && (
            <a
              href={lead.twitter_url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md p-2 text-muted-foreground hover:bg-muted transition-colors"
              title={`X: @${twitterHandle}`}
            >
              <XIcon className="h-4 w-4" />
            </a>
          )}
          {lead.telegram_username && (
            <a
              href={`https://t.me/${lead.telegram_username}`}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md p-2 text-info hover:bg-info-soft/70 transition-colors"
              title={`Telegram: @${lead.telegram_username}`}
            >
              <Send className="h-4 w-4" />
            </a>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {canSkip && (
            <Button size="sm" variant="outline" disabled={skip.isPending}
              onClick={() => skip.mutate(lead.id, { onSuccess: () => toast.success("Lead skipped") })}>
              <FastForward className="mr-1.5 h-3.5 w-3.5" /> Skip
            </Button>
          )}
          {canRequeue && (
            <Button size="sm" variant="outline" disabled={requeue.isPending}
              onClick={() => requeue.mutate(lead.id, { onSuccess: () => toast.success("Lead re-queued") })}>
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Re-queue
            </Button>
          )}
          {isRemoved ? (
            <Button size="sm" variant="outline" disabled={restore.isPending}
              onClick={() => restore.mutate(lead.id, { onSuccess: () => toast.success("Lead restored") })}>
              <Undo2 className="mr-1.5 h-3.5 w-3.5" /> Restore
            </Button>
          ) : (
            <Button size="sm" variant="ghost"
              className="text-muted-foreground hover:text-destructive hover:bg-destructive/10"
              disabled={remove.isPending}
              onClick={() => remove.mutate(lead.id, {
                onSuccess: () => { toast.success("Lead removed"); router.push("/leads"); }
              })}>
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_380px] xl:grid-cols-[minmax(0,1fr)_500px] gap-6">
        {/* Left (main): editable profile + notes & activity */}
        <div className="min-w-0 space-y-4">

          {/* Profile card */}
          <div className="rounded-[14px] border border-border/80 bg-card shadow-soft p-4">
            <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Profile</h2>
            {/* Two-column grid — fields flow across both columns, row by row */}
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <InlineField label="First name" value={lead.first_name} placeholder="Add first name"
                onSave={(v) => save("first_name", v)} />
              <InlineField label="Last name" value={lead.last_name} placeholder="Add last name"
                onSave={(v) => save("last_name", v)} />

              <InlineField label="Title / Role" value={lead.title} placeholder="Add title"
                onSave={(v) => save("title", v)} />
              <InlineField label="Company" value={lead.company} placeholder="Add company"
                onSave={(v) => save("company", v)} />

              <InlineField label="Location" value={lead.location} placeholder="Add location"
                onSave={(v) => save("location", v)} />
              <div className="flex items-start gap-2">
                <div className="flex-1 min-w-0">
                  <InlineField label="Email" value={lead.email} placeholder="Add email"
                    onSave={(v) => save("email", v)} />
                </div>
                {lead.email && (
                  <div className="mt-6">
                    <CopyButton text={lead.email} label="Copy email" />
                  </div>
                )}
              </div>

              <div className="flex items-end gap-2">
                <div className="flex-1 min-w-0">
                  <InlineField label="Phone" value={lead.phone} placeholder="Add phone"
                    onSave={(v) => save("phone", v)} />
                </div>
                <button
                  onClick={async () => {
                    try {
                      const r = await enrichPhone.mutateAsync();
                      if (r.found) toast.success(`Phone found: ${r.phone}`);
                      else toast.info("No phone found on Apollo");
                    } catch (e: unknown) {
                      toast.error(e instanceof Error ? e.message : "Enrich failed");
                    }
                  }}
                  disabled={enrichPhone.isPending}
                  className="mb-1.5 flex items-center gap-1 rounded px-2 py-1 text-xs text-plum border border-plum-line hover:bg-plum-soft/70 disabled:opacity-40 transition-colors whitespace-nowrap"
                  title="Look up phone via Apollo"
                >
                  {enrichPhone.isPending
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <Sparkles className="h-3 w-3" />}
                  {enrichPhone.isPending ? "…" : "Enrich"}
                </button>
              </div>

              {lead.lead_lists && lead.lead_lists.length > 0 && (
                <div className="flex flex-col gap-1 py-1.5">
                  <span className="text-xs font-medium text-muted-foreground">Lists</span>
                  <div className="flex flex-wrap gap-1.5">
                    {lead.lead_lists.map((ll: LeadListRef, i: number) => (
                      <span
                        key={ll.id}
                        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium border ${
                          i === 0
                            ? "bg-info-soft/70 text-info border-info-line"
                            : "bg-muted text-muted-foreground border-border"
                        }`}
                        title={`Added ${new Date(ll.added_at.endsWith("Z") ? ll.added_at : ll.added_at + "Z").toLocaleDateString()}`}
                      >
                        {ll.name}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Campaign status + Notes & Activity (merged) */}
          <ActivityFeed
            lead={lead}
            notes={notes}
            notesLoading={notesLoading}
            activity={activity}
            activityLoading={activityLoading}
          />

        </div>

        {/* Right: social & links + additional data */}
        <div className="min-w-0 space-y-4">

          {/* Social card */}
          <div className="rounded-[14px] border border-border/80 bg-card shadow-soft p-4 space-y-1">
            <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Social & Links</h2>

            {/* LinkedIn */}
            <InlineField
              label="LinkedIn"
              value={lead.linkedin_url}
              placeholder="https://linkedin.com/in/..."
              href={(v) => v.startsWith("http") ? v : `https://${v}`}
              onSave={async (v) => {
                await save("linkedin_url", v);
              }}
            />

            {/* Twitter / X — Find button, scored candidates, outreach toggle */}
            <XSection
              lead={lead}
              onSave={async (v) => {
                await save("twitter_url", v ? `https://x.com/${v.replace(/^@/, "")}` : null);
              }}
              onToggleContacted={async (contacted) => {
                await update.mutateAsync({ x_contacted: contacted });
                toast.success(contacted ? "Marked as contacted on X" : "Cleared");
              }}
            />

            {/* Company / project X account, kept apart from the person's own */}
            {companyX && (
              <div className="flex gap-2 text-sm">
                <span className="text-muted-foreground min-w-[120px]">Company X</span>
                <a href={companyX} target="_blank" rel="noopener noreferrer" className="hover:underline">
                  @{companyX.replace(/.*(x|twitter)\.com\//, "")}
                </a>
              </div>
            )}

            {/* Telegram — prominent with Find button + outreach toggle */}
            <TelegramSection
              lead={lead}
              activity={activity}
              onSave={(v) => save("telegram_username", v)}
              onToggleContacted={async (contacted) => {
                await update.mutateAsync({ tg_contacted: contacted });
                toast.success(contacted ? "Marked as contacted" : "Cleared");
              }}
            />
          </div>

          {/* Extra data if any */}
          {(extraEntries.length > 0 || outreachMessage) && (
            <div className="rounded-[14px] border border-border/80 bg-card shadow-soft p-4">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Additional Data</h2>
              <div className="space-y-1.5">
                {outreachMessage && (
                  <div className="flex gap-2 text-sm">
                    <span className="text-muted-foreground min-w-[120px]">Message</span>
                    <p className="flex-1 whitespace-pre-wrap">{outreachMessage}</p>
                    <CopyButton text={outreachMessage} label="Copy message" />
                  </div>
                )}
                {extraEntries.map(([k, v]) => (
                  <div key={k} className="flex gap-2 text-sm">
                    <span className="text-muted-foreground min-w-[120px] capitalize">{k.replace(/_/g, " ")}</span>
                    <ExtraDataValue value={String(v)} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Outreach message rendered from the lead's signal data (never stored).
 *  Returns null unless signal_line, example_1 and example_2 are all present. */
function buildOutreachMessage(extra: Record<string, unknown> | null | undefined): string | null {
  const get = (k: string) => (typeof extra?.[k] === "string" ? (extra[k] as string).trim() : "");
  const signal = get("signal_line");
  const ex1 = get("example_1");
  const ex2 = get("example_2");
  if (!signal || !ex1 || !ex2) return null;
  return `I found companies that need you guys, they're all ${signal}\n\n${ex1} and ${ex2} are from this week should I send you more with contact info?`;
}

/** Renders an Additional Data value, collapsing long strings (e.g. the
 *  "keywords" list) behind a Show more/less toggle. */
function ExtraDataValue({ value }: { value: string }) {
  const [expanded, setExpanded] = useState(false);
  const LIMIT = 140;
  const isLong = value.length > LIMIT;

  if (!isLong) {
    return <span className="text-foreground break-all">{value}</span>;
  }

  return (
    <span className="text-foreground break-all">
      {expanded ? value : value.slice(0, LIMIT) + "…"}{" "}
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="text-primary hover:underline whitespace-nowrap text-xs font-medium"
      >
        {expanded ? "Show less" : "Show more"}
      </button>
    </span>
  );
}
