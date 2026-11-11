"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";
import type { Lead } from "@/lib/types";

// ── Accounts ──────────────────────────────────────────────────────────────

export function useAccounts(params?: { include_archived?: boolean }) {
  return useQuery({
    queryKey: ["accounts", params],
    queryFn: () => api.fetchAccounts(params),
  });
}

export function useAccount(id: string, opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["accounts", id],
    queryFn: () => api.fetchAccount(id),
    enabled: opts?.enabled ?? true,
  });
}

export function useAccountActivity(id: string) {
  return useQuery({
    queryKey: ["accounts", id, "activity"],
    queryFn: () => api.fetchAccountActivity(id),
  });
}

export function useAccountStats(id: string, days = 30) {
  return useQuery({
    queryKey: ["accounts", id, "stats", days],
    queryFn: () => api.fetchAccountStats(id, days),
  });
}

export function useAccountSchedule(id: string) {
  return useQuery({
    queryKey: ["accounts", id, "schedule"],
    queryFn: () => api.fetchAccountSchedule(id),
  });
}

export function useAccountHealth(id: string) {
  return useQuery({
    queryKey: ["accounts", id, "health"],
    queryFn: () => api.fetchAccountHealth(id),
  });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createAccount,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useUpdateAccount(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.updateAccount>[1]) =>
      api.updateAccount(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts", id] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useUpdateCookie(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { li_at_cookie: string }) => api.updateCookie(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts", id] }),
  });
}

// ── Campaigns ─────────────────────────────────────────────────────────────

export function useCampaigns(params?: { account_id?: string; status?: string; include_archived?: boolean }) {
  return useQuery({
    queryKey: ["campaigns", params],
    queryFn: () => api.fetchCampaigns(params),
  });
}

export function useCampaign(id: string) {
  return useQuery({ queryKey: ["campaigns", id], queryFn: () => api.fetchCampaign(id) });
}

export function useCreateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useUpdateCampaign(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Record<string, unknown>>) => api.updateCampaign(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns", id] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useActivateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.activateCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function usePauseCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.pauseCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useTimedPauseCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, minutes }: { id: string; minutes?: number }) =>
      api.timedPauseCampaign(id, minutes),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useStartAcceptanceCatchup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { campaignId: string; cutoff_hours_override?: number }) =>
      api.startAcceptanceCatchup(args.campaignId, {
        cutoff_hours_override: args.cutoff_hours_override,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useAcceptanceCatchupStatus(
  campaignId: string,
  taskId: string | null,
) {
  return useQuery({
    queryKey: ["catchup-status", campaignId, taskId],
    queryFn: () => api.fetchAcceptanceCatchupStatus(campaignId, taskId as string),
    enabled: !!taskId,
    refetchInterval: (q) => {
      const status = (q.state.data as api.AcceptanceCatchupTask | undefined)?.status;
      return status === "running" ? 3000 : false;
    },
  });
}

export function useCampaignStats(id: string, days = 30, granularity: "day" | "hour" = "day") {
  return useQuery({
    queryKey: ["campaign-stats", id, days, granularity],
    queryFn: () => api.fetchCampaignStats(id, days, granularity),
    enabled: !!id,
  });
}

export function useResetLeads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.resetCampaignLeads,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

// ── Leads ─────────────────────────────────────────────────────────────────

export function useLeads(
  campaignId: string,
  params?: {
    page?: number; per_page?: number; status?: string; search?: string;
    excludeRemoved?: boolean; leadListId?: string;
    sortBy?: string; sortDir?: "asc" | "desc";
    lastActivityAfter?: string; lastActivityBefore?: string;
    skipReason?: string;
  }
) {
  return useQuery({
    queryKey: ["leads", campaignId, params],
    queryFn: () => api.fetchLeads(campaignId, params),
  });
}

export function useImportCSV(campaignId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, listName }: { file: File; listName?: string }) =>
      api.importCSV(campaignId, file, listName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leads", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
    },
  });
}

// ── Lead Lists ───────────────────────────────────────────────────────────

export function useLeadLists(params?: { include_archived?: boolean }) {
  return useQuery({ queryKey: ["lead-lists", params], queryFn: () => api.fetchLeadLists(params) });
}

export function useLeadList(id: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["lead-lists", id],
    queryFn: () => api.fetchLeadList(id),
    enabled: options?.enabled ?? !!id,
  });
}

export function useCreateLeadList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createLeadList,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-lists"] }),
  });
}

export function useUpdateLeadList(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name?: string; tg_enrich_enabled?: boolean }) =>
      api.updateLeadList(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
      qc.invalidateQueries({ queryKey: ["lead-list", id] });
    },
  });
}

export function useDeleteLeadList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteLeadList,
    onSuccess: async () => {
      await Promise.all(["lead-lists", "lead-list-leads", "lead", "lead-activity", "activity", "leads", "global-leads", "campaigns", "broadcasts"].map(
        (key) => qc.invalidateQueries({ queryKey: [key] }),
      ));
    },
  });
}

export function useArchiveLeadList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.archiveLeadList,
    onSuccess: async () => {
      await Promise.all(["lead-lists", "lead", "activity", "leads", "global-leads", "campaigns", "broadcasts"].map(
        (key) => qc.invalidateQueries({ queryKey: [key] }),
      ));
    },
  });
}

export function useUnarchiveLeadList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.unarchiveLeadList,
    onSuccess: async () => {
      await Promise.all(["lead-lists", "lead", "activity", "leads", "global-leads", "campaigns", "broadcasts"].map(
        (key) => qc.invalidateQueries({ queryKey: [key] }),
      ));
    },
  });
}

export function useStartEventImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startEventImport,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-lists"] }),
  });
}

export function useScrapeStatus(listId: string | null) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["scrape-status", listId],
    queryFn: () => api.fetchScrapeStatus(listId!),
    enabled: !!listId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "running") return 3000;
      if (status === "done" || status === "error") {
        // Refresh the list once scrape settles
        qc.invalidateQueries({ queryKey: ["lead-lists"] });
        return false;
      }
      return 3000;
    },
  });
}

export function useReScrapeList(listId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (opts: { account_id?: string; limit?: number } = {}) =>
      api.reScrapeList(listId, opts),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-lists", listId] });
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
    },
  });
}

export function useImportCSVToList(listId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.importCSVToList(listId, file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-lists", listId] });
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
    },
  });
}

export function useLeadListLeads(
  listId: string,
  params?: { page?: number; per_page?: number },
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: ["lead-list-leads", listId, params],
    queryFn: () => api.fetchLeadListLeads(listId, params),
    enabled: options?.enabled ?? true,
  });
}

export function useAssignListToCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ listId, campaignId }: { listId: string; campaignId: string }) =>
      api.assignListToCampaign(listId, campaignId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useUnassignListFromCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ listId, campaignId }: { listId: string; campaignId: string }) =>
      api.unassignListFromCampaign(listId, campaignId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useReorderCampaignLists() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ campaignId, orderedListIds }: { campaignId: string; orderedListIds: string[] }) =>
      api.reorderCampaignLists(campaignId, orderedListIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

// ── Global Leads ─────────────────────────────────────────────────────────

export function useGlobalLeads(params?: {
  include_archived?: boolean;
  page?: number; per_page?: number;
  lead_list_id?: string; campaign_id?: string;
  status?: string; search?: string;
  sort_by?: string; sort_dir?: "asc" | "desc";
  last_activity_after?: string; last_activity_before?: string;
  skip_reason?: string;
  has_telegram?: boolean;
  has_twitter?: boolean;
  has_email?: boolean;
  tg_contacted?: boolean;
}) {
  return useQuery({
    queryKey: ["global-leads", params],
    queryFn: () => api.fetchGlobalLeads(params),
  });
}

export function useBulkSkipLeads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.bulkSkipLeads,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useBulkRemoveLeads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.bulkRemoveLeads,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useBulkRequeueLeads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.bulkRequeueLeads,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useDeleteLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteLead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useRestoreLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.restoreLead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useSkipLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.skipLead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useRequeueLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.requeueLead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

// ── Archive ───────────────────────────────────────────────────────────────

export function useArchiveAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.archiveAccount,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useUnarchiveAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.unarchiveAccount,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useArchiveCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.archiveCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useUnarchiveCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.unarchiveCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

// ── Single Lead ───────────────────────────────────────────────────────────

// Any list query whose rows are leads. A row there is enough to draw the lead
// page at once while the full record (lists, campaigns) loads.
const LEAD_ROW_ROOTS = ["global-leads", "leads", "lead-list-leads", "lead-lookup"];

function findCachedLead(qc: ReturnType<typeof useQueryClient>, id: string): Lead | undefined {
  for (const root of LEAD_ROW_ROOTS) {
    for (const [, data] of qc.getQueriesData<unknown>({ queryKey: [root] })) {
      if (!data || typeof data !== "object") continue;
      const rows = (data as { items?: Lead[] }).items;
      if (Array.isArray(rows)) {
        const hit = rows.find((l) => l?.id === id);
        if (hit) return hit;
      } else if ((data as Lead).id === id) {
        return data as Lead;
      }
    }
  }
  return undefined;
}

export function useLead(id: string) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["lead", id],
    queryFn: () => api.fetchLead(id),
    enabled: !!id,
    placeholderData: () => findCachedLead(qc, id),
  });
}

/** Warm the three queries the lead page needs, so opening it is instant. */
export function prefetchLeadPage(qc: ReturnType<typeof useQueryClient>, id: string) {
  return Promise.all([
    qc.prefetchQuery({ queryKey: ["lead", id], queryFn: () => api.fetchLead(id) }),
    qc.prefetchQuery({ queryKey: ["lead-activity", id], queryFn: () => api.fetchLeadActivity(id) }),
    qc.prefetchQuery({ queryKey: ["lead-notes", id], queryFn: () => api.fetchLeadNotes(id) }),
  ]);
}

export function useUpdateLead(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.updateLead>[1]) =>
      api.updateLead(id, data),
    onSuccess: (updated) => {
      qc.setQueryData(["lead", id], updated);
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["global-leads"] });
    },
  });
}

export function useLeadActivity(id: string) {
  return useQuery({
    queryKey: ["lead-activity", id],
    queryFn: () => api.fetchLeadActivity(id),
    enabled: !!id,
  });
}

export function useFindTelegram(leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (force: boolean) => api.startFindTelegram(leadId, force),
    onSuccess: () => {
      // Invalidate lead so telegram_alternatives persisted by the background
      // task are reflected when the poll detects completion.
      qc.invalidateQueries({ queryKey: ["lead", leadId] });
    },
  });
}

export function useFindTelegramStatus(leadId: string, taskId: string | null) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["find-tg-status", leadId, taskId],
    queryFn: () => api.getFindTelegramStatus(leadId, taskId as string),
    enabled: !!taskId,
    refetchInterval: (q) => {
      const status = (q.state.data as { status?: string } | undefined)?.status;
      if (status === "done" || status === "error") {
        // Refresh the lead to pick up the persisted telegram_alternatives
        qc.invalidateQueries({ queryKey: ["lead", leadId] });
        return false;
      }
      return 2000;
    },
  });
}

export function useEnrichLeadPhone(leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.enrichLeadPhone(leadId),
    onSuccess: (data) => {
      if (data.found) qc.invalidateQueries({ queryKey: ["lead", leadId] });
    },
  });
}

// ── Lead notes (HubSpot-style multi-note) ───────────────────────────────────

export function useLeadNotes(leadId: string) {
  return useQuery({
    queryKey: ["lead-notes", leadId],
    queryFn: () => api.fetchLeadNotes(leadId),
    enabled: !!leadId,
  });
}

export function useCreateLeadNote(leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: string) => api.createLeadNote(leadId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-notes", leadId] }),
  });
}

export function useUpdateLeadNote(leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ noteId, body }: { noteId: string; body: string }) =>
      api.updateLeadNote(leadId, noteId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-notes", leadId] }),
  });
}

export function useDeleteLeadNote(leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (noteId: string) => api.deleteLeadNote(leadId, noteId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-notes", leadId] }),
  });
}

export function useCloneCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: { id: string; name: string; account_id: string }) =>
      api.cloneCampaign(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

// ── Scrapers ──────────────────────────────────────────────────────────────

export function useScrapers() {
  return useQuery({
    queryKey: ["scrapers"],
    queryFn: () => api.fetchScrapers(),
    refetchInterval: 30_000,
  });
}

// ── Broadcasts ────────────────────────────────────────────────────────────

export function useBroadcasts(params?: { account_id?: string; status?: string; include_archived?: boolean }) {
  return useQuery({
    queryKey: ["broadcasts", params],
    queryFn: () => api.fetchBroadcasts(params),
  });
}

export function useBroadcast(id: string) {
  return useQuery({
    queryKey: ["broadcasts", id],
    queryFn: () => api.fetchBroadcast(id),
    enabled: !!id,
  });
}

export function useCreateBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createBroadcast,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broadcasts"] }),
  });
}

export function useUpdateBroadcast(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.updateBroadcast>[1]) =>
      api.updateBroadcast(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["broadcasts", id] });
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
    },
  });
}

export function useDeleteBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteBroadcast,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broadcasts"] }),
  });
}

export function useActivateBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.activateBroadcast,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broadcasts"] }),
  });
}

export function usePauseBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.pauseBroadcast,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broadcasts"] }),
  });
}

export function useArchiveBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.archiveBroadcast,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broadcasts"] }),
  });
}

export function useUnarchiveBroadcast() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.unarchiveBroadcast,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broadcasts"] }),
  });
}

export function useBroadcastLeads(id: string, params?: { limit?: number; offset?: number; status?: string }) {
  return useQuery({
    queryKey: ["broadcasts", id, "leads", params],
    queryFn: () => api.fetchBroadcastLeads(id, params),
    enabled: !!id,
  });
}
