"""Campaign endpoints."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo
from releasi.api.schemas import CampaignOut, CampaignCreate, CampaignUpdate, CampaignStatsResponse, CloneCampaignRequest, ReorderListsRequest
from releasi.db.models import CampaignStatus
from releasi.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])

# In-process task registry for acceptance catchup jobs. Mirrors the pattern
# used by the invitation-withdrawal endpoint in routes/accounts.py.
# Format: { task_id: { status, accepted_count, scanned_slugs, cutoff_hours,
#                      hit_cutoff, hit_iteration_cap, skipped_reason, error } }
_catchup_tasks: dict = {}


async def _enrich_campaign(
    repo: Repository, campaign, include_archived: bool = False,
) -> CampaignOut:
    """Convert a Campaign ORM object to CampaignOut with status counts."""
    out = CampaignOut.model_validate(campaign)
    account = await repo.get_account(campaign.account_id)
    include_archived = include_archived or campaign.archived or bool(account and account.archived)
    out.status_counts = await repo.get_campaign_status_counts(campaign.id, include_archived=include_archived)
    out.total_leads = sum(out.status_counts.values())
    # Add account name
    if account:
        out.account_name = account.name
        out.account_status = account.status.value if hasattr(account.status, 'value') else account.status
        out.account_paused_until = account.paused_until
        out.account_timezone = account.timezone
        out.account_dispatch_mode = account.dispatch_mode
        if account.dispatch_mode == "continuous":
            sent_today = await repo.get_daily_requests_sent(account.id)
            pending_count = out.status_counts.get("pending", 0) if out.status_counts else 0
            remaining_budget = max(0, account.daily_limit - sent_today)
            out.estimated_remaining_today = min(pending_count, remaining_budget)
    # Add assigned lead lists
    links = await repo.get_campaign_lists(campaign.id, include_archived=include_archived)
    assigned = []
    for link in links:
        ll = await repo.get_lead_list(link.lead_list_id)
        if ll:
            sc = await repo.get_list_status_counts_for_campaign(ll.id, campaign.id)
            assigned.append({"id": ll.id, "name": ll.name, "total_leads": ll.total_leads, "status_counts": sc, "priority": link.priority})
    out.assigned_lists = assigned
    return out


@router.get("/campaigns", response_model=List[CampaignOut])
async def list_campaigns(
    account_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    campaigns = await repo.list_campaigns(account_id=account_id, include_archived=include_archived)
    if status:
        campaigns = [c for c in campaigns if c.status.value == status]
    return [await _enrich_campaign(repo, c, include_archived=include_archived) for c in campaigns]


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: str, include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return await _enrich_campaign(repo, campaign, include_archived=include_archived)


@router.put("/campaigns/{campaign_id}/lists/order", response_model=CampaignOut)
async def reorder_campaign_lists(
    campaign_id: str,
    body: ReorderListsRequest,
    repo: Repository = Depends(get_repo),
):
    """Set the dispatch priority order of a campaign's assigned lists.

    ``ordered_list_ids`` is highest-priority-first: the first list's pending
    leads are dispatched before the next list's, etc.
    """
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await repo.set_campaign_lists_order(campaign_id, body.ordered_list_ids)
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
async def create_campaign(body: CampaignCreate, repo: Repository = Depends(get_repo)):
    account = await repo.get_account(body.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    campaign = await repo.create_campaign(
        account_id=body.account_id,
        name=body.name,
        connection_message_template=body.connection_message_template,
        followup_message_template=body.followup_message_template,
        followup_delay_hours=body.followup_delay_hours,
        filter_no_photo=body.filter_no_photo,
        filter_min_connections=body.filter_min_connections,
    )
    return await _enrich_campaign(repo, campaign)


@router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: str,
    body: CampaignUpdate,
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    updates = body.model_dump(exclude_unset=True)
    if updates:
        campaign = await repo.update_campaign(campaign, **updates)
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/activate", response_model=CampaignOut)
async def activate_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    from datetime import datetime as _datetime, timedelta as _timedelta
    from releasi.db.models import LeadStatus
    from releasi.scheduler.planner import generate_daily_plan, SlotType

    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Clear paused_at on resume — if no catchup is run, the clock resets so
    # the next pause starts fresh rather than accumulating against the original
    # pause date.
    campaign = await repo.update_campaign(campaign, status=CampaignStatus.ACTIVE, paused_at=None, resume_at=None)

    # Trigger a fresh plan on resume so leads are scheduled immediately
    # rather than waiting until 06:00 the next morning.
    account = await repo.get_account(campaign.account_id)
    if account and not (account.paused_until and account.paused_until > _datetime.utcnow()):
        # Reset any stale scheduled leads back to pending first
        await repo.reset_stale_scheduled_leads(campaign.id)

        now = _datetime.utcnow()
        sent_today = await repo.get_daily_requests_sent(account.id)
        future_scheduled = await repo.count_future_scheduled_leads(campaign.id)
        remaining_budget = max(0, account.daily_limit - sent_today - future_scheduled)

        if remaining_budget > 0:
            pending = await repo.get_pending_leads(campaign.id)
            if pending:
                plan = generate_daily_plan(
                    account_id=account.id,
                    day=now.date(),
                    pending_lead_ids=[l.id for l in pending],
                    daily_limit=account.daily_limit,
                    timezone_str=account.timezone,
                    campaign_weekend_enabled=campaign.weekend_enabled,
                    effective_start=now + _timedelta(minutes=2),
                    remaining_budget=remaining_budget,
                )
                for slot in plan:
                    if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                        if slot.scheduled_at > now:
                            await repo.update_lead_schedule(
                                slot.lead_id, slot.scheduled_at, LeadStatus.SCHEDULED
                            )

    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    from datetime import datetime as _datetime

    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Always stamp paused_at so the dialog threshold reflects the current
    # pause, not a stale one from a previous cycle.
    updates = {"status": CampaignStatus.PAUSED, "paused_at": _datetime.utcnow(), "resume_at": None}
    campaign = await repo.update_campaign(campaign, **updates)
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/timed-pause", response_model=CampaignOut)
async def timed_pause_campaign(
    campaign_id: str,
    minutes: int = Query(default=60, ge=5, le=480),
    repo: Repository = Depends(get_repo),
):
    from datetime import datetime as _datetime, timedelta as _timedelta

    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    resume_at = _datetime.utcnow() + _timedelta(minutes=minutes)
    updates = {
        "status": CampaignStatus.PAUSED,
        "paused_at": _datetime.utcnow(),
        "resume_at": resume_at,
    }
    campaign = await repo.update_campaign(campaign, **updates)
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/acceptance-catchup")
async def start_acceptance_catchup(
    campaign_id: str,
    background_tasks: BackgroundTasks,
    body: Optional[dict] = None,
    repo: Repository = Depends(get_repo),
):
    """Start a one-shot acceptance catchup scan for a paused campaign.

    Returns task_id immediately. Poll
    GET /campaigns/{id}/acceptance-catchup/{task_id} for status.

    Optional body:
      { "cutoff_hours_override": float }  # for the "duration unknown" case
    """
    from releasi.campaign.acceptance_catchup import (
        run_acceptance_catchup,
        MIN_CUTOFF_HOURS,
        MAX_CUTOFF_HOURS,
    )
    from releasi.db.engine import get_session_factory

    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Hard precondition checks (#8 / #15) BEFORE spawning the task
    if campaign.archived:
        raise HTTPException(status_code=409, detail="Campaign is archived")

    account = await repo.get_account(campaign.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    acct_status_val = account.status.value if hasattr(account.status, "value") else account.status
    if acct_status_val != "active":
        raise HTTPException(
            status_code=409,
            detail=f"Account not active (status={acct_status_val})",
        )
    if account.paused_until and account.paused_until > datetime.utcnow():
        raise HTTPException(status_code=409, detail="Account is in cooldown")

    cutoff_override: Optional[float] = None
    if body and body.get("cutoff_hours_override") is not None:
        try:
            cutoff_override = float(body["cutoff_hours_override"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="cutoff_hours_override must be a number")
        if cutoff_override < MIN_CUTOFF_HOURS or cutoff_override > MAX_CUTOFF_HOURS:
            raise HTTPException(
                status_code=422,
                detail=f"cutoff_hours_override must be in [{MIN_CUTOFF_HOURS}, {MAX_CUTOFF_HOURS}]",
            )

    task_id = f"ca-{uuid4().hex[:8]}"
    _catchup_tasks[task_id] = {
        "status": "running",
        "campaign_id": campaign.id,
        "accepted_count": 0,
        "scanned_slugs": 0,
        "cutoff_hours": 0.0,
        "hit_cutoff": False,
        "hit_iteration_cap": False,
        "skipped_reason": None,
        "error": None,
    }

    async def _run(tid: str, c_id: str, override: Optional[float]):
        # New repo + session bound to this background task — the request
        # session would close as soon as we return the task_id.
        session_factory = get_session_factory()
        async with session_factory() as session:
            task_repo = Repository(session)
            try:
                result = await run_acceptance_catchup(
                    task_repo, c_id, cutoff_hours_override=override
                )
                _catchup_tasks[tid] = {
                    "status": "error" if result.error else "done",
                    "campaign_id": c_id,
                    "accepted_count": result.accepted_count,
                    "scanned_slugs": result.scanned_slugs,
                    "cutoff_hours": result.cutoff_hours,
                    "hit_cutoff": result.hit_cutoff,
                    "hit_iteration_cap": result.hit_iteration_cap,
                    "skipped_reason": result.skipped_reason,
                    "error": result.error,
                }
            except Exception as e:
                _catchup_tasks[tid] = {
                    "status": "error",
                    "campaign_id": c_id,
                    "accepted_count": 0,
                    "scanned_slugs": 0,
                    "cutoff_hours": 0.0,
                    "hit_cutoff": False,
                    "hit_iteration_cap": False,
                    "skipped_reason": None,
                    "error": str(e),
                }

    background_tasks.add_task(_run, task_id, campaign.id, cutoff_override)
    return {"task_id": task_id}


@router.get("/campaigns/{campaign_id}/acceptance-catchup/{task_id}")
async def get_acceptance_catchup_status(campaign_id: str, task_id: str):
    """Poll for acceptance catchup task status."""
    task = _catchup_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("campaign_id") != campaign_id:
        raise HTTPException(status_code=404, detail="Task not found for this campaign")
    return task


@router.post("/campaigns/{campaign_id}/reset-leads")
async def reset_leads(campaign_id: str, repo: Repository = Depends(get_repo)):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    count = await repo.reset_campaign_leads(campaign_id)
    return {"reset_count": count}


@router.post("/campaigns/{campaign_id}/archive", response_model=CampaignOut)
async def archive_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign = await repo.update_campaign(campaign, archived=True, status=CampaignStatus.PAUSED)
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/unarchive", response_model=CampaignOut)
async def unarchive_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign = await repo.update_campaign(campaign, archived=False)
    return await _enrich_campaign(repo, campaign)


@router.get("/campaigns/{campaign_id}/stats", response_model=CampaignStatsResponse)
async def campaign_stats(
    campaign_id: str,
    days: int = Query(30, ge=0, le=3650),
    granularity: str = Query("day", regex="^(day|hour)$"),
    include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    start = (date.today() - timedelta(days=days)) if days > 0 else None
    account = await repo.get_account(campaign.account_id)
    include_archived = include_archived or campaign.archived or bool(account and account.archived)
    daily = await repo.get_campaign_daily_stats(campaign_id, start, date.today(), granularity, include_archived=include_archived)
    summary = await repo.get_campaign_acceptance_stats(campaign_id, include_archived=include_archived)
    return {"daily": daily, "summary": summary}


@router.post("/campaigns/{campaign_id}/clone", response_model=CampaignOut, status_code=201)
async def clone_campaign(
    campaign_id: str,
    body: CloneCampaignRequest,
    repo: Repository = Depends(get_repo),
):
    """Clone a campaign's settings to a new campaign under a (potentially different) account."""
    src = await repo.get_campaign(campaign_id)
    if not src:
        raise HTTPException(status_code=404, detail="Campaign not found")

    account = await repo.get_account(body.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Target account not found")

    new_campaign = await repo.create_campaign(
        account_id=body.account_id,
        name=body.name,
        connection_message_template=src.connection_message_template,
        followup_message_template=src.followup_message_template,
        followup_delay_hours=src.followup_delay_hours,
        followup_enabled=src.followup_enabled,
        followup_message_1=src.followup_message_1,
        followup_message_2=src.followup_message_2,
        followup_message_3=src.followup_message_3,
        weekend_enabled=src.weekend_enabled,
        filter_no_photo=src.filter_no_photo,
        filter_min_connections=src.filter_min_connections,
        filter_exclude_open_to_work=src.filter_exclude_open_to_work,
    )

    # Copy lead list assignments (same lists, no lead state)
    links = await repo.get_campaign_lists(campaign_id)
    for link in links:
        from releasi.db.models import CampaignLeadList
        new_link = CampaignLeadList(
            campaign_id=new_campaign.id,
            lead_list_id=link.lead_list_id,
        )
        repo.session.add(new_link)
    await repo.session.commit()

    return await _enrich_campaign(repo, new_campaign)
