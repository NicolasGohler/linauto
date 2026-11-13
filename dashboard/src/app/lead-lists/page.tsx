"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useLeadLists,
  useCreateLeadList,
  useDeleteLeadList,
  useArchiveLeadList,
  useUnarchiveLeadList,
  useScrapeStatus,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EventImportDialog } from "@/components/leads/event-import-dialog";
import { ChevronDown, Archive, ArchiveRestore, Send, Trash2, X } from "lucide-react";
import { toast } from "sonner";

// Shows live scrape progress for a single list card
function ScrapeProgressBadge({ listId }: { listId: string }) {
  const { data: status } = useScrapeStatus(listId);
  if (!status || status.status === "unknown" || status.status === "done") return null;
  if (status.status === "error")
    return <Badge variant="destructive" className="text-xs">Scrape failed</Badge>;
  return (
    <Badge variant="secondary" className="text-xs animate-pulse">
      Scraping… {status.collected} URLs
    </Badge>
  );
}

export default function LeadListsPage() {
  const router = useRouter();
  const [showArchived, setShowArchived] = useState(false);
  const { data: lists, isLoading } = useLeadLists({ include_archived: showArchived });
  const deleteList = useDeleteLeadList();
  const archiveList = useArchiveLeadList();
  const unarchiveList = useUnarchiveLeadList();

  const createList = useCreateLeadList();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTgEnrich, setNewTgEnrich] = useState(true);
  const [newXEnrich, setNewXEnrich] = useState(false);
  const [showEventDialog, setShowEventDialog] = useState(false);
  const [scrapingIds, setScrapingIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [bulkPending, setBulkPending] = useState<null | "delete" | "archive" | "unarchive">(null);

  // Drop any selections for lists no longer in the current filtered view.
  useEffect(() => {
    if (!lists) return;
    const validIds = new Set(lists.map((l) => l.id));
    setSelectedIds((prev) => {
      const next = new Set<string>();
      prev.forEach((id) => {
        if (validIds.has(id)) next.add(id);
      });
      return next.size === prev.size ? prev : next;
    });
  }, [lists]);

  function toggleOne(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearSelection() {
    setSelectedIds(new Set());
    setConfirmingDelete(false);
  }

  const selectedList = useMemo(() => Array.from(selectedIds), [selectedIds]);
  const selectedListObjs = useMemo(
    () => (lists ?? []).filter((l) => selectedIds.has(l.id)),
    [lists, selectedIds]
  );
  const allSelectedArchived =
    selectedListObjs.length > 0 && selectedListObjs.every((l) => l.archived);
  const allSelectedActive =
    selectedListObjs.length > 0 && selectedListObjs.every((l) => !l.archived);

  async function runBulk(
    action: (id: string) => Promise<unknown>,
    label: string,
    key: "delete" | "archive" | "unarchive"
  ) {
    if (selectedList.length === 0) return;
    setBulkPending(key);
    let ok = 0;
    const failures: string[] = [];
    for (const id of selectedList) {
      try {
        await action(id);
        ok += 1;
      } catch (err) {
        failures.push((err as Error).message);
      }
    }
    setBulkPending(null);
    setConfirmingDelete(false);
    setSelectedIds(new Set());
    if (failures.length === 0) {
      toast.success(`${label} ${ok} list${ok === 1 ? "" : "s"}`);
    } else {
      toast.error(
        `${label} ${ok}/${selectedList.length} — ${failures.length} failed`
      );
    }
  }

  function handleBulkDelete() {
    return runBulk((id) => deleteList.mutateAsync(id), "Deleted", "delete");
  }

  function handleBulkArchive() {
    return runBulk((id) => archiveList.mutateAsync(id), "Archived", "archive");
  }

  function handleBulkUnarchive() {
    return runBulk(
      (id) => unarchiveList.mutateAsync(id),
      "Restored",
      "unarchive"
    );
  }

  function handleCreateBroadcast() {
    if (selectedList.length === 0) return;
    if (selectedList.length === 1) {
      router.push(`/broadcasts/new?list_id=${selectedList[0]}`);
    } else {
      router.push(`/broadcasts/new?list_ids=${selectedList.join(",")}`);
    }
  }

  const handleScrapeStarted = (listId: string) => {
    setScrapingIds((prev) => new Set(prev).add(listId));
  };

  const handleCreate = () => {
    if (!newName.trim()) return;
    createList.mutate(
      { name: newName.trim(), tg_enrich_enabled: newTgEnrich, x_enrich_enabled: newXEnrich },
      {
        onSuccess: () => {
          toast.success("Lead list created");
          setNewName("");
          setNewTgEnrich(true);
          setShowCreate(false);
        },
        onError: (err) => toast.error(err.message),
      }
    );
  };

  const filteredLists = useMemo(() => {
    if (!lists) return [];
    const q = search.trim().toLowerCase();
    if (!q) return lists;
    return lists.filter(
      (ll) =>
        ll.name?.toLowerCase().includes(q) ||
        ll.csv_filename?.toLowerCase().includes(q)
    );
  }, [lists, search]);
  const activeLists = filteredLists.filter((ll) => !ll.archived);
  const archivedLists = filteredLists.filter((ll) => ll.archived);

  const renderCard = (ll: NonNullable<typeof lists>[0]) => {
    const isScraping = scrapingIds.has(ll.id) || ll.csv_filename === "scraping...";
    const isChecked = selectedIds.has(ll.id);
    return (
      <div key={ll.id} className="relative group">
        {/* Checkbox overlay — visible on hover, always visible when the card is
            selected. Stopping propagation prevents the surrounding Link from
            navigating on click. */}
        <div
          className={`absolute left-3 top-3 z-10 transition-opacity ${
            isChecked ? "opacity-100" : "opacity-0 group-hover:opacity-100"
          }`}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleOne(ll.id);
          }}
        >
          <input
            type="checkbox"
            aria-label={`Select ${ll.name}`}
            checked={isChecked}
            onChange={() => toggleOne(ll.id)}
            onClick={(e) => e.stopPropagation()}
            className="h-4 w-4 rounded border-border accent-primary cursor-pointer bg-background"
          />
        </div>
        <Link href={`/lead-lists/${ll.id}`}>
          <Card
            className={`hover:border-muted-foreground/30 transition-colors cursor-pointer ${
              ll.archived ? "opacity-60" : ""
            } ${isChecked ? "border-primary/60 bg-primary/5" : ""}`}
          >
            <CardContent className="p-5 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="font-medium truncate pl-6">{ll.name}</h3>
                <div className="flex items-center gap-1 shrink-0">
                  {ll.archived && <Badge variant="outline" className="text-xs">Archived</Badge>}
                  {!ll.tg_enrich_enabled && <Badge variant="outline" className="text-xs text-muted-foreground">TG off</Badge>}
                  {ll.x_enrich_enabled && <Badge variant="outline" className="text-xs">X on</Badge>}
                  {isScraping && <ScrapeProgressBadge listId={ll.id} />}
                </div>
              </div>
              {ll.csv_filename && ll.csv_filename !== "scraping..." && (
                <p className="text-xs text-muted-foreground truncate">
                  {ll.csv_filename}
                </p>
              )}
              <div className="flex gap-4 text-sm text-muted-foreground">
                <span>{ll.total_leads} leads</span>
                <span>{ll.campaign_count} campaigns</span>
              </div>
              <p className="text-xs text-muted-foreground">
                Created{" "}
                {new Date(
                  ll.created_at.endsWith("Z") ? ll.created_at : ll.created_at + "Z"
                ).toLocaleDateString()}
              </p>
            </CardContent>
          </Card>
        </Link>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Lists" className="flex-col items-start gap-3 sm:flex-row sm:items-center [&>div:first-child]:pl-10 sm:[&>div:first-child]:pl-0">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowArchived((v) => !v)}
          >
            <Archive className="h-4 w-4 mr-1.5" />
            {showArchived ? "Hide archived" : "Show archived"}
            {!showArchived && archivedLists.length === 0 && lists && (
              <span className="ml-1 text-muted-foreground">
                ({lists.filter((ll) => ll.archived).length})
              </span>
            )}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button>
                New List <ChevronDown className="ml-1.5 h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setShowCreate(true)}>
                Empty list
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setShowEventDialog(true)}>
                From LinkedIn Event
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </PageHeader>

      {showCreate && (
        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="flex gap-3">
              <Input
                placeholder="List name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                autoFocus
              />
              <Button onClick={handleCreate} disabled={createList.isPending}>
                Create
              </Button>
              <Button variant="outline" onClick={() => { setShowCreate(false); setNewName(""); setNewTgEnrich(true); }}>
                Cancel
              </Button>
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
              <input
                type="checkbox"
                checked={newTgEnrich}
                onChange={(e) => setNewTgEnrich(e.target.checked)}
                className="h-4 w-4 rounded border-border accent-primary"
              />
              <span>Enable Telegram enrichment</span>
              <span className="text-xs text-muted-foreground">(background sweeper will search TG handles for leads in this list)</span>
            </label>
            <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
              <input
                type="checkbox"
                checked={newXEnrich}
                onChange={(e) => setNewXEnrich(e.target.checked)}
                className="h-4 w-4 rounded border-border accent-primary"
              />
              <span>Enable X enrichment</span>
              <span className="text-xs text-muted-foreground">(background sweeper will search personal X handles for leads without one)</span>
            </label>
          </CardContent>
        </Card>
      )}

      <EventImportDialog
        open={showEventDialog}
        onOpenChange={setShowEventDialog}
        onStarted={handleScrapeStarted}
      />

      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search lists…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        {search && lists && (
          <span className="text-xs text-muted-foreground">
            {filteredLists.length} of {lists.length} match
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : activeLists.length === 0 && !showArchived ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <p className="text-muted-foreground">
              {search ? "No lists match your search" : "No lead lists yet"}
            </p>
            {!search && (
              <Button
                variant="outline"
                className="mt-4"
                onClick={() => setShowEventDialog(true)}
              >
                Import from LinkedIn Event
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-8">
          {activeLists.length > 0 && (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {activeLists.map(renderCard)}
            </div>
          )}

          {showArchived && archivedLists.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-sm font-medium text-muted-foreground">Archived</h3>
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {archivedLists.map(renderCard)}
              </div>
            </div>
          )}

          {showArchived && archivedLists.length === 0 && (
            <p className="text-sm text-muted-foreground">No archived lists.</p>
          )}
        </div>
      )}

      {/* Sticky bulk-action bar — appears when ≥1 list selected. */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 z-50 w-[calc(100%-2rem)] max-w-3xl -translate-x-1/2">
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-background/95 px-4 py-2 shadow-xl backdrop-blur">
            <span className="text-sm font-medium">
              {selectedIds.size} selected
            </span>
            <span className="text-muted-foreground">·</span>
            <Button
              size="sm"
              onClick={handleCreateBroadcast}
              className="h-8"
            >
              <Send className="h-3.5 w-3.5 mr-1.5" />
              Create broadcast
            </Button>
            {allSelectedActive && (
              <Button
                size="sm"
                onClick={handleBulkArchive}
                disabled={bulkPending !== null}
                className="h-8"
              >
                <Archive className="h-3.5 w-3.5 mr-1.5" />
                {bulkPending === "archive" ? "Archiving…" : "Archive"}
              </Button>
            )}
            {allSelectedArchived && (
              <Button
                size="sm"
                variant="outline"
                onClick={handleBulkUnarchive}
                disabled={bulkPending !== null}
                className="h-8"
              >
                <ArchiveRestore className="h-3.5 w-3.5 mr-1.5" />
                {bulkPending === "unarchive" ? "Restoring…" : "Restore"}
              </Button>
            )}
            {allSelectedArchived && (confirmingDelete ? (
              <>
                <p className="w-full text-xs text-destructive">
                  Contacts with no other list and their campaign history will be permanently deleted.
                </p>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={handleBulkDelete}
                  disabled={bulkPending !== null}
                  className="h-8"
                >
                  {bulkPending === "delete"
                    ? "Deleting…"
                    : `Confirm delete ${selectedIds.size}`}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setConfirmingDelete(false)}
                  className="h-8"
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setConfirmingDelete(true)}
                disabled={bulkPending !== null}
                className="h-8"
              >
                <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                Delete permanently
              </Button>
            ))}
            <button
              type="button"
              onClick={clearSelection}
              className="ml-1 rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Clear selection"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
