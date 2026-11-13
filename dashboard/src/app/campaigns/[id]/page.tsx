"use client";

import { use, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";
import { CampaignActivityChart } from "@/components/stats/campaign-chart";
import {
  useCampaign,
  useCampaignStats,
  useAccount,
  useAccounts,
  useUpdateCampaign,
  useActivateCampaign,
  usePauseCampaign,
  useResetLeads,
  useLeadLists,
  useAssignListToCampaign,
  useUnassignListFromCampaign,
  useReorderCampaignLists,
  useArchiveCampaign,
  useLeads,
  useCloneCampaign,
} from "@/hooks/use-queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { StatCard } from "@/components/stats/stat-card";
import { LeadsTable } from "@/components/leads/leads-table";
import { CSVUpload } from "@/components/leads/csv-upload";
import { ActivityTimeline } from "@/components/activity-timeline";
import {
  MessageTemplateEditor,
  VariableChips,
  BlankVariableHint,
  type MessageTemplateEditorHandle,
} from "@/components/message-template-editor";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { fetchAccountActivity, replanAccount } from "@/lib/api";
import { ResumeCampaignDialog, shouldOfferCatchup } from "@/components/resume-campaign-dialog";

type AssignedList = {
  id: string;
  name: string;
  total_leads: number;
  status_counts?: Record<string, number> | null;
};

function listInCampaignCount(counts: Record<string, number>): number {
  return (
    (counts["pending"] ?? 0) +
    (counts["scheduled"] ?? 0) +
    (counts["connection_requested"] ?? 0) +
    (counts["connected"] ?? 0) +
    (counts["followup_scheduled"] ?? 0) +
    (counts["followup_sent"] ?? 0) +
    (counts["completed"] ?? 0) +
    (counts["error"] ?? 0) +
    (counts["invalid"] ?? 0) +
    (counts["skipped"] ?? 0) +
    (counts["removed"] ?? 0) +
    (counts["withdrawn"] ?? 0)
  );
}

function isListDone(ll: AssignedList): boolean {
  const counts = ll.status_counts ?? {};
  const inCampaign = listInCampaignCount(counts);
  // A list is done when it has in-campaign leads but none are pending/scheduled.
  // Falls back to ll.total_leads > 0 for lists with no assignments yet.
  return (counts["pending"] ?? 0) === 0 && (counts["scheduled"] ?? 0) === 0 && (inCampaign > 0 || ll.total_leads > 0);
}

function SortableListRow({
  ll,
  idx,
  listsCount,
  onUnassign,
  isUnassigning,
}: {
  ll: AssignedList;
  idx: number;
  listsCount: number;
  onUnassign: () => void;
  isUnassigning: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: ll.id });

  const counts = ll.status_counts ?? {};
  const pending = counts["pending"] ?? 0;
  const scheduled = counts["scheduled"] ?? 0;
  const sent = counts["connection_requested"] ?? 0;
  const accepted = (counts["connected"] ?? 0) + (counts["followup_scheduled"] ?? 0) + (counts["followup_sent"] ?? 0) + (counts["completed"] ?? 0);
  const error = counts["error"] ?? 0;
  const invalid = counts["invalid"] ?? 0;
  const skipped = counts["skipped"] ?? 0;
  const removed = counts["removed"] ?? 0;
  const withdrawn = counts["withdrawn"] ?? 0;
  // Leads actually assigned to this campaign from this list.
  // Gap vs ll.total_leads = duplicates already present from another list — not missing data.
  const inCampaign = listInCampaignCount(counts);
  const total = inCampaign > 0 ? inCampaign : ll.total_leads;
  // skipped/error/invalid/removed/withdrawn are terminal states — count as "processed" for progress
  const other = error + invalid + skipped + removed + withdrawn;
  const done = sent + accepted + other;
  const donePct = total > 0 ? (done / total) * 100 : 0;
  const acceptedPct = total > 0 ? (accepted / total) * 100 : 0;
  const sentPct = total > 0 ? (sent / total) * 100 : 0;
  const otherPct = total > 0 ? (other / total) * 100 : 0;

  // Status: dispatching today > queued (has pending) > done > empty
  const isDispatching = scheduled > 0;
  const isQueued = pending > 0 && !isDispatching;
  const isDone = pending === 0 && scheduled === 0 && total > 0;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex items-center gap-3 rounded-md border px-3 py-2.5 text-sm transition-colors ${
        isDispatching
          ? "border-emerald-200 bg-emerald-50/50 dark:border-emerald-800/50 dark:bg-emerald-950/20"
          : "border-border bg-background"
      } ${isDragging ? "opacity-50 shadow-lg" : ""}`}
    >
      {/* Drag handle */}
      {listsCount > 1 ? (
        <button
          type="button"
          className="shrink-0 cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground touch-none"
          {...attributes}
          {...listeners}
        >
          <GripVertical className="h-4 w-4" />
        </button>
      ) : (
        <div className="w-4 shrink-0" />
      )}

      {/* Rank badge */}
      <span className="shrink-0 flex h-5 min-w-[22px] items-center justify-center rounded bg-muted px-1.5 text-[10px] font-semibold tabular-nums text-foreground">
        {idx + 1}
      </span>

      {/* Name + status */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <Link
            href={`/lead-lists/${ll.id}`}
            className="font-medium hover:underline truncate"
          >
            {ll.name}
          </Link>
          {isDispatching && (
            <span className="shrink-0 inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              Active today
            </span>
          )}
          {isQueued && (
            <span className="shrink-0 rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700 dark:bg-blue-900/40 dark:text-blue-400">
              Queued
            </span>
          )}
          {isDone && (
            <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Done
            </span>
          )}
        </div>

        {/* Progress bar */}
        {total > 0 && (
          <div className="mt-1.5 flex items-center gap-2">
            <div className="flex h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
              <div className="h-full bg-emerald-500 transition-all" style={{ width: `${acceptedPct}%` }} />
              <div className="h-full bg-blue-400 transition-all" style={{ width: `${sentPct}%` }} />
              <div className="h-full bg-muted-foreground/25 transition-all" style={{ width: `${otherPct}%` }} />
            </div>
            <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
              {Math.round(donePct)}%
            </span>
          </div>
        )}
      </div>

      {/* Counts */}
      <div className="shrink-0 text-right text-xs tabular-nums space-y-0.5 hidden sm:block">
        {(pending > 0 || scheduled > 0) && (
          <div className={scheduled > 0 ? "text-emerald-600 dark:text-emerald-400 font-medium" : "text-muted-foreground"}>
            {scheduled > 0 ? `${scheduled} sched` : `${pending} pending`}
          </div>
        )}
        <div className="text-muted-foreground">{total} in campaign</div>
      </div>

      {/* Unassign */}
      <Button
        size="sm"
        variant="ghost"
        disabled={isUnassigning}
        onClick={onUnassign}
        className="shrink-0 text-xs text-muted-foreground hover:text-destructive"
      >
        Unassign
      </Button>
    </div>
  );
}

type EditorKey = "conn" | "m1" | "m2" | "m3";
const EDITOR_LABELS: Record<EditorKey, string> = {
  conn: "connection note", m1: "message 1", m2: "message 2", m3: "message 3",
};

function fmtHours(h: number) {
  return h < 1 ? `${Math.round(h * 60)}m` : `${Math.round(h * 10) / 10}h`;
}

/** Timeline connector row between sequence steps. */
function SeqWait({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative pl-7 py-2 text-xs text-muted-foreground flex flex-wrap items-center gap-2">
      <span className="absolute left-[7px] top-0 bottom-0 w-px bg-border" />
      {children}
    </div>
  );
}

/** Numbered sequence step with its message editor. */
function SeqStep({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <div className="relative pl-7">
      <span className="absolute left-0 top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-foreground text-background text-[10px] font-medium">
        {n}
      </span>
      {children}
    </div>
  );
}

export default function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: campaign, isLoading } = useCampaign(id);
  const { data: campaignStats } = useCampaignStats(id); // summary.total_sent is always all-time
  const { data: account } = useAccount(campaign?.account_id ?? "", {
    enabled: !!campaign?.account_id,
  });
  const activate = useActivateCampaign();
  const pause = usePauseCampaign();
  const resetLeads = useResetLeads();
  const [resumeDialogOpen, setResumeDialogOpen] = useState(false);
  const updateCampaign = useUpdateCampaign(id);
  const archiveCampaign = useArchiveCampaign();
  const cloneCampaign = useCloneCampaign();
  const { data: allLists } = useLeadLists();
  const { data: allAccounts } = useAccounts();
  const assign = useAssignListToCampaign();
  const unassign = useUnassignListFromCampaign();
  const reorderLists = useReorderCampaignLists();
  const { data: leadSample } = useLeads(id, { per_page: 200 });
  const dndSensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );
  const handleListDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const allAssigned = campaign?.assigned_lists ?? [];
    const activeLists = allAssigned.filter((l) => !isListDone(l));
    const doneLists = allAssigned.filter((l) => isListDone(l));
    const oldIndex = activeLists.findIndex((l) => l.id === active.id);
    const newIndex = activeLists.findIndex((l) => l.id === over.id);
    const reordered = arrayMove(activeLists, oldIndex, newIndex);
    reorderLists.mutate(
      { campaignId: id, orderedListIds: [...reordered, ...doneLists].map((l) => l.id) },
      { onError: (err) => toast.error(err.message) }
    );
  };

  // Clone dialog state
  const [cloneDialogOpen, setCloneDialogOpen] = useState(false);
  const [cloneName, setCloneName] = useState("");
  const [cloneAccountId, setCloneAccountId] = useState("");

  // Compute per-variable fill rates from the lead sample (0–1).
  const { fillRates, customVariables, customVariableExamples } = useMemo(() => {
    // Score only leads that can still be messaged; skipped/removed/invalid
    // rows from old imports would drag the % down for no reason.
    const DEAD = new Set(["skipped", "removed", "invalid", "error", "withdrawn"]);
    const all = leadSample?.items ?? [];
    const live = all.filter((l) => !DEAD.has(String(l.status).toLowerCase()));
    const leads = live.length > 0 ? live : all;
    if (leads.length === 0) return { fillRates: undefined, customVariables: [] as string[] };
    const total = leads.length;
    const FIELD_MAP: Record<string, "first_name" | "last_name" | "company" | "title"> = {
      first_name: "first_name",
      firstname:  "first_name",
      last_name:  "last_name",
      lastname:   "last_name",
      company:    "company",
      title:      "title",
    };
    const rates: Record<string, number> = {};
    for (const [token, field] of Object.entries(FIELD_MAP)) {
      const filled = leads.filter(l => l[field] != null && l[field] !== "").length;
      rates[token] = filled / total;
    }
    // extra_data keys
    const extraKeys = new Set<string>();
    for (const lead of leads) {
      if (lead.extra_data) {
        for (const k of Object.keys(lead.extra_data)) extraKeys.add(k.toLowerCase());
      }
    }
    for (const key of extraKeys) {
      const filled = leads.filter(l =>
        l.extra_data && key in l.extra_data &&
        l.extra_data[key] != null && l.extra_data[key] !== ""
      ).length;
      rates[key] = filled / total;
    }
    const standardTokens = new Set(Object.keys(FIELD_MAP));
    const examples: Record<string, string> = {};
    for (const key of extraKeys) {
      const lead = leads.find(l => l.extra_data && l.extra_data[key] != null && l.extra_data[key] !== "");
      if (lead?.extra_data) examples[key] = String(lead.extra_data[key]);
    }
    return { fillRates: rates, customVariables: [...extraKeys].filter(k => !standardTokens.has(k)), customVariableExamples: examples };
  }, [leadSample]);

  const [showDoneLists, setShowDoneLists] = useState(false);
  const [selectedList, setSelectedList] = useState("");
  const [editName, setEditName] = useState("");
  const [editConnMsg, setEditConnMsg] = useState("");
  const [editFilterNoPhoto, setEditFilterNoPhoto] = useState(false);
  const [editMinConnections, setEditMinConnections] = useState("");
  const [editExcludeOpenToWork, setEditExcludeOpenToWork] = useState(false);
  const [editFollowupEnabled, setEditFollowupEnabled] = useState(false);
  const [editFollowupDelayHours, setEditFollowupDelayHours] = useState("");
  const [editFollowupMsg1, setEditFollowupMsg1] = useState("");
  const [editFollowupMsg2, setEditFollowupMsg2] = useState("");
  const [editFollowupMsg3, setEditFollowupMsg3] = useState("");
  const [editWeekendEnabled, setEditWeekendEnabled] = useState(false);
  const [editStep2Delay, setEditStep2Delay] = useState("");
  const [settingsInit, setSettingsInit] = useState(false);
  // Shared variable bar inserts into whichever message field was focused last.
  const connEditorRef = useRef<MessageTemplateEditorHandle>(null);
  const m1EditorRef = useRef<MessageTemplateEditorHandle>(null);
  const m2EditorRef = useRef<MessageTemplateEditorHandle>(null);
  const m3EditorRef = useRef<MessageTemplateEditorHandle>(null);
  const [activeEditor, setActiveEditor] = useState<EditorKey>("m1");
  function insertIntoActiveEditor(token: string) {
    const target = { conn: connEditorRef, m1: m1EditorRef, m2: m2EditorRef, m3: m3EditorRef }[activeEditor];
    target.current?.insertToken(token);
  }

  const { data: activity } = useQuery({
    queryKey: ["campaign-activity", campaign?.account_id],
    queryFn: () => fetchAccountActivity(campaign!.account_id),
    enabled: !!campaign?.account_id,
  });

  // Include campaign-scoped entries plus account-scoped system entries
  // (e.g. ACCEPTANCE_CHECK_SUMMARY has no campaign_id but is relevant here).
  const campaignActivity =
    activity?.filter(
      (a) =>
        a.campaign_id === id ||
        a.action_type.toUpperCase() === "ACCEPTANCE_CHECK_SUMMARY",
    ) ?? [];

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      </div>
    );
  }

  if (!campaign) {
    return <p className="text-muted-foreground">Campaign not found</p>;
  }

  if (!settingsInit) {
    setEditName(campaign.name);
    setEditConnMsg(campaign.connection_message_template ?? "");
    setEditFilterNoPhoto(campaign.filter_no_photo);
    setEditMinConnections(campaign.filter_min_connections != null ? String(campaign.filter_min_connections) : "");
    setEditExcludeOpenToWork(campaign.filter_exclude_open_to_work);
    setEditFollowupEnabled(campaign.followup_enabled);
    setEditFollowupDelayHours(String(campaign.followup_delay_hours));
    setEditFollowupMsg1(campaign.followup_message_1 ?? "");
    setEditFollowupMsg2(campaign.followup_message_2 ?? "");
    setEditFollowupMsg3(campaign.followup_message_3 ?? "");
    setEditWeekendEnabled(campaign.weekend_enabled);
    setEditStep2Delay(String(campaign.followup_step2_delay_hours ?? 0));
    setSettingsInit(true);
  }

  const counts = campaign.status_counts ?? {};
  const totalLeads = Object.values(counts).reduce((a, b) => a + b, 0);
  const pending = counts["pending"] ?? 0;
  // total_sent from action_log (authoritative): counts all successful dispatches
  // regardless of current lead status (accepted, withdrawn, still pending, etc.)
  const sent = campaignStats?.summary.total_sent ?? (counts["connection_requested"] ?? 0);
  // "connected" includes all post-acceptance statuses: CONNECTED (awaiting followup),
  // FOLLOWUP_SCHEDULED, FOLLOWUP_SENT, COMPLETED. Leads move through these states
  // sequentially, so counting only CONNECTED would show 0 once followup is dispatched.
  const connected =
    (counts["connected"] ?? 0) +
    (counts["followup_scheduled"] ?? 0) +
    (counts["followup_sent"] ?? 0) +
    (counts["completed"] ?? 0);
  const errors = counts["error"] ?? 0;
  const acceptRate = campaignStats?.summary.acceptance_rate ?? null;

  const assignedListIds = new Set((campaign.assigned_lists ?? []).map((l) => l.id));
  const availableLists = (allLists ?? []).filter((ll) => !assignedListIds.has(ll.id));

  function handleSaveSettings() {
    const data: Record<string, unknown> = {};
    if (editName !== campaign!.name) data.name = editName;
    if (editConnMsg !== (campaign!.connection_message_template ?? ""))
      data.connection_message_template = editConnMsg || null;
    if (editFilterNoPhoto !== campaign!.filter_no_photo) data.filter_no_photo = editFilterNoPhoto;
    const minConn = editMinConnections ? Number(editMinConnections) : null;
    if (minConn !== campaign!.filter_min_connections) data.filter_min_connections = minConn;
    if (editExcludeOpenToWork !== campaign!.filter_exclude_open_to_work) data.filter_exclude_open_to_work = editExcludeOpenToWork;
    if (editFollowupEnabled !== campaign!.followup_enabled) data.followup_enabled = editFollowupEnabled;
    const delayHours = editFollowupDelayHours ? Number(editFollowupDelayHours) : 0;
    if (delayHours !== campaign!.followup_delay_hours) data.followup_delay_hours = delayHours;
    if (editFollowupMsg1 !== (campaign!.followup_message_1 ?? "")) data.followup_message_1 = editFollowupMsg1 || null;
    if (editFollowupMsg2 !== (campaign!.followup_message_2 ?? "")) data.followup_message_2 = editFollowupMsg2 || null;
    if (editFollowupMsg3 !== (campaign!.followup_message_3 ?? "")) data.followup_message_3 = editFollowupMsg3 || null;
    if (editWeekendEnabled !== campaign!.weekend_enabled) data.weekend_enabled = editWeekendEnabled;
    const step2 = editStep2Delay ? Number(editStep2Delay) : 0;
    if (step2 !== (campaign!.followup_step2_delay_hours ?? 0)) data.followup_step2_delay_hours = step2;
    // Re-planning queues a connection request right away, so only do it when
    // something that affects connection sending changed.
    const affectsConnections = ["connection_message_template", "filter_no_photo",
      "filter_min_connections", "filter_exclude_open_to_work", "weekend_enabled"].some((k) => k in data);
    if (Object.keys(data).length === 0) {
      toast.info("No changes to save");
      return;
    }

    // Warn if any saved template references a variable that is empty for every lead.
    if (fillRates) {
      const templatesToCheck = [editConnMsg, editFollowupMsg1, editFollowupMsg2, editFollowupMsg3].filter(Boolean);
      const zeroVars = new Set<string>();
      for (const tmpl of templatesToCheck) {
        for (const m of tmpl.matchAll(/\{\{(\w+)\}\}/g)) {
          if (fillRates[m[1].toLowerCase()] === 0) zeroVars.add(m[1].toLowerCase());
        }
      }
      if (zeroVars.size > 0) {
        toast.warning(
          `${[...zeroVars].map(v => `{{${v}}}`).join(", ")} ${zeroVars.size === 1 ? "has" : "have"} no data for any lead — will always render as empty.`
        );
      }
    }

    updateCampaign.mutate(data, {
      onSuccess: () => {
        toast.success("Campaign settings saved");
        if (campaign?.account_id && affectsConnections) {
          replanAccount(campaign.account_id).then(() =>
            toast.success("Schedule regenerated (1 lead queued immediately)")
          ).catch(() => {});
        }
      },
      onError: (err) => toast.error(err.message),
    });
  }

  const seqBase = Number(editFollowupDelayHours) || 0;
  const seqStep2 = Number(editStep2Delay) || 0;
  const seqOff = !editFollowupEnabled;

  return (
    <div>
      {/* Sticky header */}
      <div className="sticky top-0 z-20 bg-background border-b border-border -mx-6 px-6 py-3 mb-6">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <h1 className="text-lg font-semibold truncate">{campaign.name}</h1>
            <StatusBadge status={campaign.status} />
            {account && (
              <Link
                href={`/accounts/${campaign.account_id}`}
                className="hidden sm:flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
                {account.name}
              </Link>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {(campaign.status === "draft" || campaign.status === "paused") && (
              <Button
                size="sm"
                onClick={() => {
                  if (campaign.status === "paused" && shouldOfferCatchup(campaign)) {
                    setResumeDialogOpen(true);
                  } else {
                    activate.mutate(id, { onSuccess: () => toast.success("Campaign activated") });
                  }
                }}
                disabled={activate.isPending}
              >
                Activate
              </Button>
            )}
            {campaign.status === "active" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => pause.mutate(id, { onSuccess: () => toast.success("Campaign paused") })}
                disabled={pause.isPending}
              >
                Pause
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                resetLeads.mutate(id, {
                  onSuccess: (data) => toast.success(`Reset ${data.reset_count} leads`),
                })
              }
              disabled={resetLeads.isPending}
            >
              Reset Errors
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={handleSaveSettings}
              disabled={updateCampaign.isPending}
            >
              Save Settings
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setCloneName(`${campaign.name} (copy)`);
                setCloneAccountId(campaign.account_id);
                setCloneDialogOpen(true);
              }}
            >
              Clone
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground hover:text-destructive"
              onClick={() => {
                if (confirm("Archive this campaign? It will be paused and hidden from the main view.")) {
                  archiveCampaign.mutate(id, {
                    onSuccess: () => {
                      toast.success("Campaign archived");
                      router.push("/campaigns");
                    },
                    onError: (err) => toast.error(err.message),
                  });
                }
              }}
              disabled={archiveCampaign.isPending}
            >
              Archive
            </Button>
          </div>
        </div>
      </div>

      <div className="space-y-6">
        {/* Summary strip — always visible above tabs */}
        <div className={`grid gap-3 ${campaign.account_dispatch_mode === "continuous" ? "grid-cols-3 md:grid-cols-7" : "grid-cols-3 md:grid-cols-6"}`}>
          <StatCard label="Total Leads" value={totalLeads} />
          <StatCard label="Pending" value={pending} />
          <StatCard label="Sent" value={sent} />
          <StatCard label="Connected" value={connected} />
          <StatCard
            label="Accept Rate"
            value={acceptRate !== null ? `${acceptRate}%` : "—"}
          />
          <StatCard label="Errors" value={errors} />
          {campaign.account_dispatch_mode === "continuous" && (
            <StatCard
              label="Expected Today"
              value={campaign.estimated_remaining_today ?? "—"}
            />
          )}
        </div>

        {account?.paused_until && new Date(account.paused_until) > new Date() && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3">
            <p className="text-sm text-amber-400">
              Account <span className="font-medium">{account.name}</span> is paused due to weekly limit — resumes{" "}
              {new Date(account.paused_until.endsWith("Z") ? account.paused_until : account.paused_until + "Z").toLocaleString(undefined, {
                timeZone: account.timezone ?? undefined,
                weekday: "short",
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </p>
          </div>
        )}

        <Tabs defaultValue="stats">
          <TabsList>
            <TabsTrigger value="stats">Stats</TabsTrigger>
            <TabsTrigger value="leads">Leads</TabsTrigger>
            <TabsTrigger value="lists">Lists</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
          </TabsList>

          <TabsContent value="stats" className="mt-4 space-y-4">
            <Card>
              <CardContent className="pt-6">
                <CampaignActivityChart campaignId={id} totalLeads={totalLeads} />
              </CardContent>
            </Card>

            {/* Conversion Funnel */}
            {totalLeads > 0 && (() => {
              const sc = campaign.status_counts ?? {};
              const removed = sc["removed"] ?? 0;
              const imported = totalLeads - removed;
              const requested = (sc["connection_requested"] ?? 0) + (sc["connected"] ?? 0) + (sc["followup_scheduled"] ?? 0) + (sc["followup_sent"] ?? 0) + (sc["completed"] ?? 0);
              const connectedCount = (sc["connected"] ?? 0) + (sc["followup_scheduled"] ?? 0) + (sc["followup_sent"] ?? 0) + (sc["completed"] ?? 0);
              const followedUp = (sc["followup_sent"] ?? 0) + (sc["completed"] ?? 0);

              const funnelStages = [
                { label: "Imported", count: imported, base: imported },
                { label: "Requested", count: requested, base: imported },
                { label: "Connected", count: connectedCount, base: requested },
                { label: "Followed Up", count: followedUp, base: connectedCount },
              ];

              return (
                <Card>
                  <CardHeader>
                    <CardTitle>Conversion Funnel</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2">
                      {funnelStages.map((stage, i) => {
                        const pct = stage.base > 0 ? Math.round((stage.count / stage.base) * 100) : 0;
                        const barWidth = stage.base > 0 ? (stage.count / stage.base) * 100 : 0;
                        return (
                          <div key={stage.label} className="grid grid-cols-[100px_1fr_60px_48px] items-center gap-3">
                            <span className="text-xs text-muted-foreground text-right truncate">{stage.label}</span>
                            <div className="h-5 w-full rounded bg-muted overflow-hidden">
                              <div
                                className={`h-full rounded transition-all ${i === 0 ? "bg-zinc-500" : i === 1 ? "bg-blue-500" : i === 2 ? "bg-emerald-500" : "bg-emerald-600"}`}
                                style={{ width: `${barWidth}%` }}
                              />
                            </div>
                            <span className="text-xs text-right tabular-nums">{stage.count.toLocaleString()}</span>
                            <span className={`text-xs text-right tabular-nums font-medium ${i === 0 ? "text-muted-foreground" : pct >= 40 ? "text-emerald-500" : pct >= 20 ? "text-amber-500" : "text-red-400"}`}>
                              {i === 0 ? "100%" : `${pct}%`}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                    <p className="text-xs text-muted-foreground mt-3">Requested, Connected, and Followed Up percentages are relative to the previous stage.</p>
                  </CardContent>
                </Card>
              );
            })()}

            {(campaign.assigned_lists ?? []).length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>List Performance</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border text-left text-xs text-muted-foreground">
                          <th className="pb-2 font-medium">List</th>
                          <th className="pb-2 font-medium text-right">Leads</th>
                          <th className="pb-2 font-medium text-right">Sent</th>
                          <th className="pb-2 font-medium text-right">Connected</th>
                          <th className="pb-2 font-medium text-right">Accept Rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(campaign.assigned_lists ?? []).map((ll) => {
                          const sc = ll.status_counts ?? {};
                          const listConnected = (sc["connected"] ?? 0) + (sc["completed"] ?? 0) + (sc["followup_sent"] ?? 0) + (sc["followup_scheduled"] ?? 0);
                          const listSent = sc["connection_requested"] ?? 0;
                          const listWithdrawn = sc["withdrawn"] ?? 0;
                          const listProcessed = listConnected + listSent + listWithdrawn;
                          const listRate = listProcessed > 0 ? Math.round((listConnected / listProcessed) * 100) : null;
                          const listInCampaign = listInCampaignCount(sc);
                          return (
                            <tr key={ll.id} className="border-b border-border/50 last:border-0">
                              <td className="py-2 font-medium">{ll.name}</td>
                              <td className="py-2 text-right text-muted-foreground">{listInCampaign || ll.total_leads}</td>
                              <td className="py-2 text-right text-muted-foreground">{listSent + listConnected + listWithdrawn}</td>
                              <td className="py-2 text-right text-muted-foreground">{listConnected}</td>
                              <td className="py-2 text-right">
                                {listRate !== null ? (
                                  <span className={
                                    listRate >= 30 ? "text-emerald-500" :
                                    listRate >= 15 ? "text-amber-500" :
                                    "text-red-500"
                                  }>
                                    {listRate}%
                                  </span>
                                ) : (
                                  <span className="text-muted-foreground">—</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          <TabsContent value="leads" className="space-y-4 mt-4">
            <CSVUpload campaignId={id} />
            <LeadsTable
              campaignId={id}
              timezone={account?.timezone}
              assignedLists={campaign.assigned_lists}
              campaignName={campaign.name}
            />
          </TabsContent>

          <TabsContent value="lists" className="mt-4">
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <CardTitle>Assigned Lists</CardTitle>
                  {(campaign.assigned_lists ?? []).length > 0 && (
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <span className="h-2 w-2 rounded-full bg-emerald-500" /> Connected
                      </span>
                      <span className="flex items-center gap-1">
                        <span className="h-2 w-2 rounded-full bg-blue-400" /> Requested
                      </span>
                      <span className="flex items-center gap-1">
                        <span className="h-2 w-2 rounded-full bg-muted-foreground/30 border border-border" /> Remaining
                      </span>
                    </div>
                  )}
                </div>
                {(campaign.assigned_lists ?? []).length > 1 && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Dispatched top-to-bottom. Daily budget fills from #1 first — drag to reorder.
                  </p>
                )}
              </CardHeader>
              <CardContent className="space-y-3">
                {(campaign.assigned_lists ?? []).length === 0 && (
                  <p className="text-sm text-muted-foreground">No lists assigned to this campaign.</p>
                )}
                {(campaign.assigned_lists ?? []).length > 0 && (() => {
                  const allAssigned = campaign.assigned_lists ?? [];
                  const activeLists = allAssigned.filter((l) => !isListDone(l));
                  const doneLists = allAssigned.filter((l) => isListDone(l));
                  return (
                    <>
                      <DndContext sensors={dndSensors} collisionDetection={closestCenter} onDragEnd={handleListDragEnd}>
                        <SortableContext items={activeLists.map((l) => l.id)} strategy={verticalListSortingStrategy}>
                          <div className="space-y-1.5">
                            {activeLists.map((ll, idx) => (
                              <SortableListRow
                                key={ll.id}
                                ll={ll}
                                idx={idx}
                                listsCount={activeLists.length}
                                isUnassigning={unassign.isPending}
                                onUnassign={() =>
                                  unassign.mutate(
                                    { listId: ll.id, campaignId: id },
                                    {
                                      onSuccess: (data) =>
                                        toast.success(`Unassigned — ${data.leads_removed} leads removed`),
                                      onError: (err) => toast.error(err.message),
                                    }
                                  )
                                }
                              />
                            ))}
                          </div>
                        </SortableContext>
                      </DndContext>

                      {doneLists.length > 0 && (
                        <div className="pt-1">
                          <button
                            type="button"
                            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                            onClick={() => setShowDoneLists((v) => !v)}
                          >
                            <svg
                              className={`h-3 w-3 transition-transform ${showDoneLists ? "rotate-90" : ""}`}
                              fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                            >
                              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                            </svg>
                            {showDoneLists ? "Hide" : "Show"} {doneLists.length} done list{doneLists.length !== 1 ? "s" : ""}
                          </button>
                          {showDoneLists && (
                            <div className="mt-1.5 space-y-1.5">
                              {doneLists.map((ll, doneIdx) => {
                                const counts = ll.status_counts ?? {};
                                const accepted = (counts["connected"] ?? 0) + (counts["followup_scheduled"] ?? 0) + (counts["followup_sent"] ?? 0) + (counts["completed"] ?? 0);
                                const sent = counts["connection_requested"] ?? 0;
                                const err = (counts["error"] ?? 0) + (counts["invalid"] ?? 0) + (counts["skipped"] ?? 0);
                                const inCamp = listInCampaignCount(counts);
                                const total = inCamp > 0 ? inCamp : ll.total_leads;
                                const acceptedPct = total > 0 ? (accepted / total) * 100 : 0;
                                const sentPct = total > 0 ? (sent / total) * 100 : 0;
                                const donePct = total > 0 ? ((sent + accepted + err) / total) * 100 : 0;
                                const rank = activeLists.length + doneIdx + 1;
                                return (
                                  <div
                                    key={ll.id}
                                    className="flex items-center gap-3 rounded-md border border-border/50 bg-muted/20 px-3 py-2.5 text-sm opacity-60"
                                  >
                                    <div className="w-4 shrink-0" />
                                    <span className="shrink-0 flex h-5 min-w-[22px] items-center justify-center rounded bg-muted px-1.5 text-[10px] font-semibold tabular-nums text-foreground">
                                      {rank}
                                    </span>
                                    <div className="min-w-0 flex-1">
                                      <div className="flex items-center gap-2 flex-wrap">
                                        <Link href={`/lead-lists/${ll.id}`} className="font-medium hover:underline truncate">
                                          {ll.name}
                                        </Link>
                                        <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                                          Done
                                        </span>
                                      </div>
                                      {total > 0 && (
                                        <div className="mt-1.5 flex items-center gap-2">
                                          <div className="flex h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
                                            <div className="h-full bg-emerald-500 transition-all" style={{ width: `${acceptedPct}%` }} />
                                            <div className="h-full bg-blue-400 transition-all" style={{ width: `${sentPct}%` }} />
                                          </div>
                                          <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                                            {Math.round(donePct)}%
                                          </span>
                                        </div>
                                      )}
                                    </div>
                                    <div className="shrink-0 text-right text-xs tabular-nums space-y-0.5 hidden sm:block">
                                      <div className="text-muted-foreground">{total} in campaign</div>
                                    </div>
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      disabled={unassign.isPending}
                                      onClick={() =>
                                        unassign.mutate(
                                          { listId: ll.id, campaignId: id },
                                          {
                                            onSuccess: (data) => toast.success(`Unassigned — ${data.leads_removed} leads removed`),
                                            onError: (err) => toast.error(err.message),
                                          }
                                        )
                                      }
                                      className="shrink-0 text-xs text-muted-foreground hover:text-destructive"
                                    >
                                      Unassign
                                    </Button>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      )}
                    </>
                  );
                })()}

                {availableLists.length > 0 && (
                  <div className="flex gap-2 pt-2">
                    <select
                      className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
                      value={selectedList}
                      onChange={(e) => setSelectedList(e.target.value)}
                    >
                      <option value="">Select a list to assign...</option>
                      {availableLists.map((ll) => (
                        <option key={ll.id} value={ll.id}>
                          {ll.name} ({ll.total_leads} leads)
                        </option>
                      ))}
                    </select>
                    <Button
                      disabled={!selectedList || assign.isPending}
                      onClick={() => {
                        assign.mutate(
                          { listId: selectedList, campaignId: id },
                          {
                            onSuccess: (data) => {
                              toast.success(`Assigned — ${data.leads_added} leads added`);
                              setSelectedList("");
                            },
                            onError: (err) => toast.error(err.message),
                          }
                        );
                      }}
                    >
                      Assign
                    </Button>
                  </div>
                )}

                {availableLists.length === 0 && (campaign.assigned_lists ?? []).length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    No lists available.{" "}
                    <Link href="/lead-lists" className="text-blue-500 hover:underline">
                      Create one
                    </Link>
                  </p>
                )}

                <p className="text-xs text-muted-foreground pt-1">
                  Assigning a list copies its leads into this campaign. Unassigning marks those leads as removed.
                </p>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="settings" className="mt-4">
            <div className="max-w-3xl space-y-4">
              {/* One variable bar for every message field on this tab */}
              <div className="sticky top-[57px] z-10 rounded-lg border border-border bg-background/95 backdrop-blur px-3 py-2 space-y-1">
                <VariableChips
                  label={`Insert into ${EDITOR_LABELS[activeEditor]}:`}
                  onInsert={insertIntoActiveEditor}
                  customVariables={customVariables}
                  fillRates={fillRates}
                  usedText={[editConnMsg, editFollowupMsg1, editFollowupMsg2, editFollowupMsg3].join(" ")}
                />
                <BlankVariableHint />
              </div>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Connection request</CardTitle>
                </CardHeader>
                <CardContent className="space-y-5">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground" htmlFor="campaignName">Campaign name</label>
                      <Input id="campaignName" value={editName} onChange={(e) => setEditName(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Account</p>
                      <p className="text-sm h-9 flex items-center">{campaign.account_name ?? campaign.account_id}</p>
                    </div>
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground block">
                      Note <span className="text-muted-foreground/70">(optional, blank sends without a note)</span>
                    </label>
                    <MessageTemplateEditor
                      ref={connEditorRef}
                      onFocus={() => setActiveEditor("conn")}
                      hideChips
                      hideHint
                      value={editConnMsg}
                      onChange={setEditConnMsg}
                      placeholder="Hi {{first_name}}, I came across your profile and would love to connect."
                      minHeight={90}
                      showCharLimit
                      fillRates={fillRates}
                      customVariables={customVariables}
                      customVariableExamples={customVariableExamples}
                    />
                  </div>

                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">Skip profiles that</p>
                    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
                      <label className="flex items-center gap-2">
                        <input type="checkbox" checked={editFilterNoPhoto} onChange={(e) => setEditFilterNoPhoto(e.target.checked)} />
                        have no photo
                      </label>
                      <label className="flex items-center gap-2">
                        <input type="checkbox" checked={editExcludeOpenToWork} onChange={(e) => setEditExcludeOpenToWork(e.target.checked)} />
                        are &ldquo;Open to Work&rdquo;
                      </label>
                      <label className="flex items-center gap-2">
                        have fewer than
                        <Input
                          type="number"
                          min="0"
                          value={editMinConnections}
                          onChange={(e) => setEditMinConnections(e.target.value)}
                          placeholder="any"
                          className="h-7 w-20"
                        />
                        connections
                      </label>
                    </div>
                  </div>

                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={editWeekendEnabled} onChange={(e) => setEditWeekendEnabled(e.target.checked)} />
                    Also send on weekends
                  </label>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3 flex flex-row items-center justify-between space-y-0">
                  <CardTitle className="text-base">Follow-up sequence</CardTitle>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={editFollowupEnabled} onChange={(e) => setEditFollowupEnabled(e.target.checked)} />
                    On
                  </label>
                </CardHeader>
                <CardContent className={editFollowupEnabled ? "" : "opacity-60"}>
                      <div>
                        <div className="relative pl-7 text-sm font-medium">
                          <span className="absolute left-[3px] top-1.5 h-2 w-2 rounded-full bg-muted-foreground" />
                          Connection accepted
                        </div>
                        <SeqWait>
                          Wait
                          <Input
                            type="number"
                            min="0"
                            value={editFollowupDelayHours}
                            onChange={(e) => setEditFollowupDelayHours(e.target.value)}
                            disabled={seqOff}
                            className="h-7 w-16"
                          />
                          hours
                          {seqBase > 0 && <span>(randomized per lead: {fmtHours(seqBase * 0.75)} to {fmtHours(seqBase * 1.75)})</span>}
                        </SeqWait>
                        <SeqStep n={1}>
                          <MessageTemplateEditor
                            ref={m1EditorRef}
                            onFocus={() => setActiveEditor("m1")}
                            hideChips
                            hideHint
                            value={editFollowupMsg1}
                            onChange={setEditFollowupMsg1}
                            placeholder="Hi {{first_name}}, thanks for connecting!"
                            disabled={seqOff}
                            minHeight={72}
                            fillRates={fillRates}
                            customVariables={customVariables}
                            customVariableExamples={customVariableExamples}
                          />
                        </SeqStep>
                        <SeqWait>
                          Wait
                          <Input
                            type="number"
                            min="0"
                            step="0.5"
                            value={editStep2Delay}
                            onChange={(e) => setEditStep2Delay(e.target.value)}
                            disabled={seqOff || !editFollowupMsg2}
                            className="h-7 w-16"
                          />
                          hours
                          <span>
                            {seqStep2 > 0
                              ? `(randomized: ${fmtHours(seqStep2 * 0.8)} to ${fmtHours(seqStep2 * 1.3)}; skipped if they reply first)`
                              : "(0 sends it right after message 1)"}
                          </span>
                        </SeqWait>
                        <SeqStep n={2}>
                          <MessageTemplateEditor
                            ref={m2EditorRef}
                            onFocus={() => setActiveEditor("m2")}
                            hideChips
                            hideHint
                            value={editFollowupMsg2}
                            onChange={setEditFollowupMsg2}
                            placeholder="Optional"
                            disabled={seqOff || !editFollowupMsg1}
                            minHeight={72}
                            fillRates={fillRates}
                            customVariables={customVariables}
                            customVariableExamples={customVariableExamples}
                          />
                        </SeqStep>
                        <SeqWait>A few seconds later</SeqWait>
                        <SeqStep n={3}>
                          <MessageTemplateEditor
                            ref={m3EditorRef}
                            onFocus={() => setActiveEditor("m3")}
                            hideChips
                            hideHint
                            value={editFollowupMsg3}
                            onChange={setEditFollowupMsg3}
                            placeholder="Optional"
                            disabled={seqOff || !editFollowupMsg2}
                            minHeight={72}
                            fillRates={fillRates}
                            customVariables={customVariables}
                            customVariableExamples={customVariableExamples}
                          />
                        </SeqStep>
                        <p className="pl-7 pt-3 text-xs text-muted-foreground">
                          A message that would have an empty variable is not sent, and the sequence stops there.
                        </p>
                      </div>

                </CardContent>
              </Card>

              <div className="flex justify-end">
                <Button onClick={handleSaveSettings} disabled={updateCampaign.isPending}>
                  Save settings
                </Button>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <ActivityTimeline logs={campaignActivity} timezone={account?.timezone} />
          </TabsContent>
        </Tabs>
      </div>
      <ResumeCampaignDialog
        campaign={campaign}
        open={resumeDialogOpen}
        onOpenChange={setResumeDialogOpen}
      />

      {/* Clone Campaign Dialog */}
      {cloneDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setCloneDialogOpen(false)}>
          <div className="bg-background border border-border rounded-xl shadow-xl w-full max-w-md mx-4 p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-base font-semibold">Clone Campaign</h2>
            <p className="text-sm text-muted-foreground">Copy all message templates, filters, and list assignments to a new campaign.</p>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">New Campaign Name</label>
                <input
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
                  value={cloneName}
                  onChange={(e) => setCloneName(e.target.value)}
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Assign to Account</label>
                <select
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  value={cloneAccountId}
                  onChange={(e) => setCloneAccountId(e.target.value)}
                >
                  <option value="">Select an account...</option>
                  {(allAccounts ?? []).filter(a => !a.archived).map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button size="sm" variant="outline" onClick={() => setCloneDialogOpen(false)}>Cancel</Button>
              <Button
                size="sm"
                disabled={!cloneName.trim() || !cloneAccountId || cloneCampaign.isPending}
                onClick={() => {
                  cloneCampaign.mutate(
                    { id, name: cloneName.trim(), account_id: cloneAccountId },
                    {
                      onSuccess: (newCampaign) => {
                        toast.success("Campaign cloned");
                        setCloneDialogOpen(false);
                        router.push(`/campaigns/${newCampaign.id}`);
                      },
                      onError: (err) => toast.error(err.message),
                    }
                  );
                }}
              >
                {cloneCampaign.isPending ? "Cloning…" : "Clone"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
