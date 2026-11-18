"use client";

import { useState, use, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useBroadcast,
  useBroadcastLeads,
  useActivateBroadcast,
  usePauseBroadcast,
  useArchiveBroadcast,
  useDeleteBroadcast,
  useUpdateBroadcast,
  useLeadListLeads,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MessageTemplateEditor } from "@/components/message-template-editor";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { Play, Pause, Archive, Trash2, Pencil } from "lucide-react";

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  sent: "Msg sent",
  sequence_complete: "Complete",
  skipped: "Skipped",
  manual_outreach: "Manual",
  error: "Error",
};

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-neutral-soft text-neutral",
  sent: "bg-info-soft text-info",
  sequence_complete: "bg-success-soft text-success",
  skipped: "bg-warning-soft text-warning",
  manual_outreach: "bg-plum-soft text-plum",
  error: "bg-danger-soft text-danger",
};

export default function BroadcastDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data: bc, isLoading } = useBroadcast(id);
  const PAGE_SIZE = 50;
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [leadsOffset, setLeadsOffset] = useState(0);
  const { data: leadsPage } = useBroadcastLeads(id, {
    limit: PAGE_SIZE,
    offset: leadsOffset,
    status: statusFilter,
  });

  const activate = useActivateBroadcast();
  const pause = usePauseBroadcast();
  const archive = useArchiveBroadcast();
  const del = useDeleteBroadcast();
  const update = useUpdateBroadcast(id);

  // Lead sample for variable chips — fetched from the broadcast's source list.
  const { data: listLeadSample } = useLeadListLeads(
    bc?.source_list_id ?? "",
    { per_page: 100 },
    { enabled: !!bc?.source_list_id }
  );

  const { customVariables, fillRates, customVariableExamples } = useMemo(() => {
    const leads = listLeadSample?.items;
    if (!leads || leads.length === 0) return { customVariables: [] as string[], fillRates: undefined };
    const total = leads.length;
    const FIELD_MAP: Record<string, "first_name" | "last_name" | "company" | "title"> = {
      first_name: "first_name", firstname: "first_name",
      last_name: "last_name",  lastname: "last_name",
      company: "company", title: "title",
    };
    const rates: Record<string, number> = {};
    for (const [token, field] of Object.entries(FIELD_MAP)) {
      rates[token] = leads.filter(l => l[field] != null && l[field] !== "").length / total;
    }
    const extraKeys = new Set<string>();
    for (const lead of leads) {
      if (lead.extra_data) {
        for (const k of Object.keys(lead.extra_data)) extraKeys.add(k.toLowerCase());
      }
    }
    for (const key of extraKeys) {
      rates[key] = leads.filter(l =>
        l.extra_data && key in l.extra_data && l.extra_data[key] != null && l.extra_data[key] !== ""
      ).length / total;
    }
    const standardTokens = new Set(Object.keys(FIELD_MAP));
    const examples: Record<string, string> = {};
    for (const key of extraKeys) {
      const lead = leads.find(l => l.extra_data && l.extra_data[key] != null && l.extra_data[key] !== "");
      if (lead?.extra_data) examples[key] = String(lead.extra_data[key]);
    }
    return { customVariables: [...extraKeys].filter(k => !standardTokens.has(k)), fillRates: rates, customVariableExamples: examples };
  }, [listLeadSample]);

  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");

  // Sequence editing
  const [editingSeq, setEditingSeq] = useState(false);
  const [seqMsg1, setSeqMsg1] = useState("");
  const [seqMsg2, setSeqMsg2] = useState("");
  const [seqMsg3, setSeqMsg3] = useState("");
  // Delay: stored as float hours; 0 = none; fractional = minutes.
  const [seqDelayMode, setSeqDelayMode] = useState<"none" | "minutes" | "hours">("hours");
  const [seqDelayValue, setSeqDelayValue] = useState(24);
  const [seqRouting, setSeqRouting] = useState<"skip" | "branch">("skip");
  const [seqPriorOnly, setSeqPriorOnly] = useState("");
  const [seqPriorOnly2, setSeqPriorOnly2] = useState("");
  const [seqPriorOnly3, setSeqPriorOnly3] = useState("");
  const [showSeqMsg2, setShowSeqMsg2] = useState(false);
  const [showSeqMsg3, setShowSeqMsg3] = useState(false);
  const [showSeqPrior2, setShowSeqPrior2] = useState(false);
  const [showSeqPrior3, setShowSeqPrior3] = useState(false);

  function decodeDelay(h: number): { mode: "none" | "minutes" | "hours"; value: number } {
    if (h === 0) return { mode: "none", value: 0 };
    if (h < 1) return { mode: "minutes", value: Math.round(h * 60) };
    return { mode: "hours", value: h };
  }

  function encodeDelay(mode: "none" | "minutes" | "hours", value: number): number {
    if (mode === "none") return 0;
    if (mode === "minutes") return value / 60;
    return value;
  }

  function openSeqEditor() {
    setSeqMsg1(bc?.message_1 ?? "");
    setSeqMsg2(bc?.message_2 ?? "");
    setSeqMsg3(bc?.message_3 ?? "");
    const { mode, value } = decodeDelay(bc?.delay_between_hours ?? 24);
    setSeqDelayMode(mode);
    setSeqDelayValue(value);
    setSeqRouting((bc?.conversation_routing ?? "skip") as "skip" | "branch");
    setSeqPriorOnly(bc?.message_prior_only ?? "");
    setSeqPriorOnly2(bc?.message_prior_only_2 ?? "");
    setSeqPriorOnly3(bc?.message_prior_only_3 ?? "");
    setShowSeqMsg2(!!bc?.message_2);
    setShowSeqMsg3(!!bc?.message_3);
    setShowSeqPrior2(!!bc?.message_prior_only_2);
    setShowSeqPrior3(!!bc?.message_prior_only_3);
    setEditingSeq(true);
  }

  function saveSequence() {
    if (!seqMsg1.trim()) { toast.error("Message 1 is required"); return; }
    update.mutate(
      {
        message_1: seqMsg1.trim(),
        message_2: showSeqMsg2 && seqMsg2.trim() ? seqMsg2.trim() : undefined,
        message_3: showSeqMsg2 && showSeqMsg3 && seqMsg3.trim() ? seqMsg3.trim() : undefined,
        delay_between_hours: encodeDelay(seqDelayMode, seqDelayValue),
        conversation_routing: seqRouting,
        message_prior_only: seqRouting === "branch" && seqPriorOnly.trim() ? seqPriorOnly.trim() : null,
        message_prior_only_2: seqRouting === "branch" && showSeqPrior2 && seqPriorOnly2.trim() ? seqPriorOnly2.trim() : null,
        message_prior_only_3: seqRouting === "branch" && showSeqPrior2 && showSeqPrior3 && seqPriorOnly3.trim() ? seqPriorOnly3.trim() : null,
      },
      {
        onSuccess: () => { toast.success("Sequence updated"); setEditingSeq(false); },
        onError: (err: Error) => toast.error(err.message),
      }
    );
  }

  if (isLoading || !bc) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const counts = bc.status_counts ?? {};
  const total = bc.total_leads || 0;
  const messages = [bc.message_1, bc.message_2, bc.message_3].filter(Boolean) as string[];

  function saveName() {
    if (!nameDraft.trim() || nameDraft.trim() === bc?.name) {
      setEditingName(false);
      return;
    }
    update.mutate(
      { name: nameDraft.trim() },
      {
        onSuccess: () => {
          toast.success("Renamed");
          setEditingName(false);
        },
        onError: (err: Error) => toast.error(err.message),
      }
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          {editingName ? (
            <div className="flex items-center gap-2">
              <input
                autoFocus
                className="text-2xl font-medium bg-transparent border-b border-border focus:outline-none focus:border-foreground pb-1 flex-1"
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={saveName}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveName();
                  if (e.key === "Escape") setEditingName(false);
                }}
              />
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <h1
                className="text-2xl font-medium truncate cursor-pointer hover:text-muted-foreground/80"
                onClick={() => {
                  setNameDraft(bc.name);
                  setEditingName(true);
                }}
                title="Click to rename"
              >
                {bc.name}
              </h1>
              <StatusBadge status={bc.status} />
              {bc.archived && (
                <span className="text-xs rounded bg-neutral-soft text-neutral px-2 py-0.5">
                  Archived
                </span>
              )}
            </div>
          )}
          <div className="mt-1 text-sm text-muted-foreground">
            {bc.account_name}
            {bc.source_list_name && (
              <>
                <span className="mx-2 text-muted-foreground/40">·</span>
                <Link
                  href={`/lead-lists/${bc.source_list_id}`}
                  className="hover:text-foreground"
                >
                  from {bc.source_list_name}
                </Link>
              </>
            )}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2 shrink-0">
          {bc.status === "draft" || bc.status === "paused" ? (
            <Button
              variant="default"
              onClick={() =>
                activate.mutate(bc.id, {
                  onSuccess: () => toast.success("Activated"),
                  onError: (err: Error) => toast.error(err.message),
                })
              }
            >
              <Play className="mr-2 h-4 w-4" /> Activate
            </Button>
          ) : bc.status === "active" ? (
            <Button
              variant="outline"
              onClick={() =>
                pause.mutate(bc.id, {
                  onSuccess: () => toast.success("Paused"),
                })
              }
            >
              <Pause className="mr-2 h-4 w-4" /> Pause
            </Button>
          ) : null}
          {!bc.archived && (
            <Button
              variant="outline"
              onClick={() =>
                archive.mutate(bc.id, {
                  onSuccess: () => toast.success("Archived"),
                })
              }
            >
              <Archive className="mr-2 h-4 w-4" /> Archive
            </Button>
          )}
          {(bc.status === "draft" || bc.archived) && (
            <Button
              variant="outline"
              onClick={() => {
                if (confirm("Delete this broadcast? Snapshot rows will be removed.")) {
                  del.mutate(bc.id, {
                    onSuccess: () => {
                      toast.success("Deleted");
                      router.push("/broadcasts");
                    },
                    onError: (err: Error) => toast.error(err.message),
                  });
                }
              }}
            >
              <Trash2 className="mr-2 h-4 w-4" /> Delete
            </Button>
          )}
        </div>
      </div>

      {/* Status counts row */}
      <div className="grid gap-3 sm:grid-cols-6">
        {["pending", "sent", "sequence_complete", "skipped", "manual_outreach", "error"].map((s) => {
          const n = counts[s] ?? 0;
          return (
            <button
              key={s}
              onClick={() => {
                setStatusFilter(statusFilter === s ? undefined : s);
                setLeadsOffset(0);
              }}
              className={`rounded-md border px-3 py-2 text-left transition-colors ${
                statusFilter === s
                  ? "border-foreground bg-muted"
                  : "border-border hover:border-muted-foreground/40"
              }`}
            >
              <div className="text-xs text-muted-foreground">{STATUS_LABEL[s]}</div>
              <div className={`text-lg font-medium ${n > 0 ? "text-foreground" : "text-muted-foreground/40"}`}>
                {n}
              </div>
            </button>
          );
        })}
      </div>

      {/* Messages */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Message sequence</CardTitle>
            {!editingSeq && (
              <Button variant="ghost" size="sm" onClick={openSeqEditor} className="h-7 px-2 text-xs">
                <Pencil className="h-3 w-3 mr-1" /> Edit
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {editingSeq ? (
            /* ── Edit mode ────────────────────────────────────────────── */
            <div className="space-y-4">
              {bc.status === "active" && (
                <div className="rounded-md border border-warning-line bg-warning-soft/70 px-3 py-2 text-xs text-warning">
                  Broadcast is live — changes apply to all pending leads immediately.
                </div>
              )}

              <div className="space-y-1.5">
                <Label className="text-xs">Message 1</Label>
                <MessageTemplateEditor value={seqMsg1} onChange={setSeqMsg1} showCharLimit minHeight={90} customVariables={customVariables} fillRates={fillRates} customVariableExamples={customVariableExamples} />
              </div>

              {showSeqMsg2 ? (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">Message 2</Label>
                    <button type="button" onClick={() => { setShowSeqMsg2(false); setShowSeqMsg3(false); setSeqMsg2(""); setSeqMsg3(""); }} className="text-xs text-muted-foreground hover:text-foreground">remove</button>
                  </div>
                  <MessageTemplateEditor value={seqMsg2} onChange={setSeqMsg2} showCharLimit minHeight={80} customVariables={customVariables} fillRates={fillRates} customVariableExamples={customVariableExamples} />
                </div>
              ) : (
                <Button type="button" variant="outline" size="sm" onClick={() => setShowSeqMsg2(true)}>+ Add message 2</Button>
              )}

              {showSeqMsg2 && (showSeqMsg3 ? (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">Message 3</Label>
                    <button type="button" onClick={() => { setShowSeqMsg3(false); setSeqMsg3(""); }} className="text-xs text-muted-foreground hover:text-foreground">remove</button>
                  </div>
                  <MessageTemplateEditor value={seqMsg3} onChange={setSeqMsg3} showCharLimit minHeight={80} customVariables={customVariables} fillRates={fillRates} customVariableExamples={customVariableExamples} />
                </div>
              ) : (
                <Button type="button" variant="outline" size="sm" onClick={() => setShowSeqMsg3(true)}>+ Add message 3</Button>
              ))}

              {showSeqMsg2 && (
                <div className="space-y-1.5">
                  <Label className="text-xs">Delay between messages</Label>
                  <div className="flex items-center gap-2 flex-wrap">
                    {(["none", "minutes", "hours"] as const).map((m) => (
                      <button key={m} type="button" onClick={() => {
                        setSeqDelayMode(m);
                        if (m === "minutes" && seqDelayValue === 0) setSeqDelayValue(30);
                        if (m === "hours" && seqDelayValue === 0) setSeqDelayValue(24);
                      }}
                        className={`rounded border px-2.5 py-1 text-xs transition-colors capitalize ${seqDelayMode === m ? "border-primary bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:border-muted-foreground/40"}`}>
                        {m}
                      </button>
                    ))}
                    {seqDelayMode !== "none" && (
                      <Input
                        type="number"
                        min={1}
                        max={seqDelayMode === "minutes" ? 1440 : 720}
                        value={seqDelayValue}
                        onChange={(e) => setSeqDelayValue(parseInt(e.target.value) || 1)}
                        className="w-24 h-7 text-xs"
                      />
                    )}
                    {seqDelayMode === "none" && (
                      <span className="text-xs text-muted-foreground">Messages send back-to-back (~5 s apart)</span>
                    )}
                  </div>
                </div>
              )}

              {/* Conversation routing */}
              <div className="space-y-2 pt-1">
                <Label className="text-xs">If a prior LinkedIn conversation exists</Label>
                <div className="grid grid-cols-2 gap-2">
                  {(["skip", "branch"] as const).map((r) => (
                    <button key={r} type="button" onClick={() => setSeqRouting(r)}
                      className={`rounded-md border px-3 py-2 text-left text-sm transition-colors ${seqRouting === r ? "border-primary bg-primary/10" : "border-border hover:border-muted-foreground/40 text-muted-foreground"}`}>
                      <div className="font-medium capitalize">{r}</div>
                      <div className="text-xs opacity-70">{r === "skip" ? "Skip anyone already messaged" : "Route per conversation state"}</div>
                    </button>
                  ))}
                </div>
                {seqRouting === "branch" && (
                  <div className="rounded-md border border-border/60 bg-muted/20 p-3 space-y-3">
                    <div className="grid gap-1 text-xs text-muted-foreground">
                      <div className="flex items-center gap-2"><span className="rounded bg-success-soft text-success px-1.5 py-0.5 font-medium">Fresh</span><span>No prior messages → msg 1 sequence above</span></div>
                      <div className="flex items-center gap-2"><span className="rounded bg-info-soft text-info px-1.5 py-0.5 font-medium">Sent, no reply</span><span>→ prior-branch sequence below (shares the same delay)</span></div>
                      <div className="flex items-center gap-2"><span className="rounded bg-warning-soft text-warning px-1.5 py-0.5 font-medium">They replied</span><span>→ manual outreach</span></div>
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs">Prior-branch message 1</Label>
                      <MessageTemplateEditor value={seqPriorOnly} onChange={setSeqPriorOnly} placeholder="Hey {{first_name}}, just following up on my earlier message…" showCharLimit minHeight={80} customVariables={customVariables} fillRates={fillRates} customVariableExamples={customVariableExamples} />
                      <p className="text-xs text-muted-foreground">Leave blank to skip these leads entirely.</p>
                    </div>

                    {showSeqPrior2 ? (
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between">
                          <Label className="text-xs">Prior-branch message 2</Label>
                          <button type="button" onClick={() => { setShowSeqPrior2(false); setShowSeqPrior3(false); setSeqPriorOnly2(""); setSeqPriorOnly3(""); }} className="text-xs text-muted-foreground hover:text-foreground">remove</button>
                        </div>
                        <MessageTemplateEditor value={seqPriorOnly2} onChange={setSeqPriorOnly2} placeholder="Optional second follow-up…" showCharLimit minHeight={70} customVariables={customVariables} fillRates={fillRates} customVariableExamples={customVariableExamples} />
                      </div>
                    ) : (
                      seqPriorOnly.trim() && <Button type="button" variant="outline" size="sm" onClick={() => setShowSeqPrior2(true)}>+ Add prior-branch msg 2</Button>
                    )}

                    {showSeqPrior2 && (showSeqPrior3 ? (
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between">
                          <Label className="text-xs">Prior-branch message 3</Label>
                          <button type="button" onClick={() => { setShowSeqPrior3(false); setSeqPriorOnly3(""); }} className="text-xs text-muted-foreground hover:text-foreground">remove</button>
                        </div>
                        <MessageTemplateEditor value={seqPriorOnly3} onChange={setSeqPriorOnly3} placeholder="Optional third follow-up…" showCharLimit minHeight={70} customVariables={customVariables} fillRates={fillRates} customVariableExamples={customVariableExamples} />
                      </div>
                    ) : (
                      seqPriorOnly2.trim() && <Button type="button" variant="outline" size="sm" onClick={() => setShowSeqPrior3(true)}>+ Add prior-branch msg 3</Button>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex gap-2 pt-1">
                <Button size="sm" onClick={saveSequence} disabled={update.isPending}>{update.isPending ? "Saving…" : "Save changes"}</Button>
                <Button size="sm" variant="outline" onClick={() => setEditingSeq(false)}>Cancel</Button>
              </div>
            </div>
          ) : (
            /* ── Read-only view ───────────────────────────────────────── */
            <>
              {(() => {
                const dh = bc.delay_between_hours;
                const delayLabel = dh === 0
                  ? "back-to-back (~5 s)"
                  : dh < 1
                  ? `${Math.round(dh * 60)} min`
                  : `${dh}h`;
                return messages.map((m, i) => (
                  <div key={i} className="rounded-md border border-border/50 bg-muted/20 p-3">
                    <div className="text-xs text-muted-foreground mb-1">
                      Message {i + 1}
                      {i > 0 && <span className="ml-2">· sent {delayLabel} after msg {i}</span>}
                    </div>
                    <div className="whitespace-pre-wrap text-sm">{m}</div>
                  </div>
                ));
              })()}
              {bc.conversation_routing === "branch" && (
                <div className="mt-2 space-y-2">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="rounded bg-primary/20 text-primary px-1.5 py-0.5 font-medium">Branch mode</span>
                    <span>Fresh → msg 1 · Replied → manual outreach</span>
                  </div>
                  {bc.message_prior_only ? (
                    [bc.message_prior_only, bc.message_prior_only_2, bc.message_prior_only_3]
                      .filter(Boolean)
                      .map((m, i) => (
                        <div key={i} className="rounded-md border border-plum-line bg-plum-soft/70 p-3">
                          <div className="text-xs text-muted-foreground mb-1">
                            Prior-branch msg {i + 1}
                            {i > 0 && (() => {
                              const dh = bc.delay_between_hours;
                              const dl = dh === 0 ? "back-to-back" : dh < 1 ? `${Math.round(dh * 60)} min` : `${dh}h`;
                              return <span className="ml-2">· {dl} after prior-branch msg {i}</span>;
                            })()}
                          </div>
                          <div className="whitespace-pre-wrap text-sm">{m}</div>
                        </div>
                      ))
                  ) : (
                    <p className="text-xs text-muted-foreground">No warm message — &quot;sent, no reply&quot; leads will be skipped.</p>
                  )}
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Lead table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">
              Leads {statusFilter && <span className="text-sm text-muted-foreground">· filtered: {STATUS_LABEL[statusFilter]}</span>}
            </CardTitle>
            <Tabs
              value={statusFilter ?? "all"}
              onValueChange={(v) => {
                setStatusFilter(v === "all" ? undefined : v);
                setLeadsOffset(0);
              }}
            >
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="pending">Pending</TabsTrigger>
                <TabsTrigger value="sent">Sent</TabsTrigger>
                <TabsTrigger value="skipped">Skipped</TabsTrigger>
                <TabsTrigger value="manual_outreach">Manual</TabsTrigger>
                <TabsTrigger value="error">Error</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </CardHeader>
        <CardContent>
          {!leadsPage ? (
            <Skeleton className="h-40" />
          ) : leadsPage.items.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">
              No leads {statusFilter ? `in ${STATUS_LABEL[statusFilter]}` : "yet"}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted-foreground border-b border-border">
                    <th className="pb-2 pr-4">Lead</th>
                    <th className="pb-2 pr-4">Company</th>
                    <th className="pb-2 pr-4">Status</th>
                    <th className="pb-2 pr-4">Msg</th>
                    <th className="pb-2">Last sent</th>
                  </tr>
                </thead>
                <tbody>
                  {leadsPage.items.map((bl) => (
                    <tr key={bl.id} className="border-b border-border/40 last:border-0">
                      <td className="py-2 pr-4">
                        {bl.lead_linkedin_url ? (
                          <Link
                            href={`/leads/${bl.lead_id}`}
                            className="hover:text-muted-foreground"
                          >
                            {bl.lead_name ?? "(no name)"}
                          </Link>
                        ) : (
                          bl.lead_name ?? "(no name)"
                        )}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {bl.lead_company ?? "—"}
                      </td>
                      <td className="py-2 pr-4">
                        <span
                          className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${
                            STATUS_STYLE[bl.status] ?? "bg-muted text-muted-foreground"
                          }`}
                        >
                          {STATUS_LABEL[bl.status] ?? bl.status}
                        </span>
                        {bl.skipped_reason && (
                          <span className="ml-1 text-xs text-muted-foreground">
                            {bl.skipped_reason}
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {bl.last_message_index ? `${bl.last_message_index}/${messages.length}` : "—"}
                      </td>
                      <td className="py-2 text-muted-foreground text-xs">
                        {bl.last_message_sent_at
                          ? new Date(bl.last_message_sent_at.endsWith("Z") ? bl.last_message_sent_at : bl.last_message_sent_at + "Z").toLocaleString(undefined, {
                              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                            })
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {leadsPage.total > PAGE_SIZE && (
                <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
                  <span>
                    {leadsOffset + 1}–{Math.min(leadsOffset + leadsPage.items.length, leadsPage.total)} of {leadsPage.total}
                  </span>
                  <div className="flex gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      disabled={leadsOffset === 0}
                      onClick={() => setLeadsOffset(Math.max(0, leadsOffset - PAGE_SIZE))}
                    >
                      ← Prev
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      disabled={leadsOffset + PAGE_SIZE >= leadsPage.total}
                      onClick={() => setLeadsOffset(leadsOffset + PAGE_SIZE)}
                    >
                      Next →
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Footer stats */}
      <div className="text-xs text-muted-foreground flex flex-wrap gap-x-6 gap-y-1">
        <span>Total leads: {total}</span>
        <span>Messages sent: {bc.messages_sent ?? 0}</span>
        <span>Delay: {bc.delay_between_hours === 0 ? "none (~5 s)" : bc.delay_between_hours < 1 ? `${Math.round(bc.delay_between_hours * 60)} min` : `${bc.delay_between_hours}h`} between messages</span>
        <span>Created: {new Date(bc.created_at.endsWith("Z") ? bc.created_at : bc.created_at + "Z").toLocaleDateString()}</span>
      </div>
    </div>
  );
}
