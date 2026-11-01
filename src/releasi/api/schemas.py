"""Pydantic request/response schemas for the API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Accounts ──────────────────────────────────────────────────────────────

class AccountOut(BaseModel):
    id: str
    name: str
    status: str
    daily_limit: int
    # Unified daily cap on messages sent (broadcasts + post-acceptance
    # follow-ups combined). Semantics: distinct leads reached per day. A
    # 3-message sequence to one lead counts as one against the cap.
    daily_message_limit: int
    weekly_limit: int
    timezone: Optional[str] = None
    proxy_country: Optional[str] = None
    # Parsed components of proxy_url — the raw URL (with password) is never
    # returned to clients. proxy_password_set indicates whether a password is
    # stored, so the UI can show a masked placeholder and a "Change" toggle.
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password_set: bool = False
    paused_until: Optional[datetime] = None
    withdraw_threshold: Optional[int] = None
    auto_withdraw_interval_days: int = 30
    auto_withdraw_last_run: Optional[datetime] = None
    pending_invitations_count: Optional[int] = None
    pending_requests: Optional[int] = None
    avatar_path: Optional[str] = None
    dispatch_mode: Optional[str] = None
    archived: bool = False
    # Global scheduler work-window (sourced from settings.yaml at enrich time).
    # Exposed per-account so the UI can render the local "are we in the work
    # window right now?" state without hitting a separate /settings endpoint.
    work_start_hour: Optional[int] = None
    work_end_hour: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AccountCreate(BaseModel):
    name: str
    li_at_cookie: Optional[str] = None
    li_a_cookie: Optional[str] = None
    user_agent: Optional[str] = None
    timezone: Optional[str] = "Europe/Berlin"
    # Preferred: explicit proxy components. Backend assembles the URL.
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    # Legacy: raw URL (kept for CLI compatibility). If provided alongside
    # components, the raw URL wins.
    proxy_url: Optional[str] = None
    proxy_country: Optional[str] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None
    proxy_country: Optional[str] = None
    # Send any/all of the 4 proxy fields to change them. A password field that
    # is *absent* from the request body means "keep existing password";
    # presence (even empty string) replaces it. Use model_fields_set in the
    # route to distinguish.
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    daily_limit: Optional[int] = None
    daily_message_limit: Optional[int] = None
    weekly_limit: Optional[int] = None
    withdraw_threshold: Optional[int] = None
    auto_withdraw_interval_days: Optional[int] = None
    dispatch_mode: Optional[str] = None


class ProxyTestRequest(BaseModel):
    """Test proxy credentials without persisting. Used by /accounts/new form."""
    proxy_host: str
    proxy_port: int
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None


class ProxyTestResponse(BaseModel):
    ok: bool
    ip: Optional[str] = None
    country: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class CookieUpdate(BaseModel):
    li_at_cookie: str
    li_a_cookie: Optional[str] = None


# ── Campaigns ─────────────────────────────────────────────────────────────

class CampaignOut(BaseModel):
    id: str
    account_id: str
    account_name: Optional[str] = None
    name: str
    status: str
    connection_message_template: Optional[str] = None
    followup_message_template: Optional[str] = None
    followup_delay_hours: int
    followup_enabled: bool = False
    followup_message_1: Optional[str] = None
    followup_message_2: Optional[str] = None
    followup_message_3: Optional[str] = None
    weekend_enabled: bool = False
    filter_no_photo: bool
    filter_min_connections: Optional[int] = None
    filter_exclude_open_to_work: bool = False
    csv_filename: Optional[str] = None
    total_leads: int
    archived: bool = False
    paused_at: Optional[datetime] = None
    resume_at: Optional[datetime] = None
    status_counts: Optional[Dict[str, int]] = None
    assigned_lists: List[Dict[str, Any]] = []
    account_status: Optional[str] = None
    account_paused_until: Optional[datetime] = None
    account_timezone: Optional[str] = None
    account_dispatch_mode: Optional[str] = None
    estimated_remaining_today: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CampaignCreate(BaseModel):
    account_id: str
    name: str
    connection_message_template: Optional[str] = None
    followup_message_template: Optional[str] = None
    followup_delay_hours: int = 24
    followup_enabled: bool = False
    followup_message_1: Optional[str] = None
    followup_message_2: Optional[str] = None
    followup_message_3: Optional[str] = None
    weekend_enabled: bool = False
    filter_no_photo: bool = False
    filter_min_connections: Optional[int] = None
    filter_exclude_open_to_work: bool = False


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    connection_message_template: Optional[str] = None
    followup_message_template: Optional[str] = None
    followup_delay_hours: Optional[int] = None
    followup_enabled: Optional[bool] = None
    followup_message_1: Optional[str] = None
    followup_message_2: Optional[str] = None
    followup_message_3: Optional[str] = None
    weekend_enabled: Optional[bool] = None
    filter_no_photo: Optional[bool] = None
    filter_min_connections: Optional[int] = None
    filter_exclude_open_to_work: Optional[bool] = None


# ── Broadcasts (message-only campaigns) ───────────────────────────────────

class BroadcastOut(BaseModel):
    id: str
    account_id: str
    account_name: Optional[str] = None
    source_list_id: Optional[str] = None
    source_list_name: Optional[str] = None
    name: str
    status: str
    message_1: Optional[str] = None
    message_2: Optional[str] = None
    message_3: Optional[str] = None
    # 0 = no delay (5 s in backend); positive float = hours (fractional → minutes).
    delay_between_hours: float
    weekend_enabled: bool = False
    # "skip" = existing behaviour; "branch" = route on conversation history.
    conversation_routing: str = "skip"
    message_prior_only: Optional[str] = None
    message_prior_only_2: Optional[str] = None
    message_prior_only_3: Optional[str] = None
    total_leads: int
    archived: bool = False
    paused_at: Optional[datetime] = None
    # Bucket counts across broadcast_leads (pending / sent / sequence_complete /
    # skipped / manual_outreach / error)
    status_counts: Optional[Dict[str, int]] = None
    # Sum of successful action_log DIRECT_MESSAGE rows for this broadcast; distinct
    # from total_leads because a lead can receive 1–3 messages.
    messages_sent: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BroadcastCreate(BaseModel):
    """Create a broadcast.

    Exactly one of ``source_list_id``, ``source_list_ids``, or ``lead_ids`` must
    be provided:
    - ``source_list_id`` snapshots every lead currently in a single list.
    - ``source_list_ids`` snapshots the union of leads across several lists
      (deduplicated).
    - ``lead_ids`` snapshots the given lead IDs directly (bulk-selection path).
    """
    account_id: str
    name: str
    message_1: str
    message_2: Optional[str] = None
    message_3: Optional[str] = None
    # 0 = no delay (5 s in backend); positive float = hours (fractional for minutes).
    delay_between_hours: float = 24
    weekend_enabled: bool = False
    # Conversation routing: "skip" (default) or "branch".
    conversation_routing: str = "skip"
    # Messages sent in branch mode when a prior outgoing-only conversation exists.
    message_prior_only: Optional[str] = None
    message_prior_only_2: Optional[str] = None
    message_prior_only_3: Optional[str] = None
    source_list_id: Optional[str] = None
    source_list_ids: Optional[List[str]] = None
    lead_ids: Optional[List[str]] = None


class BroadcastUpdate(BaseModel):
    name: Optional[str] = None
    message_1: Optional[str] = None
    message_2: Optional[str] = None
    message_3: Optional[str] = None
    delay_between_hours: Optional[float] = None
    weekend_enabled: Optional[bool] = None
    conversation_routing: Optional[str] = None
    message_prior_only: Optional[str] = None
    message_prior_only_2: Optional[str] = None
    message_prior_only_3: Optional[str] = None


class BroadcastAddLeadsRequest(BaseModel):
    lead_ids: List[str]


class BroadcastLeadOut(BaseModel):
    id: str
    broadcast_id: str
    lead_id: str
    status: str
    scheduled_at: Optional[datetime] = None
    last_message_sent_at: Optional[datetime] = None
    last_message_index: Optional[int] = None
    next_message_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int
    skipped_reason: Optional[str] = None
    # Enriched from the joined Lead row for the detail table UI.
    lead_name: Optional[str] = None
    lead_company: Optional[str] = None
    lead_linkedin_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class BroadcastLeadsPage(BaseModel):
    items: List[BroadcastLeadOut]
    total: int
    limit: int
    offset: int


# ── Leads ─────────────────────────────────────────────────────────────────

class LeadListRef(BaseModel):
    """Slim list membership record — used in lead detail responses."""
    id: str
    name: str
    added_at: datetime


class CampaignRef(BaseModel):
    """Slim campaign membership record — used in lead detail responses."""
    id: str
    name: str
    status: str
    account_name: Optional[str] = None


class LeadOut(BaseModel):
    id: str
    campaign_id: Optional[str] = None
    lead_list_id: Optional[str] = None
    linkedin_url: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    twitter_url: Optional[str] = None
    telegram_username: Optional[str] = None
    telegram_alternatives: Optional[List[str]] = None
    tg_contacted_at: Optional[datetime] = None
    notes: Optional[str] = None
    location: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None
    status: str
    connection_requested_at: Optional[datetime] = None
    connection_accepted_at: Optional[datetime] = None
    followup_sent_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int
    scheduled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    campaign_name: Optional[str] = None
    lead_list_name: Optional[str] = None
    # Membership history is detail-only; the library includes campaign summaries.
    lead_lists: List[LeadListRef] = []
    campaigns: List[CampaignRef] = []

    class Config:
        from_attributes = True


class LeadUpdateRequest(BaseModel):
    """Editable profile fields for PATCH /leads/{lead_id}."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    telegram_username: Optional[str] = None
    # True = mark contacted now, False = clear the contacted timestamp
    tg_contacted: Optional[bool] = None
    notes: Optional[str] = None
    location: Optional[str] = None


class FindTelegramTaskOut(BaseModel):
    task_id: str
    status: str  # "running" | "done" | "error"
    telegram_username: Optional[str] = None
    telegram_alternatives: List[str] = []
    logs: List[str] = []
    error: Optional[str] = None


class LeadActivityOut(BaseModel):
    id: str
    action_type: str
    status: str
    details: Optional[Dict[str, Any]] = None
    created_at: datetime
    account_name: Optional[str] = None
    actor_name: Optional[str] = None  # dashboard user who performed a manual action
    source: str = "action_log"  # "action_log" | "lead_event"

    class Config:
        from_attributes = True


class LeadNoteOut(BaseModel):
    id: str
    lead_id: str
    body: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LeadNoteCreate(BaseModel):
    body: str


class LeadNoteUpdate(BaseModel):
    body: str


class LeadPage(BaseModel):
    items: List[LeadOut]
    total: int
    page: int
    per_page: int


class ImportResponse(BaseModel):
    total_rows: int
    imported: int
    duplicates_skipped: int
    no_url_skipped: int
    no_url_imported: int = 0
    errors: List[str]


class BulkLeadRequest(BaseModel):
    lead_ids: List[str]


class BulkLeadResponse(BaseModel):
    updated: int


# ── Lead Lists ────────────────────────────────────────────────────────

class LeadListOut(BaseModel):
    id: str
    name: str
    csv_filename: Optional[str] = None
    total_leads: int
    campaign_count: int = 0
    archived: bool = False
    tg_enrich_enabled: bool = True
    source_url: Optional[str] = None
    scrape_account_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LeadListCreate(BaseModel):
    name: str
    tg_enrich_enabled: bool = True


class LeadListUpdate(BaseModel):
    name: Optional[str] = None
    tg_enrich_enabled: Optional[bool] = None


class LeadListStats(BaseModel):
    total: int
    acceptance_rate: float   # 0-100 %
    tg_coverage: float       # 0-100 %
    email_coverage: float    # 0-100 %
    twitter_coverage: float  # 0-100 %
    tg_contacted_rate: float # 0-100 % of leads with TG that have been contacted


class LeadListDetail(LeadListOut):
    campaigns: List[Dict[str, str]] = []  # [{id, name}]
    stats: Optional[LeadListStats] = None


class CampaignLeadListOut(BaseModel):
    id: str
    campaign_id: str
    lead_list_id: str
    created_at: datetime

    class Config:
        from_attributes = True


class AssignListRequest(BaseModel):
    campaign_id: str


class ReorderListsRequest(BaseModel):
    # Lead-list ids, highest dispatch priority first.
    ordered_list_ids: List[str]


# ── Action Log ────────────────────────────────────────────────────────────

class ActionLogOut(BaseModel):
    id: str
    account_id: str
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    action_type: str
    status: str
    details: Optional[Dict[str, Any]] = None
    created_at: datetime
    # Enriched lead fields (populated by the activity endpoint)
    lead_first_name: Optional[str] = None
    lead_last_name: Optional[str] = None
    lead_url: Optional[str] = None

    class Config:
        from_attributes = True


# ── Daily Stats ───────────────────────────────────────────────────────────

class DailyStatOut(BaseModel):
    date: date
    connection_requests_sent: int
    followup_messages_sent: int
    connections_accepted: int
    errors: int

    class Config:
        from_attributes = True


# ── Campaign Stats ────────────────────────────────────────────────────────

class CampaignStatsDaily(BaseModel):
    date: str
    sent: int
    accepted: int
    errors: int


class CampaignStatsSummary(BaseModel):
    total_sent: int
    total_accepted: int
    acceptance_rate: float
    avg_time_to_accept_hours: Optional[float] = None


class CampaignStatsResponse(BaseModel):
    daily: List[CampaignStatsDaily]
    summary: CampaignStatsSummary


# ── Schedule ─────────────────────────────────────────────────────────────

class ScheduleSlotOut(BaseModel):
    lead_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    linkedin_url: Optional[str] = None
    campaign_name: str
    scheduled_at: Optional[str] = None
    status: str  # "scheduled" or "sent"


# ── Account Health ───────────────────────────────────────────────────────

class AccountHealthOut(BaseModel):
    last_action_at: Optional[datetime] = None
    days_since_last_activity: Optional[int] = None
    error_rate_7d: float = 0.0
    total_actions_7d: int = 0
    errors_7d: int = 0
    last_error_message: Optional[str] = None


# ── Apollo Enrichment ────────────────────────────────────────────────────

class EnrichPhoneResponse(BaseModel):
    phone: Optional[str] = None
    found: bool


# ── Telegram Resolver ────────────────────────────────────────────────────

class TelegramResolveRequest(BaseModel):
    name: str
    company: Optional[str] = None
    twitter_url: Optional[str] = None
    linkedin_url: Optional[str] = None             # custom vanity slug checked at Twitter-level priority
    exclude_usernames: Optional[List[str]] = None  # handles already verified as wrong
    max_seconds: Optional[int] = None              # server-side timeout cap; default 50s
    max_candidates: Optional[int] = None           # Pass 2 candidate cap; None = no cap


class TelegramResolveResponse(BaseModel):
    best_match: Optional[str] = None
    alternatives: List[str] = []
    logs: List[str] = []
    timed_out: bool = False
    flood_wait_seconds: Optional[int] = None  # Telegram rate limit; caller must sleep + retry


# ── Telegram Batch Resolver ───────────────────────────────────────────────

class TelegramResolveBatchRequest(BaseModel):
    people: List[TelegramResolveRequest]


class TelegramResolveBatchResult(BaseModel):
    name: str
    best_match: Optional[str] = None
    alternatives: List[str] = []
    logs: List[str] = []
    timed_out: bool = False
    flood_wait_seconds: Optional[int] = None


class TelegramResolveBatchStatus(BaseModel):
    task_id: str
    status: str          # "running" | "done" | "error"
    total: int
    completed: int
    results: List[TelegramResolveBatchResult] = []
    error: Optional[str] = None


# ── Campaign Clone ────────────────────────────────────────────────────────

class CloneCampaignRequest(BaseModel):
    name: str
    account_id: str


# ── Global Activity Feed ──────────────────────────────────────────────────

class ActivityItem(BaseModel):
    id: str
    source: str                      # "action_log" | "lead_event" | "lead_note"
    event_type: str
    created_at: datetime
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    lead_id: Optional[str] = None
    lead_name: Optional[str] = None
    status: Optional[str] = None    # action_log only
    details: Optional[Dict] = None
    actor_user_id: Optional[str] = None   # dashboard user who did it (manual actions)
    actor_name: Optional[str] = None      # resolved display name, e.g. "Ibrahim"


class ActivityPage(BaseModel):
    items: List[ActivityItem]
    total: int
    page: int
    per_page: int
    pages: int


# ── Users / Auth ──────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: str
    handle: str
    display_name: Optional[str] = None
    is_superadmin: bool
    is_active: bool
    created_at: datetime
    last_seen_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    handle: str
    password: str


class LoginResponse(BaseModel):
    user: UserOut


# ── Health ────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
