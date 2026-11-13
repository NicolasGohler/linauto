export interface Account {
  id: string;
  name: string;
  status: string;
  daily_limit: number;
  daily_message_limit: number;
  weekly_limit: number;
  timezone: string | null;
  proxy_country: string | null;
  proxy_host: string | null;
  proxy_port: number | null;
  proxy_username: string | null;
  proxy_password_set: boolean;
  paused_until: string | null;
  withdraw_threshold: number | null;
  auto_withdraw_interval_days: number;
  auto_withdraw_last_run: string | null;
  pending_invitations_count: number | null;
  pending_requests: number | null;
  avatar_path: string | null;
  archived: boolean;
  work_start_hour: number | null;
  work_end_hour: number | null;
  created_at: string;
  updated_at: string;
}

export interface ProxyTestResult {
  ok: boolean;
  ip?: string | null;
  country?: string | null;
  latency_ms?: number | null;
  error?: string | null;
}

export interface CampaignStatDaily {
  date: string;
  sent: number;
  accepted: number;
  errors: number;
}

export interface CampaignStats {
  daily: CampaignStatDaily[];
  summary: {
    total_sent: number;
    total_accepted: number;
    acceptance_rate: number;
    avg_time_to_accept_hours: number | null;
  };
}

export interface Campaign {
  id: string;
  account_id: string;
  account_name: string | null;
  name: string;
  status: string;
  archived: boolean;
  connection_message_template: string | null;
  followup_message_template: string | null;
  followup_delay_hours: number;
  followup_enabled: boolean;
  followup_message_1: string | null;
  followup_message_2: string | null;
  followup_message_3: string | null;
  /** Hours between message 1 and message 2 (randomized per lead). 0 = sent together. */
  followup_step2_delay_hours: number;
  /** Sample mode: sequences still allowed to start. null = no limit, 0 = paused. */
  followup_sample_remaining: number | null;
  weekend_enabled: boolean;
  filter_no_photo: boolean;
  filter_min_connections: number | null;
  filter_exclude_open_to_work: boolean;
  csv_filename: string | null;
  total_leads: number;
  status_counts: Record<string, number> | null;
  assigned_lists: { id: string; name: string; total_leads: number; status_counts: Record<string, number> | null; priority: number }[];
  account_status: string | null;
  account_paused_until: string | null;
  account_timezone: string | null;
  account_dispatch_mode: string | null;
  estimated_remaining_today: number | null;
  paused_at: string | null;
  resume_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Broadcast {
  id: string;
  account_id: string;
  account_name: string | null;
  source_list_id: string | null;
  source_list_name: string | null;
  name: string;
  status: string; // draft | active | paused | completed
  message_1: string | null;
  message_2: string | null;
  message_3: string | null;
  /** 0 = no delay (5 s); positive = hours (fractional values = minutes). */
  delay_between_hours: number;
  weekend_enabled: boolean;
  /** "skip" = original behaviour; "branch" = route on conversation history. */
  conversation_routing: string;
  /** Messages used in branch mode when prior outgoing-only conversation detected. */
  message_prior_only: string | null;
  message_prior_only_2: string | null;
  message_prior_only_3: string | null;
  total_leads: number;
  archived: boolean;
  paused_at: string | null;
  // Bucket counts: pending | sent | sequence_complete | skipped | manual_outreach | error
  status_counts: Record<string, number> | null;
  messages_sent: number | null;
  created_at: string;
  updated_at: string;
}

export interface BroadcastLead {
  id: string;
  broadcast_id: string;
  lead_id: string;
  status: string; // pending | sent | sequence_complete | skipped | error
  scheduled_at: string | null;
  last_message_sent_at: string | null;
  last_message_index: number | null;
  next_message_at: string | null;
  error_message: string | null;
  retry_count: number;
  skipped_reason: string | null;
  lead_name: string | null;
  lead_company: string | null;
  lead_linkedin_url: string | null;
  created_at: string;
}

export interface BroadcastLeadsPage {
  items: BroadcastLead[];
  total: number;
  limit: number;
  offset: number;
}

export interface LeadList {
  id: string;
  name: string;
  csv_filename: string | null;
  total_leads: number;
  campaign_count: number;
  archived: boolean;
  tg_enrich_enabled: boolean;
  x_enrich_enabled: boolean;
  source_url: string | null;
  scrape_account_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ActivityItem {
  id: string;
  source: "action_log" | "lead_event" | "lead_note";
  event_type: string;
  created_at: string;
  account_id?: string | null;
  account_name?: string | null;
  campaign_id?: string | null;
  campaign_name?: string | null;
  lead_id?: string | null;
  lead_name?: string | null;
  status?: string | null;
  details?: Record<string, unknown> | null;
  actor_user_id?: string | null;
  actor_name?: string | null;
}

export interface DashboardUser {
  id: string;
  handle: string;
  display_name?: string | null;
  is_superadmin: boolean;
  is_active: boolean;
}

export interface ActivityPage {
  items: ActivityItem[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface LeadListStats {
  total: number;
  acceptance_rate: number;
  tg_coverage: number;
  email_coverage: number;
  twitter_coverage: number;
  tg_contacted_rate: number;
  x_contacted_rate: number;
}

export interface LeadListDetail extends LeadList {
  campaigns: { id: string; name: string }[];
  stats: LeadListStats | null;
}

export interface LeadListRef {
  id: string;
  name: string;
  added_at: string;
}

export interface CampaignRef {
  id: string;
  name: string;
  status: string;
  account_name: string | null;
}

export interface Lead {
  id: string;
  campaign_id: string | null;
  lead_list_id: string | null;
  linkedin_url: string | null;
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  title: string | null;
  email: string | null;
  phone: string | null;
  twitter_url: string | null;
  telegram_username: string | null;
  telegram_alternatives: string[] | null;
  tg_contacted_at: string | null;
  x_alternatives: XCandidate[] | null;
  x_contacted_at: string | null;
  notes: string | null;
  location: string | null;
  extra_data: Record<string, unknown> | null;
  status: string;
  connection_requested_at: string | null;
  connection_accepted_at: string | null;
  followup_sent_at: string | null;
  last_activity_at: string | null;
  error_message: string | null;
  retry_count: number;
  scheduled_at: string | null;
  created_at: string;
  updated_at: string | null;
  campaign_name: string | null;
  lead_list_name: string | null;
  lead_lists: LeadListRef[];
  campaigns: CampaignRef[];
}

export interface LeadUpdateRequest {
  first_name?: string | null;
  last_name?: string | null;
  company?: string | null;
  title?: string | null;
  email?: string | null;
  phone?: string | null;
  linkedin_url?: string | null;
  twitter_url?: string | null;
  telegram_username?: string | null;
  tg_contacted?: boolean | null;
  x_contacted?: boolean | null;
  notes?: string | null;
  location?: string | null;
}

export interface XCandidate {
  username: string;
  score: number;
  name?: string;
  bio?: string;
  can_dm?: boolean | null;
  confident?: boolean;
}

export interface FindXTask {
  task_id: string;
  status: "running" | "done" | "error";
  x_username: string | null;
  x_score: number | null;
  x_alternatives: XCandidate[];
  logs: string[];
  error: string | null;
}

export interface FindTelegramTask {
  task_id: string;
  status: "running" | "done" | "error";
  telegram_username: string | null;
  telegram_alternatives: string[];
  logs: string[];
  error: string | null;
}

export interface LeadNote {
  id: string;
  lead_id: string;
  body: string;
  created_at: string;
  updated_at: string | null;
}

export interface LeadActivity {
  id: string;
  action_type: string;
  status: string;
  details: Record<string, unknown> | null;
  created_at: string;
  account_name: string | null;
  actor_name?: string | null;
  source: "action_log" | "lead_event";
}

export interface LeadPage {
  items: Lead[];
  total: number;
  page: number;
  per_page: number;
}

export interface ActionLog {
  id: string;
  account_id: string;
  campaign_id: string | null;
  lead_id: string | null;
  action_type: string;
  status: string;
  details: Record<string, unknown> | null;
  created_at: string;
  lead_first_name: string | null;
  lead_last_name: string | null;
  lead_url: string | null;
}

export interface DailyStat {
  date: string;
  connection_requests_sent: number;
  followup_messages_sent: number;
  connections_accepted: number;
  errors: number;
}

export interface ScheduleSlot {
  lead_id: string;
  first_name: string | null;
  last_name: string | null;
  linkedin_url: string | null;
  campaign_name: string;
  scheduled_at: string | null;
  status: string; // "scheduled" or "sent"
}

export interface AccountHealth {
  last_action_at: string | null;
  days_since_last_activity: number | null;
  error_rate_7d: number;
  total_actions_7d: number;
  errors_7d: number;
  last_error_message: string | null;
}

export interface ScrapeStatus {
  status: "running" | "done" | "error" | "unknown";
  collected: number;
  error?: string | null;
}

export interface ImportResponse {
  total_rows: number;
  imported: number;
  duplicates_skipped: number;
  no_url_skipped: number;
  errors: string[];
}

export interface ScraperStatus {
  site: string;
  has_cookies: boolean;
  captured_at: string | null;
  age_hours: number | null;
}

export interface TgSweepResult {
  lead_id: string;
  lead_name: string;
  company: string | null;
  found: boolean;
  telegram_username: string | null;
  searched_at: string;
  match: string | null;
}

export interface TgSweepStatus {
  locked: boolean;
  flood_wait_until: string | null;
  flood_wait_remaining_seconds: number | null;
  last_processed_at: string | null;
  pending_count: number;
  searched_count: number;
  found_count: number;
  recent_results: TgSweepResult[];
}
