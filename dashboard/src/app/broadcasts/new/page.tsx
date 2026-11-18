"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  useAccounts,
  useLeadLists,
  useLeadList,
  useLeadListLeads,
  useCreateBroadcast,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MessageTemplateEditor } from "@/components/message-template-editor";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import Link from "next/link";

export default function NewBroadcastPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading…</div>}>
      <NewBroadcastPageInner />
    </Suspense>
  );
}

function NewBroadcastPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: accounts } = useAccounts();
  const { data: lists } = useLeadLists();
  const create = useCreateBroadcast();

  // Read incoming ?lead_ids= / ?list_id= / ?list_ids= / ?from=selection once on mount.
  const preselectedLeadIds = useMemo<string[]>(() => {
    const raw = searchParams.get("lead_ids");
    if (raw) {
      return raw.split(",").map((s) => s.trim()).filter(Boolean);
    }
    if (searchParams.get("from") === "selection") {
      try {
        const stored = sessionStorage.getItem("broadcast_lead_ids");
        if (stored) return JSON.parse(stored) as string[];
      } catch {
        return [];
      }
    }
    return [];
  }, [searchParams]);
  const sourceListIdFromUrl = searchParams.get("list_id");
  const sourceListIdsFromUrl = useMemo<string[]>(() => {
    const raw = searchParams.get("list_ids");
    if (!raw) return [];
    return raw.split(",").map((s) => s.trim()).filter(Boolean);
  }, [searchParams]);
  const fromLeadSelection = preselectedLeadIds.length > 0;
  const fromMultiListSelection = sourceListIdsFromUrl.length > 0;
  const fromSelection = fromLeadSelection || fromMultiListSelection;

  // Show which list the selection came from as context (read-only).
  const { data: originList } = useLeadList(sourceListIdFromUrl ?? "");

  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState("");
  const [sourceListId, setSourceListId] = useState("");
  const [message1, setMessage1] = useState("");
  const [message2, setMessage2] = useState("");
  const [message3, setMessage3] = useState("");
  const [delayMode, setDelayMode] = useState<"none" | "minutes" | "hours">("hours");
  const [delayValue, setDelayValue] = useState(24);
  const [showMsg2, setShowMsg2] = useState(false);
  const [showMsg3, setShowMsg3] = useState(false);
  // Conversation routing
  const [conversationRouting, setConversationRouting] = useState<"skip" | "branch">("skip");
  const [messagePriorOnly, setMessagePriorOnly] = useState("");
  const [messagePriorOnly2, setMessagePriorOnly2] = useState("");
  const [messagePriorOnly3, setMessagePriorOnly3] = useState("");
  const [showPrior2, setShowPrior2] = useState(false);
  const [showPrior3, setShowPrior3] = useState(false);

  function encodeDelay(mode: "none" | "minutes" | "hours", value: number): number {
    if (mode === "none") return 0;
    if (mode === "minutes") return value / 60;
    return value;
  }

  // Clean up sessionStorage after we've read it, so a later manual open
  // doesn't re-populate from a stale selection.
  useEffect(() => {
    if (searchParams.get("from") === "selection") {
      try {
        sessionStorage.removeItem("broadcast_lead_ids");
      } catch {
        // no-op — private mode / disabled storage
      }
    }
  }, [searchParams]);

  // If the URL carried a single ?list_id= without lead_ids or list_ids, seed
  // the source-list picker so the user doesn't have to re-pick it. This covers
  // the "1 list selected on the Lists page → Create broadcast" path.
  useEffect(() => {
    if (
      sourceListIdFromUrl &&
      !fromLeadSelection &&
      !fromMultiListSelection &&
      !sourceListId
    ) {
      setSourceListId(sourceListIdFromUrl);
    }
  }, [sourceListIdFromUrl, fromLeadSelection, fromMultiListSelection, sourceListId]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name || !accountId) {
      toast.error("Name and account are required");
      return;
    }
    if (!fromSelection && !sourceListId) {
      toast.error("Please pick a source list");
      return;
    }
    if (!message1.trim()) {
      toast.error("Message 1 is required");
      return;
    }

    create.mutate(
      {
        account_id: accountId,
        name,
        source_list_id: fromSelection ? null : sourceListId,
        source_list_ids: fromMultiListSelection ? sourceListIdsFromUrl : null,
        lead_ids: fromLeadSelection ? preselectedLeadIds : null,
        message_1: message1,
        message_2: showMsg2 && message2 ? message2 : null,
        message_3: showMsg3 && message3 ? message3 : null,
        delay_between_hours: encodeDelay(delayMode, delayValue),
        conversation_routing: conversationRouting,
        message_prior_only: conversationRouting === "branch" && messagePriorOnly.trim() ? messagePriorOnly.trim() : null,
        message_prior_only_2: conversationRouting === "branch" && showPrior2 && messagePriorOnly2.trim() ? messagePriorOnly2.trim() : null,
        message_prior_only_3: conversationRouting === "branch" && showPrior2 && showPrior3 && messagePriorOnly3.trim() ? messagePriorOnly3.trim() : null,
      },
      {
        onSuccess: (bc) => {
          toast.success(`Broadcast created with ${bc.total_leads} leads`);
          router.push(`/broadcasts/${bc.id}`);
        },
        onError: (err: Error) => toast.error(err.message),
      }
    );
  }

  // Fetch a sample of leads from the selected list to derive custom variables.
  const { data: listLeadSample } = useLeadListLeads(
    sourceListId,
    { per_page: 100 },
    { enabled: !!sourceListId && !fromSelection }
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

  const availableLists = (lists ?? []).filter((l) => !l.archived);
  const selectedList = availableLists.find((l) => l.id === sourceListId);

  return (
    <div className="space-y-6 max-w-2xl">
      <PageHeader title="Create Broadcast" description="Message-only sequence to your 1st-degree connections">
        <Link href="/broadcasts">
          <Button variant="outline">Cancel</Button>
        </Link>
      </PageHeader>

      <Card>
        <CardHeader>
          <CardTitle>Setup</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g., Sep launch update"
                />
              </div>
              <div className="space-y-2">
                <Label>Account</Label>
                <Select value={accountId} onValueChange={setAccountId}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select account" />
                  </SelectTrigger>
                  <SelectContent>
                    {accounts?.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        {a.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {fromSelection ? (
              <div className="rounded-md border border-primary/30 bg-primary/5 p-3 space-y-1">
                {fromMultiListSelection ? (
                  <p className="text-sm font-medium">
                    {sourceListIdsFromUrl.length} lists selected
                    <span className="text-muted-foreground font-normal">
                      {" "}— leads will be pooled and deduplicated at snapshot time
                    </span>
                  </p>
                ) : (
                  <p className="text-sm font-medium">
                    {preselectedLeadIds.length} leads pre-selected
                    {originList?.name && (
                      <span className="text-muted-foreground font-normal">
                        {" "}
                        from{" "}
                        <Link
                          href={`/lead-lists/${sourceListIdFromUrl}`}
                          className="underline hover:text-foreground"
                        >
                          {originList.name}
                        </Link>
                      </span>
                    )}
                  </p>
                )}
                <p className="text-xs text-muted-foreground">
                  Only 1st-degree connections receive messages; others are marked skipped.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <Label>Source List</Label>
                <Select value={sourceListId} onValueChange={setSourceListId}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select a lead list" />
                  </SelectTrigger>
                  <SelectContent>
                    {availableLists.map((l) => (
                      <SelectItem key={l.id} value={l.id}>
                        {l.name} ({l.total_leads} leads)
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedList && (
                  <p className="text-xs text-muted-foreground">
                    {selectedList.total_leads} leads will be snapshotted at creation.
                    Only 1st-degree connections receive messages; others are marked skipped.
                  </p>
                )}
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="m1">Message 1</Label>
              <MessageTemplateEditor
                id="m1"
                value={message1}
                onChange={setMessage1}
                placeholder={"Hey {{first_name}}, quick update..."}
                showCharLimit
                minHeight={100}
                customVariables={customVariables}
                fillRates={fillRates}
                customVariableExamples={customVariableExamples}
              />
            </div>

            {showMsg2 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="m2">Message 2</Label>
                  <button
                    type="button"
                    onClick={() => { setShowMsg2(false); setShowMsg3(false); setMessage2(""); setMessage3(""); }}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    remove
                  </button>
                </div>
                <MessageTemplateEditor
                  id="m2"
                  value={message2}
                  onChange={setMessage2}
                  placeholder={"Optional follow-up..."}
                  showCharLimit
                  minHeight={90}
                  customVariables={customVariables}
                  fillRates={fillRates}
                  customVariableExamples={customVariableExamples}
                />
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowMsg2(true)}
              >
                + Add message 2
              </Button>
            )}

            {showMsg2 && (showMsg3 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="m3">Message 3</Label>
                  <button
                    type="button"
                    onClick={() => { setShowMsg3(false); setMessage3(""); }}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    remove
                  </button>
                </div>
                <MessageTemplateEditor
                  id="m3"
                  value={message3}
                  onChange={setMessage3}
                  placeholder={"Optional third follow-up..."}
                  showCharLimit
                  minHeight={90}
                  customVariables={customVariables}
                  fillRates={fillRates}
                  customVariableExamples={customVariableExamples}
                />
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowMsg3(true)}
              >
                + Add message 3
              </Button>
            ))}

            {(showMsg2 || showMsg3) && (
              <div className="space-y-2">
                <Label>Delay between messages</Label>
                <div className="flex items-center gap-2 flex-wrap">
                  {(["none", "minutes", "hours"] as const).map((m) => (
                    <button key={m} type="button" onClick={() => {
                      setDelayMode(m);
                      if (m === "minutes" && delayValue === 0) setDelayValue(30);
                      if (m === "hours" && delayValue === 0) setDelayValue(24);
                    }}
                      className={`rounded border px-2.5 py-1 text-sm transition-colors capitalize ${delayMode === m ? "border-primary bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:border-muted-foreground/40"}`}>
                      {m}
                    </button>
                  ))}
                  {delayMode !== "none" && (
                    <Input
                      type="number"
                      min={1}
                      max={delayMode === "minutes" ? 1440 : 720}
                      value={delayValue}
                      onChange={(e) => setDelayValue(parseInt(e.target.value) || 1)}
                      className="w-24"
                    />
                  )}
                  {delayMode === "none" && (
                    <span className="text-sm text-muted-foreground">Messages send back-to-back (~5 s apart)</span>
                  )}
                </div>
              </div>
            )}

            {/* Conversation routing */}
            <div className="space-y-3">
              <Label>If a prior LinkedIn conversation exists</Label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setConversationRouting("skip")}
                  className={`rounded-md border px-3 py-2.5 text-left text-sm transition-colors ${
                    conversationRouting === "skip"
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border hover:border-muted-foreground/40 text-muted-foreground"
                  }`}
                >
                  <div className="font-medium mb-0.5">Skip</div>
                  <div className="text-xs opacity-70">Skip anyone already messaged</div>
                </button>
                <button
                  type="button"
                  onClick={() => setConversationRouting("branch")}
                  className={`rounded-md border px-3 py-2.5 text-left text-sm transition-colors ${
                    conversationRouting === "branch"
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border hover:border-muted-foreground/40 text-muted-foreground"
                  }`}
                >
                  <div className="font-medium mb-0.5">Branch</div>
                  <div className="text-xs opacity-70">Different message per conversation state</div>
                </button>
              </div>

              {conversationRouting === "branch" && (
                <div className="rounded-md border border-border/60 bg-muted/20 p-3 space-y-4">
                  <div className="grid gap-1 text-xs text-muted-foreground">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-success-soft text-success px-1.5 py-0.5 font-medium">Fresh</span>
                      <span>No prior messages → sends Message 1 above</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-info-soft text-info px-1.5 py-0.5 font-medium">Sent, no reply</span>
                      <span>You messaged, they didn&apos;t reply → sends the message below</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-warning-soft text-warning px-1.5 py-0.5 font-medium">They replied</span>
                      <span>They replied to you → marked for manual outreach, no automated send</span>
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="msg-prior">Prior-branch message 1 (&quot;sent, no reply&quot; leads)</Label>
                    <MessageTemplateEditor
                      id="msg-prior"
                      value={messagePriorOnly}
                      onChange={setMessagePriorOnly}
                      placeholder={"Hey {{first_name}}, just following up on my last message…"}
                      showCharLimit
                      minHeight={90}
                      customVariables={customVariables}
                      fillRates={fillRates}
                      customVariableExamples={customVariableExamples}
                    />
                    <p className="text-xs text-muted-foreground">
                      Leave blank to skip these leads instead of sending an alternative message.
                    </p>
                  </div>

                  {showPrior2 ? (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label>Prior-branch message 2</Label>
                        <button type="button" onClick={() => { setShowPrior2(false); setShowPrior3(false); setMessagePriorOnly2(""); setMessagePriorOnly3(""); }} className="text-xs text-muted-foreground hover:text-foreground">remove</button>
                      </div>
                      <MessageTemplateEditor value={messagePriorOnly2} onChange={setMessagePriorOnly2} placeholder={"Optional follow-up for prior-branch leads…"} showCharLimit minHeight={80} customVariables={customVariables} fillRates={fillRates} customVariableExamples={customVariableExamples} />
                    </div>
                  ) : (
                    messagePriorOnly.trim() && (
                      <Button type="button" variant="outline" size="sm" onClick={() => setShowPrior2(true)}>
                        + Add prior-branch msg 2
                      </Button>
                    )
                  )}

                  {showPrior2 && (showPrior3 ? (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label>Prior-branch message 3</Label>
                        <button type="button" onClick={() => { setShowPrior3(false); setMessagePriorOnly3(""); }} className="text-xs text-muted-foreground hover:text-foreground">remove</button>
                      </div>
                      <MessageTemplateEditor value={messagePriorOnly3} onChange={setMessagePriorOnly3} placeholder={"Optional third follow-up for prior-branch leads…"} showCharLimit minHeight={80} customVariables={customVariables} fillRates={fillRates} customVariableExamples={customVariableExamples} />
                    </div>
                  ) : (
                    messagePriorOnly2.trim() && (
                      <Button type="button" variant="outline" size="sm" onClick={() => setShowPrior3(true)}>
                        + Add prior-branch msg 3
                      </Button>
                    )
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-md border border-border/50 bg-muted/30 p-3 text-xs text-muted-foreground">
              <p className="font-medium text-foreground/80 mb-1">Before you activate</p>
              <p>
                Broadcast will be created as <span className="font-medium">DRAFT</span>. Review the
                snapshot on the detail page, then click <span className="font-medium">Activate</span> to
                start sending. The daily cap comes from the account&apos;s Daily message limit.
              </p>
            </div>

            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create Broadcast"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
