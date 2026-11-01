import type {
  Account,
  Campaign,
  CampaignStats,
  Lead,
  LeadList,
  LeadListDetail,
  LeadPage,
  ActionLog,
  DailyStat,
  ImportResponse,
  ScrapeStatus,
  ScheduleSlot,
  AccountHealth,
  LeadUpdateRequest,
  LeadActivity,
  LeadNote,
  FindTelegramTask,
} from "./types";

// API calls go to same-origin /api/v1/* — the Next.js route handler at
// src/app/api/v1/[...path]/route.ts proxies them to the backend and attaches
// the BACKEND_API_KEY server-side. No secrets in the browser.

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
}

// ── Accounts ──────────────────────────────────────────────────────────────

export const fetchAccounts = (params?: { include_archived?: boolean }) => {
  const qs = params?.include_archived ? "?include_archived=true" : "";
  return apiFetch<Account[]>(`/accounts${qs}`);
};

export const fetchAccount = (id: string) => apiFetch<Account>(`/accounts/${id}`);

export const createAccount = (data: {
  name: string;
  li_at_cookie?: string;
  timezone?: string;
  proxy_host?: string | null;
  proxy_port?: number | null;
  proxy_username?: string | null;
  proxy_password?: string | null;
  proxy_country?: string | null;
}) => apiFetch<Account>("/accounts", { method: "POST", body: JSON.stringify(data) });

// For password edits: omit `proxy_password` entirely to keep the existing
// value. Pass an empty string to clear.
export const updateAccount = (
  id: string,
  data: {
    name?: string;
    timezone?: string;
    daily_limit?: number;
    daily_message_limit?: number;
    weekly_limit?: number;
    withdraw_threshold?: number | null;
    proxy_host?: string | null;
    proxy_port?: number | null;
    proxy_username?: string | null;
    proxy_password?: string;
    proxy_country?: string | null;
  }
) =>
  apiFetch<Account>(`/accounts/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const testProxyUnsaved = (data: {
  proxy_host: string;
  proxy_port: number;
  proxy_username?: string | null;
  proxy_password?: string | null;
}) =>
  apiFetch<import("./types").ProxyTestResult>("/accounts/test-proxy", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const testProxyStored = (id: string) =>
  apiFetch<import("./types").ProxyTestResult>(`/accounts/${id}/test-proxy`, {
    method: "POST",
  });

export const updateCookie = (id: string, data: { li_at_cookie: string }) =>
  apiFetch<Account>(`/accounts/${id}/cookie`, { method: "PUT", body: JSON.stringify(data) });

export const startLoginSession = (id: string) =>
  apiFetch<{ novnc_url: string; account_id: string }>(`/accounts/${id}/login-session`, {
    method: "POST",
  });

export const finishLoginSession = (id: string) =>
  apiFetch<{ success: boolean; message: string }>(`/accounts/${id}/login-session/finish`, {
    method: "POST",
  });

export const cancelLoginSession = (id: string) =>
  apiFetch<{ success: boolean; message: string }>(`/accounts/${id}/login-session/cancel`, {
    method: "POST",
  });

export const startBrowseSession = (id: string) =>
  apiFetch<{ novnc_url: string; account_id: string }>(`/accounts/${id}/browse-session`, {
    method: "POST",
  });

export const closeBrowseSession = (id: string) =>
  apiFetch<{ success: boolean; message: string }>(`/accounts/${id}/browse-session/close`, {
    method: "POST",
  });

export const getBrowseSessionStatus = (id: string) =>
  apiFetch<{ active: boolean }>(`/accounts/${id}/browse-session/status`);

export const fetchAccountActivity = (id: string, limit = 500) =>
  apiFetch<ActionLog[]>(`/accounts/${id}/activity?limit=${limit}`);

export const fetchAccountStats = (id: string, days = 30) =>
  apiFetch<DailyStat[]>(`/accounts/${id}/stats?days=${days}`);

export const fetchAccountSchedule = (id: string) =>
  apiFetch<ScheduleSlot[]>(`/accounts/${id}/schedule`);

export const fetchAccountHealth = (id: string) =>
  apiFetch<AccountHealth>(`/accounts/${id}/health`);

// ── Campaigns ─────────────────────────────────────────────────────────────

export const fetchCampaigns = (params?: { account_id?: string; status?: string; include_archived?: boolean }) => {
  const sp = new URLSearchParams();
  if (params?.account_id) sp.set("account_id", params.account_id);
  if (params?.status) sp.set("status", params.status);
  if (params?.include_archived) sp.set("include_archived", "true");
  const qs = sp.toString();
  return apiFetch<Campaign[]>(`/campaigns${qs ? `?${qs}` : ""}`);
};

export const fetchCampaign = (id: string) => apiFetch<Campaign>(`/campaigns/${id}`);

export const createCampaign = (data: {
  account_id: string;
  name: string;
  connection_message_template?: string;
}) => apiFetch<Campaign>("/campaigns", { method: "POST", body: JSON.stringify(data) });

export const updateCampaign = (id: string, data: Partial<Campaign>) =>
  apiFetch<Campaign>(`/campaigns/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const activateCampaign = (id: string) =>
  apiFetch<Campaign>(`/campaigns/${id}/activate`, { method: "POST" });

export const pauseCampaign = (id: string) =>
  apiFetch<Campaign>(`/campaigns/${id}/pause`, { method: "POST" });

export const timedPauseCampaign = (id: string, minutes: number = 60) =>
  apiFetch<Campaign>(`/campaigns/${id}/timed-pause?minutes=${minutes}`, { method: "POST" });

export const resetCampaignLeads = (id: string) =>
  apiFetch<{ reset_count: number }>(`/campaigns/${id}/reset-leads`, { method: "POST" });

export type AcceptanceCatchupTask = {
  status: "running" | "done" | "error";
  campaign_id: string;
  accepted_count: number;
  scanned_slugs: number;
  cutoff_hours: number;
  hit_cutoff: boolean;
  hit_iteration_cap: boolean;
  skipped_reason: string | null;
  error: string | null;
};

export const startAcceptanceCatchup = (
  id: string,
  body?: { cutoff_hours_override?: number },
) =>
  apiFetch<{ task_id: string }>(`/campaigns/${id}/acceptance-catchup`, {
    method: "POST",
    body: JSON.stringify(body ?? {}),
  });

export const fetchAcceptanceCatchupStatus = (campaignId: string, taskId: string) =>
  apiFetch<AcceptanceCatchupTask>(`/campaigns/${campaignId}/acceptance-catchup/${taskId}`);

export const fetchCampaignStats = (id: string, days = 30, granularity: "day" | "hour" = "day") =>
  apiFetch<CampaignStats>(`/campaigns/${id}/stats?days=${days}&granularity=${granularity}`);

// ── Leads ─────────────────────────────────────────────────────────────────

export const fetchLeads = (
  campaignId: string,
  params?: {
    page?: number; per_page?: number; status?: string; search?: string;
    excludeRemoved?: boolean; leadListId?: string;
    sortBy?: string; sortDir?: "asc" | "desc";
    lastActivityAfter?: string; lastActivityBefore?: string;
    skipReason?: string;
  }
) => {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  if (params?.status) sp.set("status", params.status);
  if (params?.search) sp.set("search", params.search);
  if (params?.excludeRemoved) sp.set("exclude_removed", "true");
  if (params?.leadListId) sp.set("lead_list_id", params.leadListId);
  if (params?.sortBy) sp.set("sort_by", params.sortBy);
  if (params?.sortDir) sp.set("sort_dir", params.sortDir);
  if (params?.lastActivityAfter) sp.set("last_activity_after", params.lastActivityAfter);
  if (params?.lastActivityBefore) sp.set("last_activity_before", params.lastActivityBefore);
  if (params?.skipReason) sp.set("skip_reason", params.skipReason);
  const qs = sp.toString();
  return apiFetch<LeadPage>(`/campaigns/${campaignId}/leads${qs ? `?${qs}` : ""}`);
};

export const exportCampaignLeadsCSV = async (
  campaignId: string,
  filename: string,
  filters?: { status?: string; search?: string; excludeRemoved?: boolean; leadListId?: string },
) => {
  const sp = new URLSearchParams();
  if (filters?.status) sp.set("status", filters.status);
  if (filters?.search) sp.set("search", filters.search);
  if (filters?.excludeRemoved) sp.set("exclude_removed", "true");
  if (filters?.leadListId) sp.set("lead_list_id", filters.leadListId);
  const qs = sp.toString();
  const res = await fetch(`/api/v1/campaigns/${campaignId}/leads/export${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};

export const importCSV = async (
  campaignId: string,
  file: File,
  listName?: string
): Promise<ImportResponse> => {
  const formData = new FormData();
  formData.append("file", file);
  if (listName) {
    formData.append("list_name", listName);
  }

  const res = await fetch(`/api/v1/campaigns/${campaignId}/import`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
};

// ── Lead Lists ───────────────────────────────────────────────────────────

export const fetchLeadLists = (params?: { include_archived?: boolean }) => {
  const qs = params?.include_archived ? "?include_archived=true" : "";
  return apiFetch<LeadList[]>(`/lead-lists${qs}`);
};

export const fetchLeadList = (id: string) =>
  apiFetch<LeadListDetail>(`/lead-lists/${id}`);

export const createLeadList = (data: { name: string; tg_enrich_enabled?: boolean }) =>
  apiFetch<LeadList>("/lead-lists", { method: "POST", body: JSON.stringify(data) });

export const updateLeadList = (id: string, data: { name?: string; tg_enrich_enabled?: boolean }) =>
  apiFetch<LeadList>(`/lead-lists/${id}`, { method: "PATCH", body: JSON.stringify(data) });

export const deleteLeadList = (id: string) =>
  apiFetch<{ ok: boolean }>(`/lead-lists/${id}`, { method: "DELETE" });

export const archiveLeadList = (id: string) =>
  apiFetch<LeadList>(`/lead-lists/${id}/archive`, { method: "POST" });

export const unarchiveLeadList = (id: string) =>
  apiFetch<LeadList>(`/lead-lists/${id}/unarchive`, { method: "POST" });

export const importCSVToList = async (listId: string, file: File): Promise<ImportResponse> => {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`/api/v1/lead-lists/${listId}/import`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
};

export const fetchLeadListLeads = (
  listId: string,
  params?: { page?: number; per_page?: number }
) => {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  const qs = sp.toString();
  return apiFetch<LeadPage>(`/lead-lists/${listId}/leads${qs ? `?${qs}` : ""}`);
};

export const exportLeadListCSV = async (listId: string, filename: string) => {
  const res = await fetch(`/api/v1/lead-lists/${listId}/export`);
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};

export const assignListToCampaign = (listId: string, campaignId: string) =>
  apiFetch<{ leads_added: number }>(`/lead-lists/${listId}/assign`, {
    method: "POST",
    body: JSON.stringify({ campaign_id: campaignId }),
  });

export const unassignListFromCampaign = (listId: string, campaignId: string) =>
  apiFetch<{ leads_removed: number }>(`/lead-lists/${listId}/unassign`, {
    method: "POST",
    body: JSON.stringify({ campaign_id: campaignId }),
  });

export const reorderCampaignLists = (campaignId: string, orderedListIds: string[]) =>
  apiFetch<Campaign>(`/campaigns/${campaignId}/lists/order`, {
    method: "PUT",
    body: JSON.stringify({ ordered_list_ids: orderedListIds }),
  });

// ── Global Leads / Lead Management ──────────────────────────────────────

export const fetchGlobalLeads = (params?: {
  page?: number; per_page?: number;
  include_archived?: boolean;
  // lead_list_id accepts a comma-separated list of IDs, plus the "__unassigned__" sentinel
  // (mixable with real IDs, e.g. "abc,__unassigned__,def") to filter by multiple lists at once.
  lead_list_id?: string; campaign_id?: string;
  // status accepts a comma-separated list of status values for multi-select filtering.
  status?: string; search?: string;
  sort_by?: string; sort_dir?: "asc" | "desc";
  last_activity_after?: string; last_activity_before?: string;
  skip_reason?: string;
  has_telegram?: boolean;
  has_twitter?: boolean;
  has_email?: boolean;
  tg_contacted?: boolean;
}) => {
  const sp = new URLSearchParams();
  if (params?.include_archived) sp.set("include_archived", "true");
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  if (params?.lead_list_id) {
    const parts = params.lead_list_id.split(",").filter(Boolean);
    const realIds = parts.filter((p) => p !== "__unassigned__");
    if (parts.includes("__unassigned__")) sp.set("unassigned_list", "true");
    if (realIds.length) sp.set("lead_list_id", realIds.join(","));
  }
  if (params?.campaign_id === "__unassigned__") {
    sp.set("unassigned_campaign", "true");
  } else if (params?.campaign_id) {
    sp.set("campaign_id", params.campaign_id);
  }
  if (params?.status) sp.set("status", params.status);
  if (params?.search) sp.set("search", params.search);
  if (params?.sort_by) sp.set("sort_by", params.sort_by);
  if (params?.sort_dir) sp.set("sort_dir", params.sort_dir);
  if (params?.last_activity_after) sp.set("last_activity_after", params.last_activity_after);
  if (params?.last_activity_before) sp.set("last_activity_before", params.last_activity_before);
  if (params?.skip_reason) sp.set("skip_reason", params.skip_reason);
  if (params?.has_telegram !== undefined) sp.set("has_telegram", String(params.has_telegram));
  if (params?.has_twitter !== undefined) sp.set("has_twitter", String(params.has_twitter));
  if (params?.has_email !== undefined) sp.set("has_email", String(params.has_email));
  if (params?.tg_contacted !== undefined) sp.set("tg_contacted", String(params.tg_contacted));
  const qs = sp.toString();
  return apiFetch<LeadPage>(`/leads${qs ? `?${qs}` : ""}`);
};

export const bulkSkipLeads = (lead_ids: string[]) =>
  apiFetch<{ updated: number }>("/leads/bulk/skip", { method: "POST", body: JSON.stringify({ lead_ids }) });

export const bulkRemoveLeads = (lead_ids: string[]) =>
  apiFetch<{ updated: number }>("/leads/bulk/remove", { method: "POST", body: JSON.stringify({ lead_ids }) });

export const bulkRequeueLeads = (lead_ids: string[]) =>
  apiFetch<{ updated: number }>("/leads/bulk/requeue", { method: "POST", body: JSON.stringify({ lead_ids }) });

export const deleteLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}`, { method: "DELETE" });

export const restoreLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}/restore`, { method: "POST" });

export const skipLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}/skip`, { method: "POST" });

export const requeueLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}/requeue`, { method: "POST" });

export const fetchLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}`);

export const updateLead = (id: string, data: LeadUpdateRequest) =>
  apiFetch<Lead>(`/leads/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });

export const fetchLeadActivity = (id: string, limit = 50) =>
  apiFetch<LeadActivity[]>(`/leads/${id}/activity?limit=${limit}`);

export const startFindTelegram = (leadId: string, force = false) =>
  apiFetch<FindTelegramTask>(`/leads/${leadId}/find-telegram${force ? "?force=true" : ""}`, { method: "POST" });

export const getFindTelegramStatus = (leadId: string, taskId: string) =>
  apiFetch<FindTelegramTask>(`/leads/${leadId}/find-telegram/${taskId}`);

export const enrichLeadPhone = (leadId: string) =>
  apiFetch<{ phone: string | null; found: boolean }>(`/leads/${leadId}/enrich-phone`, { method: "POST" });

// ── Lead notes (HubSpot-style multi-note) ───────────────────────────────────

export const fetchLeadNotes = (leadId: string) =>
  apiFetch<LeadNote[]>(`/leads/${leadId}/notes`);

export const createLeadNote = (leadId: string, body: string) =>
  apiFetch<LeadNote>(`/leads/${leadId}/notes`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });

export const updateLeadNote = (leadId: string, noteId: string, body: string) =>
  apiFetch<LeadNote>(`/leads/${leadId}/notes/${noteId}`, {
    method: "PATCH",
    body: JSON.stringify({ body }),
  });

export const deleteLeadNote = (leadId: string, noteId: string) =>
  apiFetch<void>(`/leads/${leadId}/notes/${noteId}`, { method: "DELETE" });

// ── TG Enrichment ─────────────────────────────────────────────────────────

export interface TgEnrichmentStatus {
  total: number;
  enriched: number;
  missing: number;
  coverage_pct: number;
}

export const fetchTgEnrichmentStatus = (campaignId: string) =>
  apiFetch<TgEnrichmentStatus>(`/campaigns/${campaignId}/leads/enrich/telegram/status`);

export const startTgEnrichment = (campaignId: string) =>
  apiFetch<{ message: string }>(`/campaigns/${campaignId}/leads/enrich/telegram`, { method: "POST" });

// ── Avatars ──────────────────────────────────────────────────────────────

export const getAvatarUrl = (accountId: string) =>
  `/api/v1/accounts/${accountId}/avatar`;

export const fetchAvatar = (accountId: string) =>
  apiFetch<{ success: boolean }>(`/accounts/${accountId}/fetch-avatar`, { method: "POST" });

export const replanAccount = (id: string) =>
  apiFetch<{ ok: boolean; scheduled: number }>(`/accounts/${id}/replan`, { method: "POST" });

// ── Invitation Withdrawal ────────────────────────────────────────────────

export const fetchInvitationCount = (accountId: string) =>
  apiFetch<{ count: number; account_id: string }>(
    `/accounts/${accountId}/invitations/count`,
    { method: "POST" }
  );

export const startWithdrawal = (accountId: string, count: number, order: "oldest" | "newest") =>
  apiFetch<{ task_id: string }>(
    `/accounts/${accountId}/invitations/withdraw`,
    { method: "POST", body: JSON.stringify({ count, order }) }
  );

export const pollWithdrawalStatus = (accountId: string, taskId: string) =>
  apiFetch<{ status: "running" | "done" | "error"; withdrawn: string[]; db_updated: number; error: string | null }>(
    `/accounts/${accountId}/invitations/withdraw/${taskId}`
  );

export const recoverStuckLeads = (accountId: string, dryRun: boolean) =>
  apiFetch<{ count: number; recovered: number }>(
    `/accounts/${accountId}/recover-stuck-leads?dry_run=${dryRun}`,
    { method: "POST" }
  );

// ── Connection Check ────────────────────────────────────────────────────

export const checkConnection = (accountId: string) =>
  apiFetch<{ valid: boolean; title?: string; has_content?: boolean; url?: string; reason?: string; error?: string; elapsed_ms: number }>(
    `/accounts/${accountId}/check-connection`,
    { method: "POST" }
  );

// ── Event Import ─────────────────────────────────────────────────────────

export const startEventImport = (data: {
  url: string;
  account_id: string;
  list_name?: string;
  limit?: number;
}) =>
  apiFetch<LeadList>("/lead-lists/event-import", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const fetchScrapeStatus = (listId: string) =>
  apiFetch<ScrapeStatus>(`/lead-lists/${listId}/scrape-status`);

export const reScrapeList = (listId: string, opts: { account_id?: string; limit?: number } = {}) =>
  apiFetch<LeadList>(`/lead-lists/${listId}/re-scrape`, {
    method: "POST",
    body: JSON.stringify(opts),
  });

// ── Archive ──────────────────────────────────────────────────────────────

export const archiveAccount = (id: string) =>
  apiFetch<Account>(`/accounts/${id}/archive`, { method: "POST" });

export const unarchiveAccount = (id: string) =>
  apiFetch<Account>(`/accounts/${id}/unarchive`, { method: "POST" });

export const archiveCampaign = (id: string) =>
  apiFetch<Campaign>(`/campaigns/${id}/archive`, { method: "POST" });

export const unarchiveCampaign = (id: string) =>
  apiFetch<Campaign>(`/campaigns/${id}/unarchive`, { method: "POST" });

export const cloneCampaign = (id: string, data: { name: string; account_id: string }) =>
  apiFetch<Campaign>(`/campaigns/${id}/clone`, { method: "POST", body: JSON.stringify(data) });

export const lookupLeadByUrl = (linkedinUrl: string) =>
  apiFetch<Lead>(`/leads/lookup?linkedin_url=${encodeURIComponent(linkedinUrl)}`);

// ── Scrapers ──────────────────────────────────────────────────────────────

export const fetchScrapers = () =>
  apiFetch<import("./types").ScraperStatus[]>("/scrapers");

export const startScraperLoginSession = (site: string) =>
  apiFetch<{ novnc_url: string; session_id: string }>(`/scrapers/${site}/login-session`, {
    method: "POST",
  });

export const finishScraperLoginSession = (site: string) =>
  apiFetch<{ success: boolean; cookie_count: number; captured_at: string }>(
    `/scrapers/${site}/login-session/finish`,
    { method: "POST" }
  );

export const cancelScraperLoginSession = (site: string) =>
  apiFetch<{ success: boolean }>(`/scrapers/${site}/login-session/cancel`, { method: "POST" });

export const fetchActivity = (params: {
  page?: number;
  per_page?: number;
  since?: string;
  until?: string;
  sources?: string;
  event_types?: string;
  account_id?: string;
  campaign_id?: string;
  user_id?: string;
}) => {
  const qs = new URLSearchParams();
  if (params.page)        qs.set("page",        String(params.page));
  if (params.per_page)    qs.set("per_page",     String(params.per_page));
  if (params.since)       qs.set("since",        params.since);
  if (params.until)       qs.set("until",        params.until);
  if (params.sources)     qs.set("sources",      params.sources);
  if (params.event_types) qs.set("event_types",  params.event_types);
  if (params.account_id)  qs.set("account_id",   params.account_id);
  if (params.campaign_id) qs.set("campaign_id",  params.campaign_id);
  if (params.user_id)     qs.set("user_id",      params.user_id);
  return apiFetch<import("./types").ActivityPage>(`/activity?${qs.toString()}`);
};

export const fetchUsers = () =>
  apiFetch<import("./types").DashboardUser[]>(`/users`);

export const fetchFundraisingRunStatus = () =>
  apiFetch<{ running: boolean; started_at?: string; finished_at?: string; exit?: string }>(
    "/scrapers/fundraising-run/status"
  );

export const fetchFundraisingRunLog = (tail = 200) =>
  apiFetch<{ lines: string[]; total_lines: number }>(
    `/scrapers/fundraising-run/log?tail=${tail}`
  );

export const triggerFundraisingRun = () =>
  apiFetch<{ success: boolean }>("/scrapers/fundraising-run/trigger", { method: "POST" });

export const fetchTgSweepStatus = () =>
  apiFetch<import("./types").TgSweepStatus>("/scrapers/tg-sweep/status");

// ── Broadcasts ────────────────────────────────────────────────────────────

export const fetchBroadcasts = (params?: {
  account_id?: string;
  status?: string;
  include_archived?: boolean;
}) => {
  const qs = new URLSearchParams();
  if (params?.account_id) qs.set("account_id", params.account_id);
  if (params?.status) qs.set("status", params.status);
  if (params?.include_archived) qs.set("include_archived", "true");
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<import("./types").Broadcast[]>(`/broadcasts${suffix}`);
};

export const fetchBroadcast = (id: string) =>
  apiFetch<import("./types").Broadcast>(`/broadcasts/${id}`);

export const createBroadcast = (data: {
  account_id: string;
  name: string;
  message_1: string;
  message_2?: string | null;
  message_3?: string | null;
  delay_between_hours?: number;
  weekend_enabled?: boolean;
  conversation_routing?: string;
  message_prior_only?: string | null;
  message_prior_only_2?: string | null;
  message_prior_only_3?: string | null;
  source_list_id?: string | null;
  source_list_ids?: string[] | null;
  lead_ids?: string[] | null;
}) =>
  apiFetch<import("./types").Broadcast>("/broadcasts", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const updateBroadcast = (id: string, data: Partial<{
  name: string;
  message_1: string;
  message_2: string;
  message_3: string;
  delay_between_hours: number;
  weekend_enabled: boolean;
  conversation_routing: string;
  message_prior_only: string | null;
  message_prior_only_2: string | null;
  message_prior_only_3: string | null;
}>) =>
  apiFetch<import("./types").Broadcast>(`/broadcasts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });

export const deleteBroadcast = (id: string) =>
  apiFetch<void>(`/broadcasts/${id}`, { method: "DELETE" });

export const activateBroadcast = (id: string) =>
  apiFetch<import("./types").Broadcast>(`/broadcasts/${id}/activate`, { method: "POST" });

export const pauseBroadcast = (id: string) =>
  apiFetch<import("./types").Broadcast>(`/broadcasts/${id}/pause`, { method: "POST" });

export const archiveBroadcast = (id: string) =>
  apiFetch<import("./types").Broadcast>(`/broadcasts/${id}/archive`, { method: "POST" });

export const unarchiveBroadcast = (id: string) =>
  apiFetch<import("./types").Broadcast>(`/broadcasts/${id}/unarchive`, { method: "POST" });

export const fetchBroadcastLeads = (
  id: string,
  params?: { limit?: number; offset?: number; status?: string }
) => {
  const qs = new URLSearchParams();
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  if (params?.status) qs.set("status", params.status);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<import("./types").BroadcastLeadsPage>(
    `/broadcasts/${id}/leads${suffix}`
  );
};
