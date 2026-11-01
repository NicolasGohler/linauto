"""Data access layer — CRUD operations for all models."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import case, select, func, update, or_, not_, exists, and_, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from releasi.campaign.importer import normalize_linkedin_url
from releasi.db.models import (
    Account, AccountStatus,
    Campaign, CampaignStatus,
    Lead, LeadStatus,
    ActionLog, ActionType, ActionLogStatus,
    DailyStat,
    LeadList, CampaignLeadList,
    LeadListMembership, CampaignLeadAssignment,
    LeadEvent, LeadNote,
    ScraperCookie,
    SessionEvent,
    User,
    Broadcast, BroadcastLead, BroadcastStatus,
)


def _lead_search_pattern(search: str) -> str:
    query = search.strip()
    query = normalize_linkedin_url(query) or query
    return f"%{query.rstrip('/')}%"


def _visible_campaign_ids():
    return select(Campaign.id).join(Account).where(
        Campaign.archived == False, Account.archived == False,
    )


def _visible_lead_condition():
    return exists(select(LeadListMembership.lead_id).where(
        LeadListMembership.lead_id == Lead.id,
        LeadListMembership.lead_list_id.in_(
            select(LeadList.id).where(LeadList.archived == False)
        ),
    ))


def _visible_lead_ids():
    return select(Lead.id).where(_visible_lead_condition())


def _last_activity_subquery():
    """Per-lead most-recent-activity timestamp across events, notes, and action log.

    Built as a UNION of individual timestamped rows (rather than a
    multi-argument MAX across three columns) so NULLs on any one side don't
    contaminate the result — SQLite's scalar max(a, b, c) returns NULL if
    any argument is NULL, but the aggregate MAX() used here over unioned
    rows correctly ignores leads with no rows in a given table.
    """
    events_ts = select(LeadEvent.lead_id.label("lead_id"), LeadEvent.created_at.label("ts"))
    notes_ts = select(LeadNote.lead_id.label("lead_id"), LeadNote.created_at.label("ts"))
    actionlog_ts = (
        select(ActionLog.lead_id.label("lead_id"), ActionLog.created_at.label("ts"))
        .where(ActionLog.lead_id.isnot(None))
    )
    union_sq = union_all(events_ts, notes_ts, actionlog_ts).subquery()
    return (
        select(
            union_sq.c.lead_id.label("lead_id"),
            func.max(union_sq.c.ts).label("last_activity_at"),
        )
        .group_by(union_sq.c.lead_id)
        .subquery()
    )


# Phase 1 of the lead-centric refactor: every Lead mutation also writes
# to the new tables. Flip this off to disable dual-write (for emergency
# rollback or running on a DB that doesn't have the new tables yet).
DUAL_WRITE_NEW_SCHEMA = True

# Status fields on Lead that also live on CampaignLeadAssignment. When a
# Lead row is updated with any of these, the matching assignment row gets
# the same value.
_ASSIGNMENT_SYNC_FIELDS = (
    "status",
    "scheduled_at",
    "connection_requested_at",
    "connection_accepted_at",
    "followup_sent_at",
    "error_message",
    "retry_count",
)


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Phase 1 dual-write helpers ─────────────────────────────────────────
    # These mirror Lead state to the new tables. Called from every Lead
    # mutation site. Safe no-ops when:
    #   - DUAL_WRITE_NEW_SCHEMA is False (rollback switch)
    #   - The Lead has no campaign_id / lead_list_id (no assignment/
    #     membership to sync)
    #   - The matching new-table row doesn't exist yet (we don't auto-create
    #     here — that's the backfill script's job for legacy rows, and the
    #     explicit creation sites' job for new rows)

    async def _sync_assignment_from_lead(self, lead: Lead, campaign_id: Optional[str] = None) -> None:
        """Mirror a Lead's per-campaign state to its CampaignLeadAssignment.

        UPSERT-ish: if the assignment row exists, UPDATE its sync fields.
        If it doesn't exist (e.g. brand-new Lead created in this transaction),
        INSERT a fresh one mirroring the Lead's full state.

        After Phase 3a row-collapse the canonical lead has campaign_id=None, so
        callers that know the campaign (dispatcher, acceptance checker, executor)
        must pass campaign_id explicitly via update_lead(campaign_id_override=...).
        """
        effective_campaign_id = campaign_id or lead.campaign_id
        if not DUAL_WRITE_NEW_SCHEMA or effective_campaign_id is None:
            return

        existing = await self.session.execute(
            select(CampaignLeadAssignment).where(
                CampaignLeadAssignment.lead_id == lead.id,
                CampaignLeadAssignment.campaign_id == effective_campaign_id,
            )
        )
        assignment = existing.scalar_one_or_none()

        if assignment is None:
            assignment = CampaignLeadAssignment(
                lead_id=lead.id,
                campaign_id=effective_campaign_id,
                lead_list_id=lead.lead_list_id,
                status=lead.status.value if hasattr(lead.status, "value") else str(lead.status).lower(),
                scheduled_at=lead.scheduled_at,
                connection_requested_at=lead.connection_requested_at,
                connection_accepted_at=lead.connection_accepted_at,
                followup_sent_at=lead.followup_sent_at,
                error_message=lead.error_message,
                retry_count=lead.retry_count or 0,
            )
            self.session.add(assignment)
        else:
            assignment.status = (
                lead.status.value if hasattr(lead.status, "value") else str(lead.status).lower()
            )
            assignment.scheduled_at = lead.scheduled_at
            assignment.connection_requested_at = lead.connection_requested_at
            assignment.connection_accepted_at = lead.connection_accepted_at
            assignment.followup_sent_at = lead.followup_sent_at
            assignment.error_message = lead.error_message
            assignment.retry_count = lead.retry_count or 0
            # Update lead_list_id if it changed on the Lead (rare)
            if lead.lead_list_id and assignment.lead_list_id != lead.lead_list_id:
                assignment.lead_list_id = lead.lead_list_id

    async def _sync_membership_from_lead(self, lead: Lead) -> None:
        """Ensure a LeadListMembership row exists for the Lead's list.

        Membership rows are write-once: the lead either is or isn't in the
        list. Idempotent — re-runs are no-ops.
        """
        if not DUAL_WRITE_NEW_SCHEMA or lead.lead_list_id is None:
            return

        existing = await self.session.execute(
            select(LeadListMembership.id).where(
                LeadListMembership.lead_id == lead.id,
                LeadListMembership.lead_list_id == lead.lead_list_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return

        membership = LeadListMembership(
            lead_id=lead.id,
            lead_list_id=lead.lead_list_id,
        )
        self.session.add(membership)

    # ── Accounts ───────────────────────────────────────────────────────────

    async def create_account(self, name: str, li_at_cookie: str, **kwargs) -> Account:
        account = Account(name=name, li_at_cookie=li_at_cookie, **kwargs)
        self.session.add(account)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def get_account_by_name(self, name: str) -> Account | None:
        result = await self.session.execute(
            select(Account).where(Account.name == name)
        )
        return result.scalar_one_or_none()

    async def get_account(self, account_id: str) -> Account | None:
        return await self.session.get(Account, account_id)

    async def list_accounts(self, include_archived: bool = False) -> Sequence[Account]:
        stmt = select(Account).order_by(Account.name)
        if not include_archived:
            stmt = stmt.where(Account.archived == False)  # noqa: E712
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_account(self, account: Account, **kwargs) -> Account:
        for key, value in kwargs.items():
            setattr(account, key, value)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    # ── Session-health ledger ────────────────────────────────────────────────

    async def log_session_event(
        self,
        account_id: str,
        job: str,
        result: str,
        **fields,
    ) -> None:
        """Append a session-health ledger row. Best-effort, never raises.

        `fields` may include any SessionEvent column: egress_ip, geo, asn,
        li_at_fp, li_at_expires_at, has_li_rm, cookie_names, user_agent,
        timezone, viewport, consecutive_session_errors, detail.
        """
        try:
            evt = SessionEvent(
                account_id=account_id,
                job=job,
                result=result,
                **fields,
            )
            self.session.add(evt)
            await self.session.commit()
        except Exception:
            # Monitoring must never break the job it observes.
            try:
                await self.session.rollback()
            except Exception:
                pass

    async def list_session_events(
        self,
        account_id: Optional[str] = None,
        limit: int = 200,
    ) -> Sequence[SessionEvent]:
        stmt = select(SessionEvent).order_by(SessionEvent.created_at.desc()).limit(limit)
        if account_id:
            stmt = stmt.where(SessionEvent.account_id == account_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    # ── Campaigns ──────────────────────────────────────────────────────────

    async def create_campaign(self, account_id: str, name: str, **kwargs) -> Campaign:
        campaign = Campaign(account_id=account_id, name=name, **kwargs)
        self.session.add(campaign)
        await self.session.commit()
        await self.session.refresh(campaign)
        return campaign

    async def get_campaign_by_name(self, name: str) -> Campaign | None:
        result = await self.session.execute(
            select(Campaign).where(Campaign.name == name)
        )
        return result.scalar_one_or_none()

    async def get_campaign(self, campaign_id: str) -> Campaign | None:
        return await self.session.get(Campaign, campaign_id)

    async def list_campaigns(self, account_id: str | None = None, include_archived: bool = False) -> Sequence[Campaign]:
        stmt = select(Campaign).order_by(Campaign.created_at.desc())
        if account_id:
            stmt = stmt.where(Campaign.account_id == account_id)
        if not include_archived:
            stmt = stmt.where(Campaign.id.in_(_visible_campaign_ids()))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_campaigns_due_for_resume(self, now: datetime) -> Sequence[Campaign]:
        from releasi.db.models import CampaignStatus
        stmt = select(Campaign).where(
            Campaign.status == CampaignStatus.PAUSED,
            Campaign.resume_at.isnot(None),
            Campaign.resume_at <= now,
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_campaign(self, campaign: Campaign, **kwargs) -> Campaign:
        for key, value in kwargs.items():
            setattr(campaign, key, value)
        await self.session.commit()
        await self.session.refresh(campaign)
        return campaign

    # ── Leads ──────────────────────────────────────────────────────────────

    async def bulk_create_leads(self, leads: list[Lead]) -> int:
        """Create Lead rows and their CampaignLeadAssignment / LeadListMembership records.

        Phase 3b: deduplicates by linkedin_url against existing canonical leads
        so importing the same URL into a second campaign reuses the existing Lead
        row rather than creating a duplicate (which would undo Phase 3a).

        For URLs that already have a canonical lead:
          - Creates a CampaignLeadAssignment (if not already present)
          - Creates a LeadListMembership (if not already present)
          - Updates the canonical lead's profile fields if the incoming data is newer
          - Does NOT create a new Lead row

        For genuinely new URLs:
          - Creates the Lead row with campaign_id=None (canonical from the start)
          - Then creates CampaignLeadAssignment + LeadListMembership
        """
        if not leads:
            return 0

        # Split into URL-keyed leads (deduped by linkedin_url) and no-URL leads
        url_to_incoming: dict[str, Lead] = {}
        no_url_leads: list[Lead] = []
        for lead in leads:
            if lead.linkedin_url:
                url_to_incoming[lead.linkedin_url] = lead  # last one wins for dupes in same CSV
            else:
                no_url_leads.append(lead)

        # Look up any existing canonical leads for URL-keyed leads
        existing_by_url: dict[str, Lead] = {}
        if url_to_incoming:
            urls = list(url_to_incoming.keys())
            existing_result = await self.session.execute(
                select(Lead).where(Lead.linkedin_url.in_(urls))
            )
            existing_by_url = {
                l.linkedin_url: l for l in existing_result.scalars().all()
            }

        assignments_added = 0
        memberships_added = 0

        async def _attach_lead(canonical: Lead, incoming: Lead) -> None:
            nonlocal assignments_added, memberships_added
            if incoming.campaign_id:
                existing_asgn = await self.session.execute(
                    select(CampaignLeadAssignment).where(
                        CampaignLeadAssignment.lead_id == canonical.id,
                        CampaignLeadAssignment.campaign_id == incoming.campaign_id,
                    )
                )
                if existing_asgn.scalar_one_or_none() is None:
                    initial_status = (
                        LeadStatus.INVALID.value
                        if not canonical.linkedin_url
                        else LeadStatus.PENDING.value
                    )
                    self.session.add(CampaignLeadAssignment(
                        lead_id=canonical.id,
                        campaign_id=incoming.campaign_id,
                        lead_list_id=incoming.lead_list_id,
                        status=initial_status,
                    ))
                    assignments_added += 1
            if incoming.lead_list_id:
                existing_mem = await self.session.execute(
                    select(LeadListMembership).where(
                        LeadListMembership.lead_id == canonical.id,
                        LeadListMembership.lead_list_id == incoming.lead_list_id,
                    )
                )
                if existing_mem.scalar_one_or_none() is None:
                    self.session.add(LeadListMembership(
                        lead_id=canonical.id,
                        lead_list_id=incoming.lead_list_id,
                    ))
                    memberships_added += 1

        # ── URL-keyed leads (dedup by linkedin_url) ─────────────────────────
        for url, incoming in url_to_incoming.items():
            canonical = existing_by_url.get(url)

            if canonical is None:
                # New URL — create canonical Lead row (campaign_id stays NULL)
                new_lead = Lead(
                    linkedin_url=incoming.linkedin_url,
                    lead_list_id=incoming.lead_list_id,
                    first_name=incoming.first_name,
                    last_name=incoming.last_name,
                    company=incoming.company,
                    title=incoming.title,
                    email=getattr(incoming, "email", None),
                    phone=getattr(incoming, "phone", None),
                    twitter_url=getattr(incoming, "twitter_url", None),
                    telegram_username=getattr(incoming, "telegram_username", None),
                    location=getattr(incoming, "location", None),
                    apollo_person_id=getattr(incoming, "apollo_person_id", None),
                    extra_data=incoming.extra_data,
                    # campaign_id intentionally omitted — canonical leads are URL-scoped
                )
                self.session.add(new_lead)
                await self.session.flush()  # populate new_lead.id
                canonical = new_lead

            await _attach_lead(canonical, incoming)

        # ── No-URL leads (no global dedup — each row becomes its own lead) ──
        for incoming in no_url_leads:
            new_lead = Lead(
                linkedin_url=None,
                lead_list_id=incoming.lead_list_id,
                first_name=incoming.first_name,
                last_name=incoming.last_name,
                company=incoming.company,
                title=incoming.title,
                email=getattr(incoming, "email", None),
                phone=getattr(incoming, "phone", None),
                twitter_url=getattr(incoming, "twitter_url", None),
                telegram_username=getattr(incoming, "telegram_username", None),
                location=getattr(incoming, "location", None),
                apollo_person_id=getattr(incoming, "apollo_person_id", None),
                extra_data=incoming.extra_data,
            )
            self.session.add(new_lead)
            await self.session.flush()
            await _attach_lead(new_lead, incoming)

        await self.session.commit()
        # For campaign imports, return assignments_added (how many were added to the campaign).
        # For list-only imports (no campaign_id), return memberships_added so that
        # update_lead_list gets the correct count — assignments_added is always 0 in that case.
        return assignments_added if assignments_added else memberships_added

    async def get_leads_by_status(
        self, campaign_id: str, status: LeadStatus
    ) -> Sequence[Lead]:
        """Phase 3b: delegates to the via_assignments read path."""
        return await self.get_leads_by_status_via_assignments(campaign_id, status)

    async def get_leads_by_status_via_assignments(
        self, campaign_id: str, status: LeadStatus
    ) -> Sequence[Lead]:
        """Phase 2 read cutover mirror of `get_leads_by_status`.

        Filters via CampaignLeadAssignment (the source of truth in the new
        schema) but returns Lead ORM instances so callers that mutate the
        Lead via `update_lead()` still work without changes. Since dual-
        write keeps both tables in sync, the resulting Lead set is
        identical to the legacy query for the same (campaign_id, status).

        The status comparison uses the lowercase enum value because
        CampaignLeadAssignment.status is stored as a plain string.
        """
        status_str = status.value if hasattr(status, "value") else str(status).lower()
        result = await self.session.execute(
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == status_str,
            )
            .order_by(Lead.created_at)
        )
        return result.scalars().all()

    async def get_scheduled_leads(
        self, campaign_id: str, before: datetime
    ) -> Sequence[Lead]:
        """Get leads scheduled to execute before the given time.

        Phase 3b: reads from CampaignLeadAssignment.
        """
        result = await self.session.execute(
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == LeadStatus.SCHEDULED.value,
                CampaignLeadAssignment.scheduled_at <= before,
                Lead.linkedin_url.isnot(None),
                _visible_lead_condition(),
            )
            .order_by(CampaignLeadAssignment.scheduled_at)
        )
        return result.scalars().all()

    async def count_future_scheduled_leads(self, campaign_id: str) -> int:
        """Count SCHEDULED assignments whose scheduled_at is still in the future.

        Phase 3b: reads from CampaignLeadAssignment.
        """
        now = datetime.utcnow()
        result = await self.session.execute(
            select(func.count()).where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == LeadStatus.SCHEDULED.value,
                CampaignLeadAssignment.scheduled_at > now,
                CampaignLeadAssignment.lead_id.in_(_visible_lead_ids()),
            )
        )
        return result.scalar_one()

    async def plan_generated_today(self, campaign_id: str, local_date: "date") -> bool:
        """Return True if a DAILY_PLAN_GENERATED entry exists for this campaign today.

        Used by the planner to distinguish a real daily plan from a single
        dispatcher backfill lead — both result in future_scheduled > 0, but
        only the former means planning is done for the day.
        """
        day_str = local_date.isoformat()  # stored in details JSON as "date": "YYYY-MM-DD"
        result = await self.session.execute(
            select(func.count()).where(
                ActionLog.campaign_id == campaign_id,
                ActionLog.action_type == ActionType.DAILY_PLAN_GENERATED,
                ActionLog.status == ActionLogStatus.SUCCESS,
                # created_at covers any UTC time that day; the details.date field
                # is the account's local date, which is the authoritative anchor.
                ActionLog.details.contains(day_str),
            )
        )
        return (result.scalar_one() or 0) > 0

    async def reset_stale_scheduled_leads(self, campaign_id: str) -> int:
        """Reset SCHEDULED assignments with a past scheduled_at back to PENDING.

        Called at the start of each planning sweep so that leads from a
        missed/skipped window (container restart, previous day) re-enter
        the pending pool and are cleanly re-planned rather than silently
        accumulating as a backlog of overdue SCHEDULED rows.
        Returns the number of assignments reset.

        Phase 3b: writes only to CampaignLeadAssignment (canonical source).
        """
        now = datetime.utcnow()
        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == LeadStatus.SCHEDULED.value,
                CampaignLeadAssignment.scheduled_at < now,
                CampaignLeadAssignment.lead_id.in_(_visible_lead_ids()),
            )
            .values(status=LeadStatus.PENDING.value, scheduled_at=None)
        )
        await self.session.commit()
        return result.rowcount

    async def get_latest_future_scheduled_at(self, campaign_id: str) -> Optional[datetime]:
        """Return the latest scheduled_at among SCHEDULED assignments still in the future.

        Phase 3b: reads from CampaignLeadAssignment.
        """
        now = datetime.utcnow()
        result = await self.session.execute(
            select(func.max(CampaignLeadAssignment.scheduled_at)).where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == LeadStatus.SCHEDULED.value,
                CampaignLeadAssignment.scheduled_at > now,
                CampaignLeadAssignment.lead_id.in_(_visible_lead_ids()),
            )
        )
        return result.scalar_one_or_none()

    async def get_followup_due_leads(
        self, campaign_id: str, before: datetime
    ) -> Sequence[Lead]:
        """Get leads with follow-ups scheduled before the given time.

        Phase 3b: delegates to the via_assignments read path.
        """
        return await self.get_followup_due_leads_via_assignments(campaign_id, before)

    async def get_followup_due_leads_via_assignments(
        self, campaign_id: str, before: datetime
    ) -> Sequence[Lead]:
        """Phase 2 read cutover mirror of `get_followup_due_leads`.

        Filters via CampaignLeadAssignment instead of Lead.campaign_id /
        Lead.status, but returns Lead ORM instances so all mutation sites
        (update_lead, etc.) work without changes.
        """
        status_str = LeadStatus.FOLLOWUP_SCHEDULED.value
        result = await self.session.execute(
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == status_str,
                CampaignLeadAssignment.scheduled_at <= before,
                _visible_lead_condition(),
            )
            .order_by(CampaignLeadAssignment.scheduled_at)
        )
        return result.scalars().all()

    async def get_pending_leads_via_assignments(
        self, campaign_id: str, limit: Optional[int] = None
    ) -> Sequence[Lead]:
        """Phase 2 read cutover mirror of `get_pending_leads`.

        Filters via CampaignLeadAssignment. Only used by daily_planning_sweep
        (planned-mode accounts), which is currently a no-op in prod (all
        accounts are continuous). Included here so Phase 3 can drop the
        legacy path uniformly.
        """
        status_str = LeadStatus.PENDING.value
        # Order by list priority first (higher = dispatched first), then by
        # lead creation time within a list. The outer join lets assignments
        # with no matching CampaignLeadList link (NULL lead_list_id, or a list
        # that was unlinked) fall back to priority 0 alongside the default.
        priority_col = func.coalesce(CampaignLeadList.priority, 0)
        stmt = (
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .outerjoin(
                CampaignLeadList,
                and_(
                    CampaignLeadList.campaign_id == CampaignLeadAssignment.campaign_id,
                    CampaignLeadList.lead_list_id
                    == CampaignLeadAssignment.lead_list_id,
                ),
            )
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == status_str,
                Lead.linkedin_url.isnot(None),
                _visible_lead_condition(),
                or_(CampaignLeadAssignment.scheduled_at.is_(None),
                    CampaignLeadAssignment.scheduled_at <= datetime.utcnow()),
            )
            .order_by(priority_col.desc(), Lead.created_at)
        )
        if limit:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def defer_connection_attempt(self, campaign_id: str, lead_id: str,
                                       reason: str, delay_seconds: int,
                                       max_attempts: int = 3) -> dict:
        """Persist retry eligibility on this assignment, never another campaign.

        scheduled_at is an eligibility timestamp for both pending and scheduled
        assignments. Exhausted attempts require manual review instead of looping.
        """
        row = (await self.session.execute(select(CampaignLeadAssignment).where(
            CampaignLeadAssignment.campaign_id == campaign_id,
            CampaignLeadAssignment.lead_id == lead_id,
        ))).scalar_one()
        if row.status not in (LeadStatus.PENDING.value, LeadStatus.SCHEDULED.value):
            return {"attempt": row.retry_count, "next_attempt_at": None, "exhausted": False}
        now = datetime.utcnow()
        row.retry_count = (row.retry_count or 0) + 1
        exhausted = row.retry_count >= max_attempts
        cap = 86400 if delay_seconds >= 86400 else 3600
        delay = min(delay_seconds * (2 ** (row.retry_count - 1)), cap)
        row.scheduled_at = None if exhausted else now + timedelta(seconds=delay)
        row.error_message = reason[:500]
        row.updated_at = now
        if exhausted:
            row.status = LeadStatus.ERROR.value
        await self.session.commit()
        return {"attempt": row.retry_count,
                "next_attempt_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
                "exhausted": exhausted}

    async def get_stranded_followup_leads(self, campaign_id: str) -> Sequence[Lead]:
        """Get CONNECTED leads that never got a followup scheduled or sent.

        These are leads that the acceptance checker marked CONNECTED but whose
        followup scheduling was missed (e.g. followup was disabled at accept
        time, a scheduler restart interrupted the step, etc.).  They are
        "stranded" because the acceptance checker only schedules *newly*
        connected leads, so they would sit in CONNECTED forever without this
        rescue query.
        """
        result = await self.session.execute(
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == LeadStatus.CONNECTED.value,
                CampaignLeadAssignment.followup_sent_at.is_(None),
                _visible_lead_condition(),
            )
        )
        return result.scalars().all()

    async def get_pending_leads(
        self, campaign_id: str, limit: int | None = None
    ) -> Sequence[Lead]:
        """Phase 3b: delegates to the via_assignments read path."""
        return await self.get_pending_leads_via_assignments(campaign_id, limit=limit)

    async def is_lead_visible(self, lead_id: str) -> bool:
        return bool((await self.session.execute(
            select(Lead.id).where(Lead.id == lead_id, _visible_lead_condition())
        )).scalar_one_or_none())

    async def lead_exists_in_campaign(
        self, campaign_id: str, linkedin_url: str
    ) -> bool:
        """Check if a lead URL already has an assignment in the given campaign.

        Phase 3b: checks CampaignLeadAssignment joined to Lead instead of
        the stale Lead.campaign_id column (which is NULL on all canonicals).
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(CampaignLeadAssignment)
            .join(Lead, Lead.id == CampaignLeadAssignment.lead_id)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                Lead.linkedin_url == linkedin_url,
            )
        )
        return result.scalar_one() > 0

    async def update_lead(
        self,
        lead: Lead,
        campaign_id_override: Optional[str] = None,
        **kwargs,
    ) -> Lead:
        """Update a lead's fields and sync to CampaignLeadAssignment.

        After Phase 3a row-collapse the canonical lead has campaign_id=None.
        Callers that know the campaign context (dispatcher, acceptance checker,
        executor) MUST pass campaign_id_override=campaign.id so the assignment
        sync does not silently skip.
        """
        for key, value in kwargs.items():
            setattr(lead, key, value)
        # Dual-write: mirror state changes to the assignment row and ensure
        # membership exists. Both are no-ops if no campaign can be resolved
        # or DUAL_WRITE_NEW_SCHEMA is False.
        await self._sync_assignment_from_lead(lead, campaign_id=campaign_id_override)
        await self._sync_membership_from_lead(lead)
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def get_campaign_status_counts(self, campaign_id: str, include_archived: bool = False) -> dict[str, int]:
        """Get lead counts grouped by status for a campaign (excludes REMOVED leads).

        Phase 3b: reads from CampaignLeadAssignment — the canonical source of
        per-campaign state after Phase 3a row collapse.
        """
        result = await self.session.execute(
            select(CampaignLeadAssignment.status, func.count())
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status != "removed",
                True if include_archived else CampaignLeadAssignment.lead_id.in_(_visible_lead_ids()),
            )
            .group_by(CampaignLeadAssignment.status)
        )
        return {row[0]: row[1] for row in result.all()}

    async def get_list_status_counts_for_campaign(
        self, lead_list_id: str, campaign_id: str
    ) -> dict[str, int]:
        """Get lead counts grouped by status for a specific list within a campaign.

        Phase 3b: reads from CampaignLeadAssignment.
        """
        result = await self.session.execute(
            select(CampaignLeadAssignment.status, func.count())
            .where(
                CampaignLeadAssignment.lead_list_id == lead_list_id,
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status != "removed",
            )
            .group_by(CampaignLeadAssignment.status)
        )
        return {row[0]: row[1] for row in result.all()}

    async def get_leads_by_ids(self, lead_ids: list) -> dict:
        """Batch-fetch leads by id. Returns {lead_id: Lead}."""
        if not lead_ids:
            return {}
        result = await self.session.execute(
            select(Lead).where(Lead.id.in_(lead_ids))
        )
        return {l.id: l for l in result.scalars().all()}

    # ── Action Log ─────────────────────────────────────────────────────────

    async def log_action(
        self,
        account_id: str,
        action_type: ActionType,
        status: ActionLogStatus,
        campaign_id: str | None = None,
        lead_id: str | None = None,
        details: dict | None = None,
    ) -> ActionLog:
        entry = ActionLog(
            account_id=account_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            action_type=action_type,
            status=status,
            details=details,
        )
        self.session.add(entry)
        await self.session.commit()
        return entry

    # ── Daily Stats ────────────────────────────────────────────────────────

    async def get_or_create_daily_stat(
        self, account_id: str, stat_date: date | None = None
    ) -> DailyStat:
        stat_date = stat_date or date.today()
        result = await self.session.execute(
            select(DailyStat).where(
                DailyStat.account_id == account_id,
                DailyStat.date == stat_date,
            )
        )
        stat = result.scalar_one_or_none()
        if stat is None:
            stat = DailyStat(account_id=account_id, date=stat_date)
            self.session.add(stat)
            await self.session.commit()
            await self.session.refresh(stat)
        return stat

    async def increment_daily_stat(
        self, account_id: str, field: str, amount: int = 1
    ) -> None:
        stat = await self.get_or_create_daily_stat(account_id)
        setattr(stat, field, getattr(stat, field) + amount)
        await self.session.commit()

    async def get_weekly_request_count(
        self, account_id: str, week_start: date
    ) -> int:
        """Sum connection_requests_sent from week_start through +6 days."""
        from datetime import timedelta
        week_end = week_start + timedelta(days=6)
        result = await self.session.execute(
            select(func.coalesce(func.sum(DailyStat.connection_requests_sent), 0))
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date >= week_start,
                DailyStat.date <= week_end,
            )
        )
        return result.scalar_one()

    async def get_daily_proxy_mb(self, account_id: str) -> float:
        """Get today's estimated proxy bandwidth usage in MB."""
        stat = await self.get_or_create_daily_stat(account_id)
        return stat.proxy_mb_used or 0.0

    async def add_proxy_mb(self, account_id: str, mb: float) -> None:
        """Add MB to today's proxy bandwidth usage tally."""
        stat = await self.get_or_create_daily_stat(account_id)
        stat.proxy_mb_used = round((stat.proxy_mb_used or 0.0) + mb, 2)
        await self.session.commit()

    async def get_daily_requests_sent(
        self, account_id: str, stat_date: date | None = None
    ) -> int:
        """Get today's connection_requests_sent count."""
        stat_date = stat_date or date.today()
        result = await self.session.execute(
            select(func.coalesce(DailyStat.connection_requests_sent, 0))
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date == stat_date,
            )
        )
        return result.scalar_one_or_none() or 0

    async def get_followup_messages_sent_today(
        self, account_id: str, stat_date: date | None = None
    ) -> int:
        """Get today's followup_messages_sent count from DailyStat."""
        stat_date = stat_date or date.today()
        result = await self.session.execute(
            select(func.coalesce(DailyStat.followup_messages_sent, 0))
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date == stat_date,
            )
        )
        return result.scalar_one_or_none() or 0

    async def get_lead_by_id(self, lead_id: str) -> Optional[Lead]:
        """Fetch a single lead by primary key (for pre-send status re-verification)."""
        return await self.session.get(Lead, lead_id)

    async def get_last_successful_followup_index(self, lead_id: str) -> int:
        """Return the highest message_index (1-based) successfully sent for this lead.

        Returns 0 if no successful FOLLOWUP_MESSAGE log exists. Used to resume
        a partial sequence: if message 1 sent+logged but message 2 failed, the
        next run skips index 0 and retries from index 1.
        """
        result = await self.session.execute(
            select(ActionLog.details)
            .where(
                ActionLog.lead_id == lead_id,
                ActionLog.action_type == ActionType.FOLLOWUP_MESSAGE,
                ActionLog.status == ActionLogStatus.SUCCESS,
            )
        )
        rows = result.scalars().all()
        if not rows:
            return 0
        return max((r.get("message_index", 0) for r in rows if r), default=0)

    async def get_followup_sequences_started_today(self, account_id: str) -> int:
        """Count distinct leads with at least one successful FOLLOWUP_MESSAGE today (UTC).

        Used as the daily cap check — counts sequences started, not individual
        messages sent, so multi-message sequences don't eat double the budget.
        """
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(func.count(func.distinct(ActionLog.lead_id)))
            .where(
                ActionLog.account_id == account_id,
                ActionLog.action_type == ActionType.FOLLOWUP_MESSAGE,
                ActionLog.status == ActionLogStatus.SUCCESS,
                ActionLog.created_at >= today_start,
            )
        )
        return result.scalar_one() or 0

    async def count_leads_by_status(self, campaign_id: str, statuses: list) -> int:
        """Count assignments in a campaign matching any of the given statuses.

        Phase 3b: reads from CampaignLeadAssignment.
        """
        status_vals = [
            s.value if hasattr(s, "value") else str(s).lower()
            for s in statuses
        ]
        result = await self.session.execute(
            select(func.count()).select_from(CampaignLeadAssignment).where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status.in_(status_vals),
            )
        )
        return result.scalar_one()

    async def count_leads_updated_today_with_status(self, campaign_id: str, status: LeadStatus) -> int:
        """Count assignments in a campaign updated today with the given status.

        Phase 3b: reads from CampaignLeadAssignment.
        """
        from sqlalchemy import cast, Date as SADate
        from datetime import date as date_type
        result = await self.session.execute(
            select(func.count()).select_from(CampaignLeadAssignment).where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == status.value,
                cast(CampaignLeadAssignment.updated_at, SADate) == date_type.today(),
            )
        )
        return result.scalar_one()

    async def count_pending_requests_for_account(self, account_id: str) -> int:
        """Count CONNECTION_REQUESTED assignments across all campaigns for an account.

        Phase 3b: reads from CampaignLeadAssignment.
        """
        result = await self.session.execute(
            select(func.count()).select_from(CampaignLeadAssignment)
            .join(Campaign, Campaign.id == CampaignLeadAssignment.campaign_id)
            .where(
                Campaign.account_id == account_id,
                CampaignLeadAssignment.status == LeadStatus.CONNECTION_REQUESTED.value,
            )
        )
        return result.scalar_one()

    async def get_todays_schedule(self, account_id: str, day_start: datetime, day_end: datetime) -> list:
        """Get today's scheduled + already-executed leads for an account.

        Returns list of dicts with lead info + campaign_name + status.

        Phase 3b: joins via CampaignLeadAssignment for status/timestamp fields.
        """
        # SCHEDULED assignments (not yet executed)
        scheduled_q = await self.session.execute(
            select(
                Lead, Campaign.name.label("campaign_name"),
                CampaignLeadAssignment.scheduled_at.label("asgn_scheduled_at"),
                CampaignLeadAssignment.status.label("asgn_status"),
            )
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .join(Campaign, Campaign.id == CampaignLeadAssignment.campaign_id)
            .where(
                Campaign.account_id == account_id,
                CampaignLeadAssignment.status == LeadStatus.SCHEDULED.value,
                CampaignLeadAssignment.scheduled_at >= day_start,
                CampaignLeadAssignment.scheduled_at < day_end,
            )
            .order_by(CampaignLeadAssignment.scheduled_at)
        )
        scheduled_rows = scheduled_q.all()

        # Already-executed today (CONNECTION_REQUESTED with connection_requested_at today)
        executed_q = await self.session.execute(
            select(
                Lead, Campaign.name.label("campaign_name"),
                CampaignLeadAssignment.connection_requested_at.label("asgn_req_at"),
                CampaignLeadAssignment.status.label("asgn_status"),
            )
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .join(Campaign, Campaign.id == CampaignLeadAssignment.campaign_id)
            .where(
                Campaign.account_id == account_id,
                CampaignLeadAssignment.status == LeadStatus.CONNECTION_REQUESTED.value,
                CampaignLeadAssignment.connection_requested_at >= day_start,
                CampaignLeadAssignment.connection_requested_at < day_end,
            )
            .order_by(CampaignLeadAssignment.connection_requested_at)
        )
        executed_rows = executed_q.all()

        # Also include connected leads that were requested today
        connected_statuses = [
            LeadStatus.CONNECTED.value,
            LeadStatus.FOLLOWUP_SCHEDULED.value,
            LeadStatus.FOLLOWUP_SENT.value,
            LeadStatus.COMPLETED.value,
        ]
        connected_q = await self.session.execute(
            select(
                Lead, Campaign.name.label("campaign_name"),
                CampaignLeadAssignment.connection_requested_at.label("asgn_req_at"),
                CampaignLeadAssignment.status.label("asgn_status"),
            )
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .join(Campaign, Campaign.id == CampaignLeadAssignment.campaign_id)
            .where(
                Campaign.account_id == account_id,
                CampaignLeadAssignment.status.in_(connected_statuses),
                CampaignLeadAssignment.connection_requested_at >= day_start,
                CampaignLeadAssignment.connection_requested_at < day_end,
            )
            .order_by(CampaignLeadAssignment.connection_requested_at)
        )
        connected_rows = connected_q.all()

        results = []
        for lead, cname, sched_at, status in scheduled_rows:
            results.append({
                "lead_id": lead.id,
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "linkedin_url": lead.linkedin_url,
                "campaign_name": cname,
                "scheduled_at": sched_at.isoformat() if sched_at else None,
                "status": status,
            })
        for lead, cname, req_at, _status in list(executed_rows) + list(connected_rows):
            results.append({
                "lead_id": lead.id,
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "linkedin_url": lead.linkedin_url,
                "campaign_name": cname,
                "scheduled_at": req_at.isoformat() if req_at else None,
                "status": "sent",
            })

        # Sort by scheduled_at
        results.sort(key=lambda r: r["scheduled_at"] or "")
        return results

    async def get_account_health_stats(self, account_id: str) -> dict:
        """Get health stats for an account: last activity, 7d error rate."""
        from datetime import timedelta
        seven_days_ago = datetime.utcnow() - timedelta(days=7)

        # Last action + 7d error rate in one query
        result = await self.session.execute(
            select(
                func.max(ActionLog.created_at).label("last_action_at"),
                func.count().label("total_7d"),
                func.sum(
                    case(
                        (ActionLog.status == ActionLogStatus.FAILED, 1),
                        else_=0,
                    )
                ).label("errors_7d"),
            ).where(
                ActionLog.account_id == account_id,
                ActionLog.created_at >= seven_days_ago,
            )
        )
        row = result.one()
        total_7d = row.total_7d or 0
        errors_7d = row.errors_7d or 0
        last_action_at = row.last_action_at

        # Last error message
        last_error_msg = None
        if errors_7d > 0:
            err_result = await self.session.execute(
                select(ActionLog.details)
                .where(
                    ActionLog.account_id == account_id,
                    ActionLog.status == ActionLogStatus.FAILED,
                )
                .order_by(ActionLog.created_at.desc())
                .limit(1)
            )
            details = err_result.scalar_one_or_none()
            if details and isinstance(details, dict):
                last_error_msg = details.get("error") or details.get("reason")

        days_since = None
        if last_action_at:
            days_since = (datetime.utcnow() - last_action_at).days

        return {
            "last_action_at": last_action_at,
            "days_since_last_activity": days_since,
            "error_rate_7d": round(errors_7d / total_7d * 100, 1) if total_7d > 0 else 0.0,
            "total_actions_7d": total_7d,
            "errors_7d": errors_7d,
            "last_error_message": last_error_msg,
        }

    # ── Scheduler helpers ─────────────────────────────────────────────────

    async def list_active_accounts(self) -> Sequence[Account]:
        """Get all non-archived accounts with status=active."""
        result = await self.session.execute(
            select(Account).where(
                Account.status == AccountStatus.ACTIVE,
                Account.archived == False,  # noqa: E712
            )
        )
        return result.scalars().all()

    async def list_paused_accounts(self) -> Sequence[Account]:
        """Get all accounts with a paused_until set."""
        result = await self.session.execute(
            select(Account).where(Account.paused_until.isnot(None))
        )
        return result.scalars().all()

    async def get_active_campaigns(self, account_id: str) -> Sequence[Campaign]:
        """Get all active, non-archived campaigns for an account."""
        result = await self.session.execute(
            select(Campaign).where(
                Campaign.account_id == account_id,
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.archived == False,  # noqa: E712
            )
        )
        return result.scalars().all()

    async def get_operational_campaigns(self, account_id: str) -> Sequence[Campaign]:
        """Get all non-archived campaigns (active + paused) for an account.

        Used by acceptance checker and follow-up dispatcher so that leads in
        paused campaigns are still processed — connections accepted while a
        campaign is paused should still be marked CONNECTED.
        """
        result = await self.session.execute(
            select(Campaign).where(
                Campaign.account_id == account_id,
                Campaign.archived == False,  # noqa: E712
            )
        )
        return result.scalars().all()

    async def update_lead_schedule(
        self, lead_id: str, scheduled_at, new_status: LeadStatus
    ) -> None:
        """Update a lead assignment's scheduled_at and status.

        Phase 3b: writes only to CampaignLeadAssignment. Called by the daily
        planner, which always has a specific campaign context via the lead_id
        having exactly one relevant assignment.
        """
        await self.session.execute(
            update(CampaignLeadAssignment)
            .where(CampaignLeadAssignment.lead_id == lead_id)
            .values(scheduled_at=scheduled_at, status=new_status.value)
        )
        await self.session.commit()

    async def bulk_update_lead_status(
        self,
        campaign_id: str,
        from_status: LeadStatus,
        to_status: LeadStatus,
    ) -> int:
        """Bulk update assignments from one status to another within a campaign.

        Phase 3b: writes only to CampaignLeadAssignment.
        """
        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == from_status.value,
            )
            .values(status=to_status.value)
        )
        await self.session.commit()
        return result.rowcount

    async def reset_campaign_leads(
        self,
        campaign_id: str,
        statuses: list[LeadStatus] | None = None,
    ) -> int:
        """
        Reset assignments back to PENDING so leads can be reprocessed.
        If statuses is None, resets error, skipped, and limit_paused leads.
        Also clears error_message, retry_count, and timestamp fields.

        Phase 3b: writes only to CampaignLeadAssignment.
        """
        if statuses is None:
            statuses = [LeadStatus.ERROR, LeadStatus.SKIPPED, LeadStatus.LIMIT_PAUSED]

        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status.in_([s.value for s in statuses]),
            )
            .values(
                status=LeadStatus.PENDING.value,
                error_message=None,
                retry_count=0,
                connection_requested_at=None,
                connection_accepted_at=None,
                followup_sent_at=None,
                scheduled_at=None,
            )
        )
        await self.session.commit()
        return result.rowcount

    async def list_leads_paginated(
        self,
        campaign_id: str,
        page: int = 1,
        per_page: int = 50,
        status_filter: str | None = None,
        search: str | None = None,
        exclude_removed: bool = False,
        lead_list_id: str | None = None,
        sort_by: str | None = None,
        sort_dir: str = "asc",
        last_activity_after: str | None = None,
        last_activity_before: str | None = None,
        skip_reason: str | None = None,
        include_archived: bool = False,
    ) -> tuple:
        """Return (leads, total_count) with pagination, optional status filter and search."""
        last_activity_sq = _last_activity_subquery()
        stmt = (
            select(Lead)
            .outerjoin(last_activity_sq, last_activity_sq.c.lead_id == Lead.id)
            .where(Lead.campaign_id == campaign_id)
        )
        count_stmt = (
            select(func.count())
            .select_from(Lead)
            .outerjoin(last_activity_sq, last_activity_sq.c.lead_id == Lead.id)
            .where(Lead.campaign_id == campaign_id)
        )

        if not include_archived:
            stmt = stmt.where(_visible_lead_condition())
            count_stmt = count_stmt.where(_visible_lead_condition())
        if status_filter:
            stmt = stmt.where(Lead.status == status_filter)
            count_stmt = count_stmt.where(Lead.status == status_filter)
        elif exclude_removed:
            stmt = stmt.where(Lead.status != "REMOVED")
            count_stmt = count_stmt.where(Lead.status != "REMOVED")

        if lead_list_id:
            stmt = stmt.where(Lead.lead_list_id == lead_list_id)
            count_stmt = count_stmt.where(Lead.lead_list_id == lead_list_id)

        if search:
            pattern = _lead_search_pattern(search)
            full_name = func.coalesce(Lead.first_name, "") + " " + func.coalesce(Lead.last_name, "")
            search_filter = or_(
                Lead.first_name.ilike(pattern),
                Lead.last_name.ilike(pattern),
                full_name.ilike(pattern),
                Lead.company.ilike(pattern),
                Lead.title.ilike(pattern),
                Lead.linkedin_url.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if last_activity_after:
            stmt = stmt.where(last_activity_sq.c.last_activity_at >= last_activity_after)
            count_stmt = count_stmt.where(last_activity_sq.c.last_activity_at >= last_activity_after)
        if last_activity_before:
            stmt = stmt.where(last_activity_sq.c.last_activity_at <= last_activity_before)
            count_stmt = count_stmt.where(last_activity_sq.c.last_activity_at <= last_activity_before)

        if skip_reason:
            stmt = stmt.where(Lead.error_message == skip_reason)
            count_stmt = count_stmt.where(Lead.error_message == skip_reason)

        total = (await self.session.execute(count_stmt)).scalar_one()

        _SORT_COLS = {
            "name": Lead.first_name,
            "company": Lead.company,
            "status": Lead.status,
            "last_activity_at": last_activity_sq.c.last_activity_at,
            "created_at": Lead.created_at,
        }
        col = _SORT_COLS.get(sort_by or "created_at", Lead.created_at)
        order_col = col.desc() if sort_dir == "desc" else col.asc()
        stmt = (
            stmt.add_columns(last_activity_sq.c.last_activity_at)
            .order_by(order_col)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self.session.execute(stmt)
        leads = []
        for lead, last_activity_at in result.all():
            lead.last_activity_at = last_activity_at
            leads.append(lead)
        return leads, total

    # ── Phase 2 read cutover: campaign-scoped lead listing via assignments ──
    # Same filters and sort options as `list_leads_paginated`, but the
    # primary table is `campaign_lead_assignments` joined to `leads` for
    # identity fields. Returns objects that quack like Lead rows for the
    # consumer (status/timestamps come from the assignment side).

    async def list_leads_via_assignments_paginated(
        self,
        campaign_id: str,
        page: int = 1,
        per_page: int = 50,
        status_filter: str | None = None,
        search: str | None = None,
        exclude_removed: bool = False,
        lead_list_id: str | None = None,
        sort_by: str | None = None,
        sort_dir: str = "asc",
        last_activity_after: str | None = None,
        last_activity_before: str | None = None,
        skip_reason: str | None = None,
        include_archived: bool = False,
    ) -> tuple:
        """Mirror of list_leads_paginated reading from CampaignLeadAssignment.

        The result row shape matches what the export consumer expects —
        Lead identity fields plus assignment-derived state fields. Returns
        a list of duck-typed objects with the same attribute names as Lead.
        """
        # Inline class so we don't add a public type for what's currently a
        # transitional shape. Once Phase 3 lands and the legacy path is gone,
        # we can fold this into a proper return type.
        class _LeadView:
            __slots__ = (
                "id", "linkedin_url", "first_name", "last_name", "company",
                "title", "email", "phone", "extra_data", "campaign_id",
                "lead_list_id", "created_at",
                # State fields sourced from the assignment row
                "status", "scheduled_at",
                "connection_requested_at", "connection_accepted_at",
                "followup_sent_at", "error_message", "retry_count",
                "last_activity_at",
            )

            def __init__(self, **kwargs):
                for k in self.__slots__:
                    setattr(self, k, kwargs.get(k))

        # Status comparisons need to bridge the enum/string gap. Legacy
        # `leads.status` stores upper-case (e.g. "CONNECTION_REQUESTED")
        # while assignments stores lower-case ("connection_requested").
        # We normalise inputs to lower-case for the assignment table.

        last_activity_sq = _last_activity_subquery()
        stmt = (
            select(
                CampaignLeadAssignment.id.label("assignment_id"),
                CampaignLeadAssignment.status,
                CampaignLeadAssignment.scheduled_at,
                CampaignLeadAssignment.connection_requested_at,
                CampaignLeadAssignment.connection_accepted_at,
                CampaignLeadAssignment.followup_sent_at,
                CampaignLeadAssignment.error_message,
                CampaignLeadAssignment.retry_count,
                CampaignLeadAssignment.lead_list_id.label("asgn_lead_list_id"),
                Lead.id,
                Lead.linkedin_url,
                Lead.first_name,
                Lead.last_name,
                Lead.company,
                Lead.title,
                Lead.email,
                Lead.phone,
                Lead.extra_data,
                Lead.campaign_id,
                Lead.lead_list_id,
                Lead.created_at,
                last_activity_sq.c.last_activity_at,
            )
            .join(Lead, Lead.id == CampaignLeadAssignment.lead_id)
            .outerjoin(last_activity_sq, last_activity_sq.c.lead_id == Lead.id)
            .where(CampaignLeadAssignment.campaign_id == campaign_id)
        )
        count_stmt = (
            select(func.count())
            .select_from(CampaignLeadAssignment)
            .join(Lead, Lead.id == CampaignLeadAssignment.lead_id)
            .outerjoin(last_activity_sq, last_activity_sq.c.lead_id == Lead.id)
            .where(CampaignLeadAssignment.campaign_id == campaign_id)
        )

        if not include_archived:
            stmt = stmt.where(_visible_lead_condition())
            count_stmt = count_stmt.where(_visible_lead_condition())
        if status_filter:
            sf = status_filter.lower()
            stmt = stmt.where(CampaignLeadAssignment.status == sf)
            count_stmt = count_stmt.where(CampaignLeadAssignment.status == sf)
        elif exclude_removed:
            stmt = stmt.where(CampaignLeadAssignment.status != "removed")
            count_stmt = count_stmt.where(CampaignLeadAssignment.status != "removed")

        if lead_list_id:
            stmt = stmt.where(CampaignLeadAssignment.lead_list_id == lead_list_id)
            count_stmt = count_stmt.where(
                CampaignLeadAssignment.lead_list_id == lead_list_id
            )

        if search:
            pattern = _lead_search_pattern(search)
            full_name = func.coalesce(Lead.first_name, "") + " " + func.coalesce(Lead.last_name, "")
            search_filter = or_(
                Lead.first_name.ilike(pattern),
                Lead.last_name.ilike(pattern),
                full_name.ilike(pattern),
                Lead.company.ilike(pattern),
                Lead.title.ilike(pattern),
                Lead.linkedin_url.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if last_activity_after:
            stmt = stmt.where(
                last_activity_sq.c.last_activity_at >= last_activity_after
            )
            count_stmt = count_stmt.where(
                last_activity_sq.c.last_activity_at >= last_activity_after
            )
        if last_activity_before:
            stmt = stmt.where(
                last_activity_sq.c.last_activity_at <= last_activity_before
            )
            count_stmt = count_stmt.where(
                last_activity_sq.c.last_activity_at <= last_activity_before
            )

        if skip_reason:
            stmt = stmt.where(CampaignLeadAssignment.error_message == skip_reason)
            count_stmt = count_stmt.where(
                CampaignLeadAssignment.error_message == skip_reason
            )

        total = (await self.session.execute(count_stmt)).scalar_one()

        _SORT_COLS = {
            "name": Lead.first_name,
            "company": Lead.company,
            "status": CampaignLeadAssignment.status,
            "last_activity_at": last_activity_sq.c.last_activity_at,
            "created_at": Lead.created_at,
        }
        col = _SORT_COLS.get(sort_by or "created_at", Lead.created_at)
        order_col = col.desc() if sort_dir == "desc" else col.asc()
        stmt = stmt.order_by(order_col).offset((page - 1) * per_page).limit(per_page)

        result = await self.session.execute(stmt)
        rows = result.all()
        views = []
        for row in rows:
            m = row._mapping
            views.append(_LeadView(
                id=m["id"],
                linkedin_url=m["linkedin_url"],
                first_name=m["first_name"],
                last_name=m["last_name"],
                company=m["company"],
                title=m["title"],
                email=m["email"],
                phone=m["phone"],
                extra_data=m["extra_data"],
                campaign_id=m["campaign_id"],
                # Prefer the assignment's lead_list_id (which lead-list
                # brought this lead into THIS campaign); fall back to Lead's.
                lead_list_id=m["asgn_lead_list_id"] or m["lead_list_id"],
                created_at=m["created_at"],
                status=m["status"],  # lower-case from assignment
                scheduled_at=m["scheduled_at"],
                connection_requested_at=m["connection_requested_at"],
                connection_accepted_at=m["connection_accepted_at"],
                followup_sent_at=m["followup_sent_at"],
                error_message=m["error_message"],
                retry_count=m["retry_count"],
                last_activity_at=m["last_activity_at"],
            ))
        return views, total

    async def list_action_log(
        self,
        account_id: str | None = None,
        campaign_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[ActionLog]:
        """List action log entries, newest first."""
        stmt = select(ActionLog).order_by(ActionLog.created_at.desc())
        if account_id:
            stmt = stmt.where(ActionLog.account_id == account_id)
        if campaign_id:
            stmt = stmt.where(ActionLog.campaign_id == campaign_id)
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_daily_stats_range(
        self,
        account_id: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyStat]:
        """Get daily stats for an account within a date range."""
        result = await self.session.execute(
            select(DailyStat)
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date >= start_date,
                DailyStat.date <= end_date,
            )
            .order_by(DailyStat.date)
        )
        return result.scalars().all()

    async def bulk_update_lead_status_for_account(
        self,
        account_id: str,
        from_status: LeadStatus,
        to_status: LeadStatus,
    ) -> int:
        """Bulk update leads across all campaigns of an account."""
        # Get all campaign IDs for this account
        camp_result = await self.session.execute(
            select(Campaign.id).where(Campaign.account_id == account_id)
        )
        campaign_ids = [r[0] for r in camp_result.all()]
        if not campaign_ids:
            return 0

        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.campaign_id.in_(campaign_ids),
                CampaignLeadAssignment.status == from_status.value,
            )
            .values(status=to_status.value)
        )
        await self.session.commit()
        return result.rowcount

    # ── Withdrawal helpers ──────────────────────────────────────────────

    async def get_leads_by_url(self, account_id: str, linkedin_url: str) -> Sequence[Lead]:
        """Find leads by LinkedIn URL across all campaigns for an account.

        Phase 3b: joins via CampaignLeadAssignment → Campaign instead of
        the stale Lead.campaign_id column.
        """
        # Normalize: strip trailing slash for matching
        url_base = linkedin_url.rstrip("/")
        result = await self.session.execute(
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .join(Campaign, Campaign.id == CampaignLeadAssignment.campaign_id)
            .where(
                Campaign.account_id == account_id,
                Lead.linkedin_url.contains(url_base.split("/in/")[-1] if "/in/" in url_base else url_base),
            )
            .distinct()
        )
        return result.scalars().all()

    # ── Campaign Stats ─────────────────────────────────────────────────

    async def get_campaign_daily_stats(
        self, campaign_id: str, start_date: Optional[date], end_date: date,
        granularity: str = "day",
        include_archived: bool = False,
    ) -> list:
        """Aggregate stats per campaign from ActionLog + accepted from leads."""
        from sqlalchemy import cast, Date as SADate, case, text

        # ── Sent + errors from action_log ──────────────────────────────────
        if granularity == "hour":
            # SQLite: bucket by hour string
            bucket_expr = func.strftime("%Y-%m-%dT%H:00", ActionLog.created_at).label("bucket")
        else:
            bucket_expr = cast(ActionLog.created_at, SADate).label("bucket")

        sent_q = (
            select(
                bucket_expr,
                func.sum(case(
                    (ActionLog.action_type == ActionType.CONNECTION_REQUEST, 1),
                    else_=0,
                )).label("sent"),
                func.sum(case(
                    (ActionLog.action_type == ActionType.ERROR, 1),
                    else_=0,
                )).label("errors"),
            )
            .where(ActionLog.campaign_id == campaign_id)
            .where(True if include_archived else or_(ActionLog.lead_id.is_(None), ActionLog.lead_id.in_(_visible_lead_ids())))
            .group_by(text("bucket"))
            .order_by(text("bucket"))
        )
        if start_date:
            sent_q = sent_q.where(cast(ActionLog.created_at, SADate) >= start_date)
        sent_q = sent_q.where(cast(ActionLog.created_at, SADate) <= end_date)

        sent_rows = (await self.session.execute(sent_q)).all()

        # ── Accepted from assignments (connection_accepted_at is authoritative) ─
        # Phase 3b: reads from CampaignLeadAssignment.
        if granularity == "hour":
            acc_bucket = func.strftime("%Y-%m-%dT%H:00", CampaignLeadAssignment.connection_accepted_at).label("bucket")
        else:
            acc_bucket = cast(CampaignLeadAssignment.connection_accepted_at, SADate).label("bucket")

        acc_q = (
            select(acc_bucket, func.count().label("accepted"))
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.connection_accepted_at.isnot(None),
                True if include_archived else CampaignLeadAssignment.lead_id.in_(_visible_lead_ids()),
            )
            .group_by(text("bucket"))
        )
        if start_date:
            acc_q = acc_q.where(cast(CampaignLeadAssignment.connection_accepted_at, SADate) >= start_date)
        acc_q = acc_q.where(cast(CampaignLeadAssignment.connection_accepted_at, SADate) <= end_date)

        acc_map: dict = {row.bucket: row.accepted for row in (await self.session.execute(acc_q)).all()}

        return [
            {
                "date": str(row.bucket),
                "sent": row.sent,
                "accepted": acc_map.get(str(row.bucket), 0),
                "errors": row.errors,
            }
            for row in sent_rows
        ]

    async def get_campaign_acceptance_stats(self, campaign_id: str, include_archived: bool = False) -> dict:
        """Get acceptance rate and average time-to-accept for a campaign."""
        # total_sent: count successful CONNECTION_REQUEST actions (more reliable than
        # connection_requested_at which was not always populated in older runs)
        sent_result = await self.session.execute(
            select(func.count())
            .where(
                ActionLog.campaign_id == campaign_id,
                ActionLog.action_type == ActionType.CONNECTION_REQUEST,
                ActionLog.status == ActionLogStatus.SUCCESS,
                True if include_archived else ActionLog.lead_id.in_(_visible_lead_ids()),
            )
        )
        total_sent = sent_result.scalar() or 0

        # total_accepted: assignments with connection_accepted_at set (authoritative)
        # Phase 3b: reads from CampaignLeadAssignment.
        acc_result = await self.session.execute(
            select(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.connection_accepted_at.isnot(None),
                True if include_archived else CampaignLeadAssignment.lead_id.in_(_visible_lead_ids()),
            )
        )
        accepted_asgns = acc_result.scalars().all()
        total_accepted = len(accepted_asgns)

        avg_hours = None
        if accepted_asgns:
            deltas = [
                (a.connection_accepted_at - a.connection_requested_at).total_seconds() / 3600
                for a in accepted_asgns
                if a.connection_accepted_at and a.connection_requested_at
            ]
            avg_hours = round(sum(deltas) / len(deltas), 1) if deltas else None

        return {
            "total_sent": total_sent,
            "total_accepted": total_accepted,
            "acceptance_rate": round(total_accepted / total_sent * 100, 1) if total_sent > 0 else 0,
            "avg_time_to_accept_hours": avg_hours,
        }

    # ── Lead Lists ────────────────────────────────────────────────────────

    async def create_lead_list(
        self,
        name: str,
        csv_filename: str | None = None,
        tg_enrich_enabled: bool = True,
    ) -> LeadList:
        lead_list = LeadList(
            name=name,
            csv_filename=csv_filename,
            tg_enrich_enabled=tg_enrich_enabled,
        )
        self.session.add(lead_list)
        await self.session.commit()
        await self.session.refresh(lead_list)
        return lead_list

    async def get_lead_list(self, lead_list_id: str) -> LeadList | None:
        return await self.session.get(LeadList, lead_list_id)

    async def get_lead_memberships(self, lead_id: str, include_archived: bool = False) -> list:
        """Return all lists a lead belongs to, most-recent first.

        Returns list of (lead_list_id, name, added_at) tuples.
        """
        stmt = (
            select(LeadList.id, LeadList.name, LeadListMembership.added_at)
            .join(LeadListMembership, LeadList.id == LeadListMembership.lead_list_id)
            .where(LeadListMembership.lead_id == lead_id)
            .order_by(LeadListMembership.added_at.desc())
        )
        if not include_archived:
            stmt = stmt.where(LeadList.archived == False)
        result = await self.session.execute(stmt)
        return result.all()

    async def get_lead_campaigns(self, lead_id: str, include_archived: bool = False) -> list:
        """Return all campaigns a lead is assigned to, with their per-campaign status.

        Returns list of (campaign_id, name, status, account_name) tuples.
        """
        stmt = (
            select(
                Campaign.id, Campaign.name,
                CampaignLeadAssignment.status,
                Account.name,
            )
            .join(CampaignLeadAssignment, Campaign.id == CampaignLeadAssignment.campaign_id)
            .join(Account, Campaign.account_id == Account.id, isouter=True)
            .where(CampaignLeadAssignment.lead_id == lead_id)
            .order_by(CampaignLeadAssignment.created_at.desc())
        )
        if not include_archived:
            stmt = stmt.where(Campaign.id.in_(_visible_campaign_ids()))
        result = await self.session.execute(stmt)
        return result.all()

    async def get_campaign_assignments_for_leads(
        self, lead_ids: list, include_archived: bool = False,
        target_campaign_id: Optional[str] = None,
    ) -> dict:
        """Batch campaign summaries, ordered by assignment creation time."""
        if not lead_ids:
            return {}
        stmt = (
            select(CampaignLeadAssignment, Campaign.name, Account.name)
            .join(Campaign, Campaign.id == CampaignLeadAssignment.campaign_id)
            .outerjoin(Account, Account.id == Campaign.account_id)
            .where(CampaignLeadAssignment.lead_id.in_(lead_ids))
            .order_by(
                CampaignLeadAssignment.created_at.desc(),
                CampaignLeadAssignment.id.desc(),
            )
        )
        if not include_archived:
            stmt = stmt.where(or_(
                Campaign.id.in_(_visible_campaign_ids()),
                Campaign.id == target_campaign_id if target_campaign_id else False,
            ))
        result = await self.session.execute(stmt)
        grouped = {}
        for assignment, name, account_name in result.all():
            grouped.setdefault(assignment.lead_id, []).append(
                (assignment, name, account_name)
            )
        return grouped

    async def get_most_recent_list_for_leads(
        self, lead_ids: list, include_archived: bool = False,
        target_list_ids: Optional[list] = None,
    ) -> dict:
        """Return {lead_id: (list_id, list_name)} for the most-recent membership per lead.

        Used by _enrich_leads to populate lead_list_name in list views without
        relying on the denormalized leads.lead_list_id column.
        """
        if not lead_ids:
            return {}
        query = (
            select(
                LeadListMembership.lead_id,
                LeadList.id.label("list_id"), LeadList.name,
                func.row_number().over(
                    partition_by=LeadListMembership.lead_id,
                    order_by=(LeadListMembership.added_at.desc(), LeadListMembership.id.desc()),
                ).label("position"),
            )
            .join(LeadList, LeadList.id == LeadListMembership.lead_list_id)
            .where(LeadListMembership.lead_id.in_(lead_ids))
        )
        if not include_archived:
            query = query.where(or_(
                LeadList.archived == False, LeadList.id.in_(target_list_ids or []),
            ))
        subq = query.subquery()
        result = await self.session.execute(
            select(subq.c.lead_id, subq.c.list_id, subq.c.name)
            .where(subq.c.position == 1)
        )
        return {row[0]: (row[1], row[2]) for row in result.all()}

    async def get_lead_list_by_name(self, name: str) -> LeadList | None:
        result = await self.session.execute(
            select(LeadList).where(LeadList.name == name)
        )
        return result.scalar_one_or_none()

    async def list_lead_lists(self, include_archived: bool = False) -> Sequence[LeadList]:
        stmt = select(LeadList).order_by(LeadList.created_at.desc())
        if not include_archived:
            stmt = stmt.where(LeadList.archived == False)  # noqa: E712
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_lead_list(self, lead_list: LeadList, **kwargs) -> LeadList:
        for key, value in kwargs.items():
            setattr(lead_list, key, value)
        await self.session.commit()
        await self.session.refresh(lead_list)
        return lead_list

    async def delete_lead_list(self, lead_list_id: str) -> bool:
        """Delete the list and contacts that have no other list, atomically."""
        from sqlalchemy import delete as sa_delete
        try:
            # Acquire the SQLite writer lock before deciding which leads are shared.
            locked = await self.session.execute(
                update(LeadList).where(LeadList.id == lead_list_id)
                .values(updated_at=LeadList.updated_at)
            )
            if not locked.rowcount:
                await self.session.rollback()
                return False
            exclusive_query = select(Lead.id).where(
                Lead.id.in_(select(LeadListMembership.lead_id).where(
                    LeadListMembership.lead_list_id == lead_list_id
                )),
                not_(exists(select(LeadListMembership.lead_id).where(
                    LeadListMembership.lead_id == Lead.id,
                    LeadListMembership.lead_list_id != lead_list_id,
                ))),
            )
            exclusive_ids = list((await self.session.execute(exclusive_query)).scalars())
            campaign_ids = set()
            broadcast_ids = set()
            # Bound SQL parameters so large imported lists can be deleted safely.
            for offset in range(0, len(exclusive_ids), 400):
                batch = exclusive_ids[offset:offset + 400]
                campaign_ids.update((await self.session.execute(
                    select(CampaignLeadAssignment.campaign_id).where(CampaignLeadAssignment.lead_id.in_(batch))
                )).scalars())
                broadcast_ids.update((await self.session.execute(
                    select(BroadcastLead.broadcast_id).where(BroadcastLead.lead_id.in_(batch))
                )).scalars())
                for model in (ActionLog, LeadEvent, LeadNote, BroadcastLead,
                              CampaignLeadAssignment, LeadListMembership):
                    await self.session.execute(sa_delete(model).where(model.lead_id.in_(batch)))
                await self.session.execute(sa_delete(Lead).where(Lead.id.in_(batch)))

            await self.session.execute(sa_delete(CampaignLeadList).where(
                CampaignLeadList.lead_list_id == lead_list_id
            ))
            await self.session.execute(sa_delete(LeadListMembership).where(
                LeadListMembership.lead_list_id == lead_list_id
            ))
            await self.session.execute(update(CampaignLeadAssignment).where(
                CampaignLeadAssignment.lead_list_id == lead_list_id
            ).values(lead_list_id=None))
            await self.session.execute(update(Lead).where(
                Lead.lead_list_id == lead_list_id
            ).values(lead_list_id=None))
            await self.session.execute(update(Broadcast).where(
                Broadcast.source_list_id == lead_list_id
            ).values(source_list_id=None))
            await self.session.execute(sa_delete(LeadList).where(LeadList.id == lead_list_id))
            for campaign_id in campaign_ids:
                total = (await self.session.execute(select(func.count()).select_from(
                    CampaignLeadAssignment
                ).where(CampaignLeadAssignment.campaign_id == campaign_id))).scalar_one()
                await self.session.execute(update(Campaign).where(Campaign.id == campaign_id).values(total_leads=total))
            for broadcast_id in broadcast_ids:
                total = (await self.session.execute(select(func.count()).select_from(
                    BroadcastLead
                ).where(BroadcastLead.broadcast_id == broadcast_id))).scalar_one()
                await self.session.execute(update(Broadcast).where(Broadcast.id == broadcast_id).values(total_leads=total))
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return True

    async def get_list_leads(
        self, lead_list_id: str, page: int = 1, per_page: int = 50
    ) -> tuple:
        """Return (leads, total_count) for a lead list via LeadListMembership."""
        member_ids = select(LeadListMembership.lead_id).where(
            LeadListMembership.lead_list_id == lead_list_id
        )
        count_stmt = select(func.count()).select_from(Lead).where(Lead.id.in_(member_ids))
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = (
            select(Lead)
            .where(Lead.id.in_(member_ids))
            .order_by(Lead.created_at)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

    async def get_list_lead_urls(self, lead_list_id: str) -> set:
        """Get all linkedin_urls in a lead list (for dedup), via LeadListMembership."""
        result = await self.session.execute(
            select(Lead.linkedin_url)
            .join(LeadListMembership, LeadListMembership.lead_id == Lead.id)
            .where(
                LeadListMembership.lead_list_id == lead_list_id,
                Lead.linkedin_url.isnot(None),
            )
        )
        return {r[0] for r in result.all()}

    async def update_leads_enrichment(self, enrichment_map: dict) -> int:
        """Bulk-update Apollo enrichment fields on canonical Lead rows by linkedin_url.

        enrichment_map: {linkedin_url: {first_name, last_name, email, company, title}}
        Only sets fields that are non-None in the incoming data. Runs inside a
        single transaction — fast enough for 500 rows in SQLite.
        """
        from sqlalchemy import update as sa_update

        updated = 0
        for li_url, data in enrichment_map.items():
            non_null = {k: v for k, v in data.items() if v is not None}
            if not non_null:
                continue
            r = await self.session.execute(
                sa_update(Lead).where(Lead.linkedin_url == li_url).values(**non_null)
            )
            updated += r.rowcount
        await self.session.commit()
        return updated

    # ── Campaign ↔ Lead List Assignment ───────────────────────────────────

    async def assign_list_to_campaign(
        self, lead_list_id: str, campaign_id: str
    ) -> int:
        """
        Assign a lead list to a campaign: creates CampaignLeadAssignment rows
        for all leads in the list that don't already have one for this campaign.
        Returns number of new assignments created.

        Phase 3b rewrite: no longer clones Lead rows (which caused duplicates).
        Uses LeadListMembership as authoritative list of leads in the list, and
        checks CampaignLeadAssignment for dedup instead of Lead.campaign_id.
        """
        # Create/confirm the CampaignLeadList junction record
        existing_link = await self.session.execute(
            select(CampaignLeadList).where(
                CampaignLeadList.campaign_id == campaign_id,
                CampaignLeadList.lead_list_id == lead_list_id,
            )
        )
        if not existing_link.scalar_one_or_none():
            self.session.add(CampaignLeadList(
                campaign_id=campaign_id, lead_list_id=lead_list_id
            ))

        # Leads already assigned to this campaign (by lead_id)
        existing_asgn_result = await self.session.execute(
            select(CampaignLeadAssignment.lead_id).where(
                CampaignLeadAssignment.campaign_id == campaign_id,
            )
        )
        already_assigned: set = {r[0] for r in existing_asgn_result.all()}

        # All leads in this list via LeadListMembership (authoritative after Phase 3a)
        membership_result = await self.session.execute(
            select(LeadListMembership.lead_id).where(
                LeadListMembership.lead_list_id == lead_list_id,
            )
        )
        list_lead_ids = [r[0] for r in membership_result.all()]

        new_assignments = []
        for lead_id in list_lead_ids:
            if lead_id in already_assigned:
                continue
            already_assigned.add(lead_id)
            new_assignments.append(CampaignLeadAssignment(
                lead_id=lead_id,
                campaign_id=campaign_id,
                lead_list_id=lead_list_id,
                status=LeadStatus.PENDING.value,
            ))

        if new_assignments:
            self.session.add_all(new_assignments)

        await self.session.commit()
        return len(new_assignments)

    async def unassign_list_from_campaign(
        self, lead_list_id: str, campaign_id: str
    ) -> int:
        """
        Unassign a lead list from a campaign: marks leads from that list as REMOVED.
        Returns number of leads removed.
        """
        from sqlalchemy import delete as sa_delete

        # Remove the junction link
        await self.session.execute(
            sa_delete(CampaignLeadList).where(
                CampaignLeadList.campaign_id == campaign_id,
                CampaignLeadList.lead_list_id == lead_list_id,
            )
        )

        # Mark assignments from this list in this campaign as REMOVED
        # Phase 3b: writes only to CampaignLeadAssignment.
        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.lead_list_id == lead_list_id,
                CampaignLeadAssignment.status != LeadStatus.REMOVED.value,
            )
            .values(status=LeadStatus.REMOVED.value)
        )
        await self.session.commit()
        return result.rowcount

    async def get_campaign_lists(
        self, campaign_id: str, include_archived: bool = False,
    ) -> Sequence[CampaignLeadList]:
        """Get all lead list links for a campaign, highest priority first."""
        stmt = (
            select(CampaignLeadList)
            .where(CampaignLeadList.campaign_id == campaign_id)
            .order_by(CampaignLeadList.priority.desc(), CampaignLeadList.created_at)
        )
        if not include_archived:
            stmt = stmt.where(CampaignLeadList.lead_list_id.in_(
                select(LeadList.id).where(LeadList.archived == False)
            ))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def set_campaign_lists_order(
        self, campaign_id: str, ordered_list_ids: Sequence[str]
    ) -> int:
        """Set dispatch priority for a campaign's lists from an ordered id list.

        ``ordered_list_ids`` is highest-priority-first. Priority values are
        assigned descending (first item gets the largest number) so the
        existing ``priority DESC`` ordering dispatches them in array order.
        Returns the number of links updated.
        """
        n = len(ordered_list_ids)
        updated = 0
        for idx, list_id in enumerate(ordered_list_ids):
            result = await self.session.execute(
                update(CampaignLeadList)
                .where(
                    CampaignLeadList.campaign_id == campaign_id,
                    CampaignLeadList.lead_list_id == list_id,
                )
                .values(priority=n - idx)
            )
            updated += result.rowcount
        await self.session.commit()
        return updated

    async def get_list_campaigns(
        self, lead_list_id: str, include_archived: bool = False,
    ) -> Sequence[CampaignLeadList]:
        """Get all campaign links for a lead list."""
        stmt = (
            select(CampaignLeadList).where(
                CampaignLeadList.lead_list_id == lead_list_id
            )
        )
        if not include_archived:
            stmt = stmt.where(CampaignLeadList.campaign_id.in_(_visible_campaign_ids()))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    # ── Lead Soft Delete / Restore ────────────────────────────────────────

    async def remove_lead(self, lead_id: str) -> Lead | None:
        """Soft delete a lead by setting status to REMOVED in all its assignments.

        Phase 3b: writes to CampaignLeadAssignment directly (canonical source).
        The Lead row is still returned for API response compatibility.
        """
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return None
        # Update all assignments for this lead
        await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.lead_id == lead_id,
                CampaignLeadAssignment.status != "removed",
            )
            .values(status="removed")
        )
        # Also flip the legacy Lead.status. The lead detail page and the global
        # leads library read Lead.status (not the assignment), so without this
        # the "deleted" lead keeps showing as active everywhere.
        lead.status = LeadStatus.REMOVED
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def restore_lead(self, lead_id: str) -> Lead | None:
        """Restore a removed lead back to PENDING in all its assignments.

        Phase 3b: writes to CampaignLeadAssignment directly.
        """
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return None
        await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.lead_id == lead_id,
                CampaignLeadAssignment.status == "removed",
            )
            .values(
                status=LeadStatus.PENDING.value,
                error_message=None,
                retry_count=0,
                connection_requested_at=None,
                connection_accepted_at=None,
                followup_sent_at=None,
                scheduled_at=None,
            )
        )
        # Mirror the restore on the legacy Lead.status (see remove_lead).
        lead.status = LeadStatus.PENDING
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def skip_lead(self, lead_id: str) -> Lead | None:
        """Skip a lead — valid from PENDING or SCHEDULED in any assignment.

        Phase 3b: writes to CampaignLeadAssignment directly.
        """
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return None
        await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.lead_id == lead_id,
                CampaignLeadAssignment.status.in_(["pending", "scheduled"]),
            )
            .values(
                status=LeadStatus.SKIPPED.value,
                scheduled_at=None,
                error_message="skipped_manually",
            )
        )
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def mark_lead_withdrawn_by_slug(self, slug: str, *, account_id: str) -> bool:
        """
        Mark requested assignments for this exact profile on this account only.
        Returns True if at least one assignment was updated.

        Phase 3b: writes to CampaignLeadAssignment directly.
        """
        canonical = normalize_linkedin_url("https://www.linkedin.com/in/" + slug)
        if not canonical:
            return False
        lead_result = await self.session.execute(
            select(Lead.id).where(
                func.lower(func.rtrim(Lead.linkedin_url, "/")) == canonical,
            )
        )
        lead_ids = [r[0] for r in lead_result.all()]
        if not lead_ids:
            return False
        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.lead_id.in_(lead_ids),
                CampaignLeadAssignment.campaign_id.in_(
                    select(Campaign.id).where(Campaign.account_id == account_id)
                ),
                CampaignLeadAssignment.status == LeadStatus.CONNECTION_REQUESTED.value,
            )
            .values(status=LeadStatus.WITHDRAWN.value)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def requeue_lead(self, lead_id: str) -> Lead | None:
        """Re-queue a lead back to PENDING — valid from ERROR, WITHDRAWN, SKIPPED.

        Phase 3b: writes to CampaignLeadAssignment directly.
        """
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return None
        await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.lead_id == lead_id,
                CampaignLeadAssignment.status.in_(["error", "withdrawn", "skipped"]),
            )
            .values(
                status=LeadStatus.PENDING.value,
                error_message=None,
                retry_count=0,
                scheduled_at=None,
            )
        )
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def bulk_skip_leads(self, lead_ids: list) -> int:
        """Skip multiple leads (PENDING/SCHEDULED → SKIPPED). Returns count updated.

        Phase 3b: writes only to CampaignLeadAssignment.
        """
        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.lead_id.in_(lead_ids),
                CampaignLeadAssignment.status.in_(["pending", "scheduled"]),
            )
            .values(status=LeadStatus.SKIPPED.value, error_message="skipped_manually")
        )
        await self.session.commit()
        return result.rowcount

    async def bulk_remove_leads(self, lead_ids: list) -> int:
        """Soft-delete multiple leads. Returns count updated.

        Writes the removed status to both the CampaignLeadAssignment and the
        legacy Lead.status — the lead detail page and global library read
        Lead.status, so updating only the assignment leaves them looking active.
        """
        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.lead_id.in_(lead_ids),
                CampaignLeadAssignment.status != LeadStatus.REMOVED.value,
            )
            .values(status=LeadStatus.REMOVED.value)
        )
        await self.session.execute(
            update(Lead)
            .where(Lead.id.in_(lead_ids))
            .values(status=LeadStatus.REMOVED)
        )
        await self.session.commit()
        return result.rowcount

    async def bulk_requeue_leads(self, lead_ids: list) -> int:
        """Re-queue multiple leads (ERROR/WITHDRAWN/SKIPPED → PENDING). Returns count updated.

        Phase 3b: writes only to CampaignLeadAssignment.
        """
        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(
                CampaignLeadAssignment.lead_id.in_(lead_ids),
                CampaignLeadAssignment.status.in_(["error", "withdrawn", "skipped"]),
            )
            .values(
                status=LeadStatus.PENDING.value,
                error_message=None, retry_count=0, scheduled_at=None,
            )
        )
        await self.session.commit()
        return result.rowcount

    async def recover_stuck_leads_for_account(
        self, account_id: str, dry_run: bool = False
    ) -> dict:
        """Find ERROR leads caused by DOM/proxy failures and requeue them to PENDING.

        Targets errors safe to retry: profile detection failures
        (send_button_not_found, no_connect_button) and transient network
        errors (proxy connection failures, navigation timeouts). Excludes
        state-machine errors, explicit skips, and uncertain post-send outcomes.

        Returns {"count": N} for dry_run=True, {"recovered": N} otherwise.
        """
        _RECOVERABLE_EXACT = [
            "send_button_not_found",
            "no_connect_button",
            "[profile_dom] no_connect_button",
        ]
        _RECOVERABLE_PREFIXES = [
            "Navigation failed: Page.goto: net::ERR_PROXY_CONNECTION_FAILED",
            "Navigation failed: Page.goto: Timeout",
            "Locator.evaluate: Timeout",
        ]

        campaign_ids_sq = select(Campaign.id).where(
            Campaign.account_id == account_id
        )

        conditions = [
            CampaignLeadAssignment.campaign_id.in_(campaign_ids_sq),
            CampaignLeadAssignment.status == "error",
            or_(
                CampaignLeadAssignment.error_message.in_(_RECOVERABLE_EXACT),
                *[
                    CampaignLeadAssignment.error_message.like(p + "%")
                    for p in _RECOVERABLE_PREFIXES
                ],
            ),
        ]

        if dry_run:
            count = (
                await self.session.execute(
                    select(func.count())
                    .select_from(CampaignLeadAssignment)
                    .where(*conditions)
                )
            ).scalar_one()
            return {"count": count}

        result = await self.session.execute(
            update(CampaignLeadAssignment)
            .where(*conditions)
            .values(
                status=LeadStatus.PENDING.value,
                error_message=None,
                retry_count=0,
                scheduled_at=None,
            )
        )
        await self.session.commit()
        return {"recovered": result.rowcount}

    # ── Global Leads (Lead Library) ───────────────────────────────────────

    async def list_leads_global(
        self,
        page: int = 1,
        per_page: int = 50,
        lead_list_id: str | None = None,
        campaign_id: str | None = None,
        status_filter: str | None = None,
        search: str | None = None,
        sort_by: str | None = None,
        sort_dir: str = "desc",
        last_activity_after: str | None = None,
        last_activity_before: str | None = None,
        skip_reason: str | None = None,
        unassigned_campaign: bool = False,
        unassigned_list: bool = False,
        # Social / enrichment filters
        has_telegram: Optional[bool] = None,
        has_twitter: Optional[bool] = None,
        has_email: Optional[bool] = None,
        tg_contacted: Optional[bool] = None,
        include_archived: bool = False,
    ) -> tuple:
        """Return (leads, total_count) across all lists/campaigns."""
        assignment_query = select(
            CampaignLeadAssignment.lead_id,
            CampaignLeadAssignment.status,
            CampaignLeadAssignment.error_message,
            func.row_number().over(
                partition_by=CampaignLeadAssignment.lead_id,
                order_by=(
                    CampaignLeadAssignment.created_at.desc(),
                    CampaignLeadAssignment.id.desc(),
                ),
            ).label("position"),
        )
        if not include_archived and not campaign_id:
            assignment_query = assignment_query.where(
                CampaignLeadAssignment.campaign_id.in_(_visible_campaign_ids())
            )
        if campaign_id:
            assignment_query = assignment_query.where(
                CampaignLeadAssignment.campaign_id == campaign_id
            )
        ranked_assignments = assignment_query.subquery()
        assignment_join = and_(
            ranked_assignments.c.lead_id == Lead.id,
            ranked_assignments.c.position == 1,
        )
        display_status = func.coalesce(ranked_assignments.c.status, func.lower(Lead.status))
        last_activity_sq = _last_activity_subquery()
        stmt = select(Lead).outerjoin(
            last_activity_sq, last_activity_sq.c.lead_id == Lead.id
        ).outerjoin(ranked_assignments, assignment_join)
        count_stmt = (
            select(func.count())
            .select_from(Lead)
            .outerjoin(last_activity_sq, last_activity_sq.c.lead_id == Lead.id)
            .outerjoin(ranked_assignments, assignment_join)
        )

        list_ids = [s.strip() for s in lead_list_id.split(",") if s.strip()] if lead_list_id else []

        target_is_archived = False
        if campaign_id:
            target_campaign = await self.get_campaign(campaign_id)
            target_account = await self.get_account(target_campaign.account_id) if target_campaign else None
            target_is_archived = bool(target_campaign and (target_campaign.archived or (target_account and target_account.archived)))
        if list_ids:
            target_is_archived = target_is_archived or bool((await self.session.execute(
                select(LeadList.id).where(LeadList.id.in_(list_ids), LeadList.archived == True).limit(1)
            )).scalar_one_or_none())
        if not include_archived and not target_is_archived:
            visible_lead = _visible_lead_condition()
            stmt = stmt.where(visible_lead)
            count_stmt = count_stmt.where(visible_lead)

        if campaign_id:
            # Phase 3b: join via CampaignLeadAssignment since Lead.campaign_id is NULL
            stmt = stmt.join(
                CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id
            ).where(
                CampaignLeadAssignment.campaign_id == campaign_id,
            )
            count_stmt = (
                count_stmt
                .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
                .where(CampaignLeadAssignment.campaign_id == campaign_id)
            )

        if status_filter:
            statuses = [s.strip().lower() for s in status_filter.split(",") if s.strip()]
            if statuses:
                stmt = stmt.where(display_status.in_(statuses))
                count_stmt = count_stmt.where(display_status.in_(statuses))

        if search:
            pattern = _lead_search_pattern(search)
            full_name = func.coalesce(Lead.first_name, "") + " " + func.coalesce(Lead.last_name, "")
            search_filter = or_(
                Lead.first_name.ilike(pattern),
                Lead.last_name.ilike(pattern),
                full_name.ilike(pattern),
                Lead.company.ilike(pattern),
                Lead.title.ilike(pattern),
                Lead.linkedin_url.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if last_activity_after:
            stmt = stmt.where(last_activity_sq.c.last_activity_at >= last_activity_after)
            count_stmt = count_stmt.where(last_activity_sq.c.last_activity_at >= last_activity_after)
        if last_activity_before:
            stmt = stmt.where(last_activity_sq.c.last_activity_at <= last_activity_before)
            count_stmt = count_stmt.where(last_activity_sq.c.last_activity_at <= last_activity_before)

        if skip_reason:
            display_error = case(
                (ranked_assignments.c.lead_id.isnot(None), ranked_assignments.c.error_message),
                else_=Lead.error_message,
            )
            stmt = stmt.where(display_error == skip_reason)
            count_stmt = count_stmt.where(display_error == skip_reason)

        if unassigned_campaign:
            assigned = select(CampaignLeadAssignment.lead_id).where(
                CampaignLeadAssignment.lead_id == Lead.id
            )
            if not include_archived:
                assigned = assigned.where(CampaignLeadAssignment.campaign_id.in_(_visible_campaign_ids()))
            no_cla = not_(exists(assigned))
            stmt = stmt.where(no_cla)
            count_stmt = count_stmt.where(no_cla)

        if list_ids or unassigned_list:
            selected_mem = exists(
                select(LeadListMembership.lead_id).where(
                    LeadListMembership.lead_id == Lead.id,
                    LeadListMembership.lead_list_id.in_(list_ids),
                )
            )
            memberships = select(LeadListMembership.lead_id).where(LeadListMembership.lead_id == Lead.id)
            if not include_archived:
                memberships = memberships.where(LeadListMembership.lead_list_id.in_(
                    select(LeadList.id).where(LeadList.archived == False)
                ))
            no_mem = not_(exists(memberships))
            if list_ids and unassigned_list:
                list_cond = or_(selected_mem, no_mem)
            elif list_ids:
                list_cond = selected_mem
            else:
                list_cond = no_mem
            stmt = stmt.where(list_cond)
            count_stmt = count_stmt.where(list_cond)

        # ── Social / enrichment filters ───────────────────────────────────
        if has_telegram is True:
            stmt = stmt.where(Lead.telegram_username.isnot(None), Lead.telegram_username != "")
            count_stmt = count_stmt.where(Lead.telegram_username.isnot(None), Lead.telegram_username != "")
        elif has_telegram is False:
            stmt = stmt.where(or_(Lead.telegram_username.is_(None), Lead.telegram_username == ""))
            count_stmt = count_stmt.where(or_(Lead.telegram_username.is_(None), Lead.telegram_username == ""))

        if has_twitter is True:
            stmt = stmt.where(Lead.twitter_url.isnot(None), Lead.twitter_url != "")
            count_stmt = count_stmt.where(Lead.twitter_url.isnot(None), Lead.twitter_url != "")
        elif has_twitter is False:
            stmt = stmt.where(or_(Lead.twitter_url.is_(None), Lead.twitter_url == ""))
            count_stmt = count_stmt.where(or_(Lead.twitter_url.is_(None), Lead.twitter_url == ""))

        if has_email is True:
            stmt = stmt.where(Lead.email.isnot(None), Lead.email != "")
            count_stmt = count_stmt.where(Lead.email.isnot(None), Lead.email != "")
        elif has_email is False:
            stmt = stmt.where(or_(Lead.email.is_(None), Lead.email == ""))
            count_stmt = count_stmt.where(or_(Lead.email.is_(None), Lead.email == ""))

        if tg_contacted is True:
            stmt = stmt.where(Lead.tg_contacted_at.isnot(None))
            count_stmt = count_stmt.where(Lead.tg_contacted_at.isnot(None))
        elif tg_contacted is False:
            stmt = stmt.where(Lead.tg_contacted_at.is_(None))
            count_stmt = count_stmt.where(Lead.tg_contacted_at.is_(None))

        total = (await self.session.execute(count_stmt)).scalar_one()

        _SORT_COLS = {
            "name": Lead.first_name,
            "company": Lead.company,
            "status": display_status,
            "last_activity_at": last_activity_sq.c.last_activity_at,
            "created_at": Lead.created_at,
        }
        col = _SORT_COLS.get(sort_by or "created_at", Lead.created_at)
        order_col = col.desc() if sort_dir == "desc" else col.asc()
        stmt = (
            stmt.add_columns(last_activity_sq.c.last_activity_at)
            .order_by(order_col)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self.session.execute(stmt)
        leads = []
        for lead, last_activity_at in result.all():
            lead.last_activity_at = last_activity_at
            leads.append(lead)
        return leads, total

    async def get_lead_list_stats(self, list_id: str) -> dict:
        """Return quality/coverage stats for all leads in a list."""
        from sqlalchemy import func, case
        from releasi.db.models import LeadListMembership, LeadStatus

        # Join memberships → leads
        stmt = (
            select(
                func.count(Lead.id).label("total"),
                func.sum(
                    case((Lead.telegram_username.isnot(None), 1), else_=0)
                ).label("tg_count"),
                func.sum(
                    case((Lead.tg_contacted_at.isnot(None), 1), else_=0)
                ).label("tg_contacted_count"),
                func.sum(
                    case((Lead.email.isnot(None), 1), else_=0)
                ).label("email_count"),
                func.sum(
                    case((Lead.twitter_url.isnot(None), 1), else_=0)
                ).label("twitter_count"),
                func.sum(
                    case((Lead.status == LeadStatus.CONNECTED.value, 1), else_=0)
                ).label("connected_count"),
                func.sum(
                    case((Lead.status.in_([
                        LeadStatus.CONNECTION_REQUESTED.value,
                        LeadStatus.CONNECTED.value,
                        LeadStatus.FOLLOWUP_SENT.value,
                        LeadStatus.COMPLETED.value,
                    ]), 1), else_=0)
                ).label("requested_count"),
            )
            .join(LeadListMembership, LeadListMembership.lead_id == Lead.id)
            .where(LeadListMembership.lead_list_id == list_id)
        )
        row = (await self.session.execute(stmt)).one()
        total = row.total or 0
        tg_count = row.tg_count or 0
        connected = row.connected_count or 0
        requested = row.requested_count or 0

        def pct(n: int, d: int) -> float:
            return round(n / d * 100, 1) if d else 0.0

        return {
            "total": total,
            "acceptance_rate": pct(connected, requested),
            "tg_coverage": pct(tg_count, total),
            "email_coverage": pct(row.email_count or 0, total),
            "twitter_coverage": pct(row.twitter_count or 0, total),
            "tg_contacted_rate": pct(row.tg_contacted_count or 0, tg_count),
        }

    async def log_lead_event(
        self,
        lead_id: str,
        event_type: str,
        details: Optional[dict] = None,
        actor_user_id: Optional[str] = None,
    ) -> LeadEvent:
        """Write a lightweight event to lead_events (no account_id required).

        ``actor_user_id`` attributes a human-driven action to a dashboard user;
        leave None for automated/system events (TG sweep, scheduler).
        """
        event = LeadEvent(
            lead_id=lead_id,
            event_type=event_type,
            details=details,
            actor_user_id=actor_user_id,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    # ── Lead notes (HubSpot-style multi-note) ──────────────────────────────

    async def list_lead_notes(self, lead_id: str) -> List["LeadNote"]:
        """Return all notes for a lead, newest-first."""
        from releasi.db.models import LeadNote

        result = await self.session.execute(
            select(LeadNote)
            .where(LeadNote.lead_id == lead_id)
            .order_by(LeadNote.created_at.desc())
        )
        return list(result.scalars().all())

    async def add_lead_note(
        self, lead_id: str, body: str, actor_user_id: Optional[str] = None
    ) -> "LeadNote":
        """Create a new note for a lead, attributed to ``actor_user_id`` if known."""
        from releasi.db.models import LeadNote

        note = LeadNote(lead_id=lead_id, body=body, actor_user_id=actor_user_id)
        self.session.add(note)
        await self.session.flush()
        return note

    async def get_lead_note(self, note_id: str) -> Optional["LeadNote"]:
        from releasi.db.models import LeadNote

        return await self.session.get(LeadNote, note_id)

    async def update_lead_note(self, note: "LeadNote", body: str) -> "LeadNote":
        note.body = body
        await self.session.flush()
        return note

    async def delete_lead_note(self, note: "LeadNote") -> None:
        await self.session.delete(note)
        await self.session.flush()

    async def get_next_lead_for_tg_sweep(self) -> Optional[Lead]:
        """Return the next lead to enrich with Telegram, or None if nothing is queued.

        Eligibility:
          - telegram_username IS NULL (not already found)
          - no tg_sweep_searched event in lead_events (never searched before)
          - at least one of the lead's lists has tg_enrich_enabled=True
            (checked via legacy lead_list_id FK or LeadListMembership rows)
          - must have first_name (need a real person name to search)

        Newest imports are processed first so fresh fundraising leads are enriched
        before older backlog entries.
        """
        already_searched = (
            select(LeadEvent.id)
            .where(LeadEvent.lead_id == Lead.id)
            .where(LeadEvent.event_type == "tg_sweep_searched")
            .exists()
        )
        legacy_enabled = (
            select(LeadList.id)
            .where(LeadList.id == Lead.lead_list_id)
            .where(LeadList.tg_enrich_enabled == True)  # noqa: E712
            .exists()
        )
        membership_enabled = (
            select(LeadListMembership.lead_list_id)
            .join(LeadList, LeadList.id == LeadListMembership.lead_list_id)
            .where(LeadListMembership.lead_id == Lead.id)
            .where(LeadList.tg_enrich_enabled == True)  # noqa: E712
            .exists()
        )
        stmt = (
            select(Lead)
            .where(Lead.telegram_username.is_(None))
            .where(not_(already_searched))
            .where(or_(legacy_enabled, membership_enabled))
            .where(Lead.first_name.isnot(None))
            .order_by(Lead.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_tg_sweep_stats(self) -> dict:
        """Stats for the Telegram enrichment sweep dashboard panel.

        Returns pending_count (eligible but not yet searched), searched_count
        (ever attempted), and found_count (found via sweep — source='sweep').
        """
        # Reuse the same eligibility predicates as get_next_lead_for_tg_sweep
        already_searched = (
            select(LeadEvent.id)
            .where(LeadEvent.lead_id == Lead.id)
            .where(LeadEvent.event_type == "tg_sweep_searched")
            .exists()
        )
        legacy_enabled = (
            select(LeadList.id)
            .where(LeadList.id == Lead.lead_list_id)
            .where(LeadList.tg_enrich_enabled == True)  # noqa: E712
            .exists()
        )
        membership_enabled = (
            select(LeadListMembership.lead_list_id)
            .join(LeadList, LeadList.id == LeadListMembership.lead_list_id)
            .where(LeadListMembership.lead_id == Lead.id)
            .where(LeadList.tg_enrich_enabled == True)  # noqa: E712
            .exists()
        )
        pending_r = await self.session.execute(
            select(func.count())
            .select_from(Lead)
            .where(Lead.telegram_username.is_(None))
            .where(not_(already_searched))
            .where(or_(legacy_enabled, membership_enabled))
            .where(Lead.first_name.isnot(None))
        )
        pending_count = pending_r.scalar_one()

        searched_r = await self.session.execute(
            select(func.count(func.distinct(LeadEvent.lead_id)))
            .where(LeadEvent.event_type == "tg_sweep_searched")
        )
        searched_count = searched_r.scalar_one()

        # Count leads found via sweep specifically (details->source == "sweep")
        found_r = await self.session.execute(
            select(func.count(func.distinct(LeadEvent.lead_id)))
            .where(LeadEvent.event_type == "telegram_found")
            .where(func.json_extract(LeadEvent.details, "$.source") == "sweep")
        )
        found_count = found_r.scalar_one()

        return {
            "pending_count": pending_count,
            "searched_count": searched_count,
            "found_count": found_count,
        }

    async def get_recent_tg_sweep_results(self, limit: int = 20) -> list:
        """Return the last `limit` tg_sweep_searched events with lead info."""
        stmt = (
            select(
                LeadEvent.lead_id,
                LeadEvent.details,
                LeadEvent.created_at,
                Lead.first_name,
                Lead.last_name,
                Lead.company,
                Lead.telegram_username,
            )
            .join(Lead, Lead.id == LeadEvent.lead_id)
            .where(LeadEvent.event_type == "tg_sweep_searched")
            .order_by(LeadEvent.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            {
                "lead_id": str(row.lead_id),
                "lead_name": f"{row.first_name or ''} {row.last_name or ''}".strip(),
                "company": row.company,
                "found": bool(row.telegram_username),
                "telegram_username": row.telegram_username,
                "searched_at": row.created_at.isoformat(),
                "match": (row.details or {}).get("match"),
            }
            for row in rows
        ]

    async def list_activity(
        self,
        page: int = 1,
        per_page: int = 50,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        sources: Optional[list] = None,
        event_types: Optional[list] = None,
        account_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        include_archived: bool = False,
    ) -> tuple[list[dict], int]:
        """Return a merged, time-sorted activity feed from action_log + lead_events.

        Returns (items, total_count). Each item is a plain dict ready to be
        converted to ActivityItem. Resolution of account/campaign/lead names is
        done here to avoid N+1 queries in the route.

        ``actor_user_id`` filters to manual actions performed by one dashboard
        user (lead_events + notes only — action_log has no human actor).
        """
        from releasi.db.models import Campaign, LeadNote, User

        include_action_log  = not sources or "action_log" in sources
        include_lead_events = not sources or "lead_events" in sources
        include_notes       = not sources or "lead_notes" in sources

        # Filtering by a person only makes sense for human-attributed rows;
        # action_log entries are account/scheduler actions, not user actions.
        if actor_user_id:
            include_action_log = False

        # Manual events (lead_events + notes) are not account/campaign-scoped —
        # they carry no account_id. When the feed is filtered by account or
        # campaign, the user wants that scope's LinkedIn actions only, so the
        # unscoped manual events are excluded. Otherwise e.g. Telegram outreach
        # leaks into every account's filtered view — including access-only
        # accounts like Ibrahim, making it look mis-attributed.
        if account_id or campaign_id:
            include_lead_events = False
            include_notes = False

        # Notes surface as a synthetic "note_added" event_type, so honour an
        # explicit event_types filter that doesn't ask for them.
        if include_notes and event_types and "note_added" not in event_types:
            include_notes = False

        items: list[dict] = []

        # ── action_log ────────────────────────────────────────────────────────
        if include_action_log:
            stmt = select(ActionLog).order_by(ActionLog.created_at.desc())
            if not include_archived:
                stmt = stmt.where(
                    or_(ActionLog.lead_id.is_(None), ActionLog.lead_id.in_(_visible_lead_ids())),
                    ActionLog.account_id.in_(select(Account.id).where(Account.archived == False)),
                    or_(ActionLog.campaign_id.is_(None), ActionLog.campaign_id.in_(_visible_campaign_ids())),
                )
            if since:
                stmt = stmt.where(ActionLog.created_at >= since)
            if until:
                stmt = stmt.where(ActionLog.created_at <= until)
            if account_id:
                stmt = stmt.where(ActionLog.account_id == account_id)
            if campaign_id:
                stmt = stmt.where(ActionLog.campaign_id == campaign_id)
            if event_types:
                stmt = stmt.where(ActionLog.action_type.in_(event_types))
            # Fetch enough for the page; we'll merge and re-sort below
            stmt = stmt.limit(per_page * page * 2)
            rows = (await self.session.execute(stmt)).scalars().all()

            # Collect unique IDs for name resolution
            acct_ids  = {r.account_id for r in rows if r.account_id}
            camp_ids  = {r.campaign_id for r in rows if r.campaign_id}
            lead_ids  = {r.lead_id for r in rows if r.lead_id}

            acct_names: dict[str, str] = {}
            camp_names: dict[str, str] = {}
            lead_names: dict[str, str] = {}

            if acct_ids:
                acct_rows = (await self.session.execute(
                    select(Account.id, Account.name).where(Account.id.in_(acct_ids))
                )).all()
                acct_names = {r.id: r.name for r in acct_rows}
            if camp_ids:
                camp_rows = (await self.session.execute(
                    select(Campaign.id, Campaign.name).where(Campaign.id.in_(camp_ids))
                )).all()
                camp_names = {r.id: r.name for r in camp_rows}
            if lead_ids:
                lead_rows = (await self.session.execute(
                    select(Lead.id, Lead.first_name, Lead.last_name).where(Lead.id.in_(lead_ids))
                )).all()
                lead_names = {
                    r.id: f"{r.first_name or ''} {r.last_name or ''}".strip()
                    for r in lead_rows
                }

            for r in rows:
                items.append({
                    "id": r.id,
                    "source": "action_log",
                    "event_type": r.action_type.value if hasattr(r.action_type, "value") else str(r.action_type),
                    "created_at": r.created_at,
                    "account_id": r.account_id,
                    "account_name": acct_names.get(r.account_id) if r.account_id else None,
                    "campaign_id": r.campaign_id,
                    "campaign_name": camp_names.get(r.campaign_id) if r.campaign_id else None,
                    "lead_id": r.lead_id,
                    "lead_name": lead_names.get(r.lead_id) if r.lead_id else None,
                    "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                    "details": r.details,
                })

        # Resolve dashboard-user display names for manual-action attribution.
        async def _actor_names(ids: set) -> dict:
            ids = {i for i in ids if i}
            if not ids:
                return {}
            urows = (await self.session.execute(
                select(User.id, User.display_name, User.handle).where(User.id.in_(ids))
            )).all()
            return {u.id: (u.display_name or u.handle) for u in urows}

        # ── lead_events ───────────────────────────────────────────────────────
        if include_lead_events:
            stmt2 = select(LeadEvent).order_by(LeadEvent.created_at.desc())
            if not include_archived:
                stmt2 = stmt2.where(LeadEvent.lead_id.in_(_visible_lead_ids()))
            if since:
                stmt2 = stmt2.where(LeadEvent.created_at >= since)
            if until:
                stmt2 = stmt2.where(LeadEvent.created_at <= until)
            if event_types:
                stmt2 = stmt2.where(LeadEvent.event_type.in_(event_types))
            if actor_user_id:
                stmt2 = stmt2.where(LeadEvent.actor_user_id == actor_user_id)
            # account_id / campaign_id filters don't apply to lead_events
            stmt2 = stmt2.limit(per_page * page * 2)
            rows2 = (await self.session.execute(stmt2)).scalars().all()

            le_lead_ids = {r.lead_id for r in rows2}
            le_lead_names: dict[str, str] = {}
            if le_lead_ids:
                le_lead_rows = (await self.session.execute(
                    select(Lead.id, Lead.first_name, Lead.last_name).where(Lead.id.in_(le_lead_ids))
                )).all()
                le_lead_names = {
                    r.id: f"{r.first_name or ''} {r.last_name or ''}".strip()
                    for r in le_lead_rows
                }
            le_actor_names = await _actor_names({r.actor_user_id for r in rows2})

            for r in rows2:
                items.append({
                    "id": r.id,
                    "source": "lead_event",
                    "event_type": r.event_type,
                    "created_at": r.created_at,
                    "account_id": None,
                    "account_name": None,
                    "campaign_id": None,
                    "campaign_name": None,
                    "lead_id": r.lead_id,
                    "lead_name": le_lead_names.get(r.lead_id),
                    "status": None,
                    "details": r.details,
                    "actor_user_id": r.actor_user_id,
                    "actor_name": le_actor_names.get(r.actor_user_id),
                })

        # ── lead_notes (manual notes) ─────────────────────────────────────────
        # Surfaced as synthetic "note_added" events so manually-left notes show
        # up in the feed alongside Telegram outreach. Uses updated_at when the
        # note was edited after creation so edits float to the top.
        if include_notes:
            nstmt = select(LeadNote).order_by(LeadNote.created_at.desc())
            if not include_archived:
                nstmt = nstmt.where(LeadNote.lead_id.in_(_visible_lead_ids()))
            if since:
                nstmt = nstmt.where(LeadNote.created_at >= since)
            if until:
                nstmt = nstmt.where(LeadNote.created_at <= until)
            if actor_user_id:
                nstmt = nstmt.where(LeadNote.actor_user_id == actor_user_id)
            nstmt = nstmt.limit(per_page * page * 2)
            nrows = (await self.session.execute(nstmt)).scalars().all()

            n_lead_ids = {r.lead_id for r in nrows}
            n_lead_names: dict[str, str] = {}
            if n_lead_ids:
                n_lead_rows = (await self.session.execute(
                    select(Lead.id, Lead.first_name, Lead.last_name).where(Lead.id.in_(n_lead_ids))
                )).all()
                n_lead_names = {
                    r.id: f"{r.first_name or ''} {r.last_name or ''}".strip()
                    for r in n_lead_rows
                }
            n_actor_names = await _actor_names({r.actor_user_id for r in nrows})

            for r in nrows:
                edited = bool(r.updated_at and r.updated_at != r.created_at)
                items.append({
                    "id": r.id,
                    "source": "lead_note",
                    "event_type": "note_added",
                    "created_at": r.updated_at if edited else r.created_at,
                    "account_id": None,
                    "account_name": None,
                    "campaign_id": None,
                    "campaign_name": None,
                    "lead_id": r.lead_id,
                    "lead_name": n_lead_names.get(r.lead_id),
                    "status": "edited" if edited else None,
                    "details": {"body": r.body, "edited": edited},
                    "actor_user_id": r.actor_user_id,
                    "actor_name": n_actor_names.get(r.actor_user_id),
                })

        # Sort merged results, paginate, return total
        items.sort(key=lambda x: x["created_at"], reverse=True)
        total = len(items)
        offset = (page - 1) * per_page
        return items[offset : offset + per_page], total

    async def get_scraper_cookie(self, site: str) -> Optional[ScraperCookie]:
        result = await self.session.execute(
            select(ScraperCookie).where(ScraperCookie.site == site)
        )
        return result.scalar_one_or_none()

    async def upsert_scraper_cookie(
        self, site: str, cookies_json: str, captured_at: datetime
    ) -> ScraperCookie:
        row = await self.get_scraper_cookie(site)
        if row:
            row.cookies_json = cookies_json
            row.captured_at = captured_at
        else:
            row = ScraperCookie(site=site, cookies_json=cookies_json, captured_at=captured_at)
            self.session.add(row)
        await self.session.commit()
        return row

    async def touch_scraper_cookie(self, site: str) -> None:
        """Update last_used_at for a scraper cookie row."""
        row = await self.get_scraper_cookie(site)
        if row:
            row.last_used_at = datetime.utcnow()
            await self.session.commit()

    # ── Users (auth + attribution) ─────────────────────────────────────────

    async def count_users(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(User))
        return int(result.scalar_one())

    async def get_user(self, user_id: str) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_user_by_handle(self, handle: str) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.handle == handle.lower())
        )
        return result.scalar_one_or_none()

    async def list_users(self) -> Sequence[User]:
        result = await self.session.execute(
            select(User).order_by(User.created_at.asc())
        )
        return result.scalars().all()

    async def create_user(
        self,
        *,
        handle: str,
        password_hash: str,
        display_name: Optional[str] = None,
        is_superadmin: bool = False,
    ) -> User:
        user = User(
            handle=handle.lower(),
            display_name=display_name,
            password_hash=password_hash,
            is_superadmin=is_superadmin,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update_user(self, user: User, **kwargs) -> User:
        for k, v in kwargs.items():
            if hasattr(user, k):
                setattr(user, k, v)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def touch_user_last_seen(self, user_id: str) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(last_seen_at=datetime.utcnow())
        )
        await self.session.commit()

    # ── Broadcasts ─────────────────────────────────────────────────────────

    async def create_broadcast(
        self,
        account_id: str,
        name: str,
        message_1: Optional[str] = None,
        message_2: Optional[str] = None,
        message_3: Optional[str] = None,
        source_list_id: Optional[str] = None,
        delay_between_hours: float = 24,
        weekend_enabled: bool = False,
        conversation_routing: str = "skip",
        message_prior_only: Optional[str] = None,
        message_prior_only_2: Optional[str] = None,
        message_prior_only_3: Optional[str] = None,
        status: BroadcastStatus = BroadcastStatus.DRAFT,
    ) -> Broadcast:
        broadcast = Broadcast(
            account_id=account_id,
            name=name,
            source_list_id=source_list_id,
            message_1=message_1,
            message_2=message_2,
            message_3=message_3,
            delay_between_hours=delay_between_hours,
            weekend_enabled=weekend_enabled,
            conversation_routing=conversation_routing,
            message_prior_only=message_prior_only,
            message_prior_only_2=message_prior_only_2,
            message_prior_only_3=message_prior_only_3,
            status=status,
        )
        self.session.add(broadcast)
        await self.session.commit()
        await self.session.refresh(broadcast)
        return broadcast

    async def get_broadcast(self, broadcast_id: str) -> Optional[Broadcast]:
        return await self.session.get(Broadcast, broadcast_id)

    async def list_broadcasts(
        self,
        account_id: Optional[str] = None,
        include_archived: bool = False,
    ) -> Sequence[Broadcast]:
        query = select(Broadcast)
        if account_id:
            query = query.where(Broadcast.account_id == account_id)
        if not include_archived:
            query = query.where(Broadcast.archived == False)  # noqa: E712
        query = query.order_by(Broadcast.created_at.desc())
        result = await self.session.execute(query)
        return result.scalars().all()

    async def update_broadcast(self, broadcast: Broadcast, **kwargs) -> Broadcast:
        # paused_at is set when transitioning ACTIVE → PAUSED (mirrors Campaign)
        if kwargs.get("status") == BroadcastStatus.PAUSED and broadcast.status == BroadcastStatus.ACTIVE:
            if broadcast.paused_at is None:
                broadcast.paused_at = datetime.utcnow()
        for k, v in kwargs.items():
            if hasattr(broadcast, k):
                setattr(broadcast, k, v)
        await self.session.commit()
        await self.session.refresh(broadcast)
        return broadcast

    async def delete_broadcast(self, broadcast_id: str) -> bool:
        """Hard-delete a broadcast and its snapshot rows.

        Explicit child cleanup — this codebase's SQLite engine does not enable
        FK enforcement, so ON DELETE CASCADE declared in the schema is
        informational only. Deletes broadcast_leads first, then the broadcast.

        Use archive (broadcast.archived=True) for soft-delete instead when a
        record of the broadcast's sends should be preserved.
        """
        from sqlalchemy import delete as sa_delete

        broadcast = await self.session.get(Broadcast, broadcast_id)
        if broadcast is None:
            return False
        await self.session.execute(
            sa_delete(BroadcastLead).where(BroadcastLead.broadcast_id == broadcast_id)
        )
        await self.session.delete(broadcast)
        await self.session.commit()
        return True

    async def snapshot_leads_into_broadcast(
        self,
        broadcast_id: str,
        lead_ids: Sequence[str],
    ) -> int:
        """Create BroadcastLead rows for each lead_id.

        Idempotent: leads already in this broadcast are skipped (relies on
        UNIQUE(broadcast_id, lead_id)). Only leads that actually exist in
        the leads table are snapshotted — silently drops unknown IDs.

        Returns the number of new snapshot rows created.
        """
        if not lead_ids:
            return 0

        # De-duplicate the input to avoid unique-constraint noise on retries
        # driven by a caller that sent the same lead twice.
        unique_ids = list(dict.fromkeys(lead_ids))

        # Filter to leads that actually exist AND aren't already in this broadcast.
        existing_lead_rows = await self.session.execute(
            select(Lead.id).where(Lead.id.in_(unique_ids))
        )
        existing_lead_ids = set(existing_lead_rows.scalars().all())

        already_snapshotted_rows = await self.session.execute(
            select(BroadcastLead.lead_id).where(
                BroadcastLead.broadcast_id == broadcast_id,
                BroadcastLead.lead_id.in_(unique_ids),
            )
        )
        already_snapshotted = set(already_snapshotted_rows.scalars().all())

        to_insert = [lid for lid in unique_ids if lid in existing_lead_ids and lid not in already_snapshotted]
        if not to_insert:
            return 0

        # Count BEFORE add_all — any SELECT after add_all triggers autoflush,
        # which would make the count include the just-added rows and cause a
        # double-count when we add len(to_insert) on top.
        count_result = await self.session.execute(
            select(func.count()).select_from(BroadcastLead).where(
                BroadcastLead.broadcast_id == broadcast_id
            )
        )
        current_count = count_result.scalar_one() or 0

        rows = [
            BroadcastLead(broadcast_id=broadcast_id, lead_id=lid, status="pending")
            for lid in to_insert
        ]
        self.session.add_all(rows)

        broadcast = await self.session.get(Broadcast, broadcast_id)
        if broadcast is not None:
            broadcast.total_leads = current_count + len(to_insert)

        await self.session.commit()
        return len(to_insert)

    async def snapshot_lead_list_into_broadcast(
        self,
        broadcast_id: str,
        source_list_id: str,
    ) -> int:
        """Snapshot every lead currently in a list into a broadcast.

        Uses LeadListMembership (Phase-2 source of truth). Idempotent: already-
        snapshotted leads are skipped.
        """
        lead_ids_result = await self.session.execute(
            select(LeadListMembership.lead_id).where(
                LeadListMembership.lead_list_id == source_list_id
            )
        )
        lead_ids = list(lead_ids_result.scalars().all())
        if not lead_ids:
            return 0
        return await self.snapshot_leads_into_broadcast(broadcast_id, lead_ids)

    async def snapshot_lead_lists_into_broadcast(
        self,
        broadcast_id: str,
        source_list_ids: list,
    ) -> int:
        """Snapshot the union of leads across multiple lists into a broadcast.

        Deduplicates lead IDs before delegating to snapshot_leads_into_broadcast.
        """
        if not source_list_ids:
            return 0
        lead_ids_result = await self.session.execute(
            select(LeadListMembership.lead_id).where(
                LeadListMembership.lead_list_id.in_(source_list_ids)
            )
        )
        lead_ids = list({lid for lid in lead_ids_result.scalars().all()})
        if not lead_ids:
            return 0
        return await self.snapshot_leads_into_broadcast(broadcast_id, lead_ids)

    async def get_broadcast_lead_by_id(self, broadcast_lead_id: str) -> Optional[BroadcastLead]:
        return await self.session.get(BroadcastLead, broadcast_lead_id)

    async def update_broadcast_lead(
        self, broadcast_lead: BroadcastLead, **kwargs
    ) -> BroadcastLead:
        for k, v in kwargs.items():
            if hasattr(broadcast_lead, k):
                setattr(broadcast_lead, k, v)
        await self.session.commit()
        await self.session.refresh(broadcast_lead)
        return broadcast_lead

    async def get_pending_broadcast_leads(
        self,
        broadcast_id: str,
        limit: int = 50,
    ) -> Sequence[BroadcastLead]:
        """Leads awaiting their FIRST message send.

        status='pending' — never touched. Ordered by created_at so the
        dispatcher walks the snapshot in insertion order.
        """
        result = await self.session.execute(
            select(BroadcastLead)
            .where(
                BroadcastLead.broadcast_id == broadcast_id,
                BroadcastLead.status == "pending",
                BroadcastLead.lead_id.in_(_visible_lead_ids()),
            )
            .order_by(BroadcastLead.created_at)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_due_broadcast_leads_for_next_message(
        self,
        broadcast_id: str,
        before: Optional[datetime] = None,
        limit: int = 50,
    ) -> Sequence[BroadcastLead]:
        """Leads awaiting message 2 or 3 in the sequence.

        status='sent' means at least one message has been sent successfully.
        next_message_at is set by the executor after each successful send
        to (now + delay_between_hours). Once last_message_index reaches the
        configured sequence length, status flips to 'sequence_complete' and
        this query stops returning the row.
        """
        cutoff = before or datetime.utcnow()
        result = await self.session.execute(
            select(BroadcastLead)
            .where(
                BroadcastLead.broadcast_id == broadcast_id,
                BroadcastLead.status == "sent",
                BroadcastLead.next_message_at != None,  # noqa: E711
                BroadcastLead.next_message_at <= cutoff,
                BroadcastLead.lead_id.in_(_visible_lead_ids()),
            )
            .order_by(BroadcastLead.next_message_at)
            .limit(limit)
        )
        return result.scalars().all()

    async def list_broadcast_leads_paginated(
        self,
        broadcast_id: str,
        limit: int = 50,
        offset: int = 0,
        status_filter: Optional[str] = None,
        include_archived: bool = False,
    ) -> tuple[Sequence[BroadcastLead], int]:
        """For the broadcast detail page's per-lead table.

        Returns (rows, total_count). total_count applies the same status
        filter so pagination reflects the filtered set.
        """
        base_where = [BroadcastLead.broadcast_id == broadcast_id]
        if not include_archived:
            base_where.append(BroadcastLead.lead_id.in_(_visible_lead_ids()))
        if status_filter:
            base_where.append(BroadcastLead.status == status_filter)

        rows_result = await self.session.execute(
            select(BroadcastLead)
            .where(*base_where)
            .order_by(BroadcastLead.created_at)
            .limit(limit)
            .offset(offset)
        )
        rows = rows_result.scalars().all()

        count_result = await self.session.execute(
            select(func.count()).select_from(BroadcastLead).where(*base_where)
        )
        total = count_result.scalar_one() or 0
        return rows, total

    async def get_broadcast_status_counts(self, broadcast_id: str, include_archived: bool = False) -> dict[str, int]:
        """Bucket counts for the broadcast detail header (pending / sent / ...)."""
        result = await self.session.execute(
            select(BroadcastLead.status, func.count())
            .where(BroadcastLead.broadcast_id == broadcast_id)
            .where(True if include_archived else BroadcastLead.lead_id.in_(_visible_lead_ids()))
            .group_by(BroadcastLead.status)
        )
        return {status: count for status, count in result.all()}

    async def list_active_broadcasts(
        self, account_id: Optional[str] = None
    ) -> Sequence[Broadcast]:
        """Broadcasts the dispatcher should consider this cycle.

        Excludes archived and non-active statuses so paused broadcasts don't
        get dispatched.
        """
        query = select(Broadcast).where(
            Broadcast.status == BroadcastStatus.ACTIVE,
            Broadcast.archived == False,  # noqa: E712
        )
        if account_id:
            query = query.where(Broadcast.account_id == account_id)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_direct_messages_sent_today(
        self, account_id: str, stat_date: Optional[date] = None
    ) -> int:
        """Today's direct_messages_sent count from DailyStat.

        Counts broadcast messages specifically (not follow-ups). The daily
        message limit is enforced against the SUM of this + follow-ups.
        """
        stat_date = stat_date or date.today()
        result = await self.session.execute(
            select(func.coalesce(DailyStat.direct_messages_sent, 0))
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date == stat_date,
            )
        )
        return result.scalar_one_or_none() or 0

    async def get_distinct_message_recipients_today(
        self, account_id: str, at: Optional[datetime] = None
    ) -> int:
        """Distinct leads that received a message today (broadcast OR follow-up).

        This is the unified counter enforced against Account.daily_message_limit.
        Semantics: the cap is per lead-per-day, not per message — a 3-message
        broadcast sequence to one person counts as one. Preserves the historical
        follow-up cap semantics (which also counted distinct sequences, not
        individual messages) while extending it to include broadcast sends.
        """
        now = at or datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(func.count(func.distinct(ActionLog.lead_id)))
            .where(
                ActionLog.account_id == account_id,
                ActionLog.action_type.in_(
                    [ActionType.DIRECT_MESSAGE, ActionType.FOLLOWUP_MESSAGE]
                ),
                ActionLog.status == ActionLogStatus.SUCCESS,
                ActionLog.created_at >= today_start,
                ActionLog.lead_id != None,  # noqa: E711
            )
        )
        return result.scalar_one() or 0

    async def account_has_broadcast_work(
        self,
        account_id: str,
        now: Optional[datetime] = None,
    ) -> bool:
        """Pre-dispatch check: does any active broadcast for this account
        have work to do right now?

        Cheap SQL — avoids acquiring the browser pool if nothing is due.
        Considers both pending (never-touched) leads and due
        next-in-sequence sends.
        """
        cutoff = now or datetime.utcnow()
        result = await self.session.execute(
            select(func.count()).select_from(BroadcastLead)
            .join(Broadcast, Broadcast.id == BroadcastLead.broadcast_id)
            .where(
                Broadcast.account_id == account_id,
                Broadcast.status == BroadcastStatus.ACTIVE,
                Broadcast.archived == False,  # noqa: E712
                or_(
                    BroadcastLead.status == "pending",
                    and_(
                        BroadcastLead.status == "sent",
                        BroadcastLead.next_message_at != None,  # noqa: E711
                        BroadcastLead.next_message_at <= cutoff,
                    ),
                ),
            )
        )
        return (result.scalar_one() or 0) > 0

    async def get_broadcast_leads_for_lead(
        self, lead_id: str
    ) -> Sequence[BroadcastLead]:
        """All BroadcastLead rows for a lead across broadcasts.

        Used by the lead detail Outreach panel to show broadcast membership
        + per-broadcast state.
        """
        result = await self.session.execute(
            select(BroadcastLead)
            .where(BroadcastLead.lead_id == lead_id)
            .order_by(BroadcastLead.created_at.desc())
        )
        return result.scalars().all()

    async def count_broadcast_sent_leads(self, broadcast_id: str) -> int:
        """Count broadcast_leads that have had at least one message successfully sent.

        Uses last_message_index IS NOT NULL as the signal — this survives status
        resets (error→pending, manual_outreach→pending) that would otherwise make
        the trial counter think these leads were never contacted.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(BroadcastLead)
            .where(
                BroadcastLead.broadcast_id == broadcast_id,
                BroadcastLead.last_message_index.isnot(None),
            )
        )
        return result.scalar_one() or 0

    async def mark_broadcast_completed_if_done(self, broadcast_id: str) -> bool:
        """Flip broadcast to COMPLETED when every lead has terminated.

        Terminal states for a broadcast lead: sequence_complete, skipped, error.
        Returns True if the status was changed.
        """
        broadcast = await self.session.get(Broadcast, broadcast_id)
        if broadcast is None or broadcast.status != BroadcastStatus.ACTIVE:
            return False

        counts_result = await self.session.execute(
            select(BroadcastLead.status, func.count())
            .where(BroadcastLead.broadcast_id == broadcast_id)
            .group_by(BroadcastLead.status)
        )
        counts = {s: c for s, c in counts_result.all()}
        if not counts:
            return False  # empty broadcast — leave it alone

        terminal = {"sequence_complete", "skipped", "error"}
        outstanding = sum(c for s, c in counts.items() if s not in terminal)
        if outstanding > 0:
            return False

        broadcast.status = BroadcastStatus.COMPLETED
        await self.session.commit()
        return True
