"""Lead list endpoints — CRUD, CSV upload, assign/unassign to campaigns."""
from __future__ import annotations

import tempfile
from datetime import datetime
from typing import Optional

import structlog
import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File  # noqa: F401
from pydantic import BaseModel, Field

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo, get_current_user_id
from releasi.api.schemas import (
    LeadListOut,
    LeadListCreate,
    LeadListUpdate,
    LeadListDetail,
    LeadListStats,
    AssignListRequest,
    ImportResponse,
    LeadOut,
    LeadPage,
)
from releasi.db.repository import Repository
from releasi.linkedin.scrape_jobs import get_scrape_job_store

logger = structlog.get_logger()
router = APIRouter(dependencies=[Depends(require_api_key)])


def _get_apollo_key() -> str:
    """Read apollo_api_key from settings.yaml (top-level or under fundraising:)."""
    for path in ["/app/config/settings.yaml", "config/settings.yaml"]:
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return (
                data.get("apollo_api_key")
                or (data.get("fundraising") or {}).get("apollo_api_key")
                or ""
            )
        except FileNotFoundError:
            continue
    return ""


async def _notify_event_scrape(text: str) -> bool:
    """Reuse the configured fundraising Slack destination for event jobs only."""
    from releasi.config import get_settings
    from releasi.notifications.slack import notify
    settings = get_settings()
    if settings.slack_bot_token:
        return await notify(text)
    for path in ("/app/config/settings.yaml", "config/settings.yaml"):
        try:
            with open(path) as file:
                config = yaml.safe_load(file) or {}
        except FileNotFoundError:
            continue
        funding = config.get("fundraising") or {}
        return await notify(text, bot_token=funding.get("slack_bot_token", ""),
                            channel=funding.get("slack_channel") or settings.slack_user_id)
    return False


class EventImportRequest(BaseModel):
    url: str
    account_id: str
    list_name: Optional[str] = None
    limit: Optional[int] = Field(None, ge=1)
    start: bool = True


class ScrapeStatusOut(BaseModel):
    status: str  # running, done, error, awaiting_login, unknown
    collected: int = 0
    error: Optional[str] = None
    next_page: int = 1


def _validate_event_url(url: str) -> None:
    import json
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    try:
        events = json.loads(parse_qs(parsed.query)["eventAttending"][0])
        valid = (parsed.scheme == "https" and parsed.hostname == "www.linkedin.com"
                 and parsed.path.rstrip("/") == "/search/results/people"
                 and not parsed.username and not parsed.password
                 and isinstance(events, list) and len(events) == 1
                 and isinstance(events[0], str) and events[0].isdigit())
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(422, "Use a LinkedIn event attendee people-search URL")


async def _scrape_account(repo: Repository, account_id: str, ready: bool = True):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if ready:
        if account.status != "active" or not account.li_at_cookie:
            raise HTTPException(409, "Reconnect this account and Save before starting/resuming the scrape")
        from releasi.scheduler.runner import _account_proxy_url
        if not await _account_proxy_url(account):
            raise HTTPException(409, "An account proxy is required")
    return account


async def _apollo_enrich_batch(items: list, apollo_api_key: str) -> dict:
    """
    Enrich up to 10 leads via Apollo bulk_match (linkedin_url → name, email, company, title).
    Returns a dict keyed by normalized linkedin_url with the enriched fields.
    """
    import httpx

    details = []
    for item in items:
        d = {"linkedin_url": item["url"]}
        name = item.get("name", "")
        if name:
            parts = name.strip().split(None, 1)
            if parts:
                d["first_name"] = parts[0]
            if len(parts) > 1:
                d["last_name"] = parts[1]
        details.append(d)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.apollo.io/api/v1/people/bulk_match",
                json={"details": details, "reveal_personal_emails": True},
                headers={"x-api-key": apollo_api_key, "Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            logger.warning("apollo_enrich.batch_failed", status=resp.status_code)
            return {}
        matches = resp.json().get("matches") or []
        result = {}
        for m in matches:
            li_url = (m.get("linkedin_url") or "").lower().rstrip("/")
            if not li_url:
                continue
            email = m.get("email") or None
            result[li_url] = {
                "first_name": m.get("first_name") or None,
                "last_name": m.get("last_name") or None,
                "email": email if email and "@" in email else None,
                "company": (m.get("organization") or {}).get("name") or None,
                "title": m.get("title") or None,
            }
        return result
    except Exception as e:
        logger.warning("apollo_enrich.error", error=str(e))
        return {}


class EventScrapeSetupError(RuntimeError):
    """Safe, user-facing setup errors with no credentials or browser details."""


async def _run_event_scrape(list_id: str, account_id: str, url: str, limit: Optional[int]):
    """Run a claimed job, checkpoint each page, and retain partial failures."""
    import asyncio
    from releasi.db.engine import get_session_factory
    from releasi.db.models import Lead, LeadStatus
    from releasi.linkedin.browser import LinkedInBrowser
    from releasi.linkedin.pool import get_browser_pool
    from releasi.linkedin.scraper import scrape_event_attendees
    from releasi.scheduler.runner import _account_proxy_url

    store = get_scrape_job_store()
    sessions = get_session_factory()
    browser = LinkedInBrowser()
    lease = None
    list_name = list_id
    items = []
    try:
        # Keep DB sessions short: an hours-long scrape must not hold a read txn.
        async with sessions() as session:
            repo = Repository(session)
            account = await repo.get_account(account_id)
            ll = await repo.get_lead_list(list_id)
            if not account or not ll:
                raise EventScrapeSetupError("Account or list no longer exists")
            list_name = ll.name
            if account.status != "active" or not account.li_at_cookie:
                raise EventScrapeSetupError("Reconnect this account and Save, then use Re-scrape.")
            if not await _account_proxy_url(account):
                raise EventScrapeSetupError("An account proxy is required for event scraping")

        pool = get_browser_pool()
        if pool.is_busy(account_id):
            raise EventScrapeSetupError("Account browser is busy; resume when its current session is closed.")
        lease = await pool.reserve_external(account_id)
        async with sessions() as session:
            account = await Repository(session).get_account(account_id)
            if not account or account.status != "active" or not account.li_at_cookie:
                raise EventScrapeSetupError("Reconnect this account before resuming.")
            if not await _account_proxy_url(account):
                raise EventScrapeSetupError("An account proxy is required for event scraping")
            await browser.launch(
                account_id=account.id, li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent, proxy_url=account.proxy_url,
                proxy_country=account.proxy_country, timezone=account.timezone,
                cookies_json=account.cookies_json,
            )

        # Include the feed validation in pacing. Never rapid-fire feed + search.
        await store.before_navigation(account_id)
        if not await browser.validate_session():
            raise EventScrapeSetupError("Browser session validation failed; reconnect/check proxy before resuming.")
        async with sessions() as session:
            existing = await Repository(session).get_list_lead_urls(list_id)
        state = store.read(list_id)
        store.update(list_id, collected=len(existing))
        effective_limit = limit if limit is not None else state.get("limit")

        async def _checkpoint(new_items: list) -> None:
            async with sessions() as session:
                repo = Repository(session)
                ll = await repo.get_lead_list(list_id)
                if not ll:
                    raise EventScrapeSetupError("List deleted; scrape stopped")
                existing_now = await repo.get_list_lead_urls(list_id)
                raw = []
                for item in new_items:
                    if item["url"] in existing_now:
                        continue
                    parts = (item.get("name") or "").strip().split(None, 1)
                    raw.append(Lead(
                        lead_list_id=list_id, linkedin_url=item["url"],
                        first_name=parts[0] if parts else None,
                        last_name=parts[1] if len(parts) > 1 else None,
                        status=LeadStatus.PENDING,
                    ))
                if raw:
                    await repo.bulk_create_leads(raw)
                    # Reused canonical leads may still have blank imported names.
                    from sqlalchemy import select
                    names = {lead.linkedin_url: lead for lead in raw}
                    rows = await session.execute(select(Lead).where(Lead.linkedin_url.in_(names)))
                    for lead in rows.scalars():
                        incoming = names[lead.linkedin_url]
                        for field in ("first_name", "last_name"):
                            if not getattr(lead, field) and getattr(incoming, field):
                                setattr(lead, field, getattr(incoming, field))
                # Count actual memberships, including previously existing people.
                total = len(await repo.get_list_lead_urls(list_id))
                await repo.update_lead_list(ll, total_leads=total, csv_filename="event_attendees.csv")
                store.update(list_id, collected=total)

        async def _page_saved(page_num: int, page_size: int, complete: bool) -> None:
            store.record_page_size(account_id, page_size)
            store.update(list_id, next_page=page_num + 1 if complete else page_num)

        async def _before_navigation() -> None:
            await store.before_navigation(account_id)

        page = await browser.new_page()
        items = await scrape_event_attendees(
            page, url, limit=effective_limit,
            page_factory=browser.new_page,
            on_checkpoint=_checkpoint,
            start_page=state.get("next_page", 1), existing_urls=existing,
            before_navigation=_before_navigation, on_page_saved=_page_saved,
        )
        await browser.close()
        lease.release()
        lease = None

        # Enrichment needs no LinkedIn browser and cannot lose saved attendees.
        enrichment = {}
        apollo_key = _get_apollo_key()
        if apollo_key:
            for index in range(0, len(items), 10):
                enrichment.update(await _apollo_enrich_batch(items[index:index + 10], apollo_key))
            if enrichment:
                from sqlalchemy import select
                async with sessions() as session:
                    rows = await session.execute(select(Lead).where(
                        Lead.linkedin_url.in_([item["url"] for item in items])
                    ))
                    for lead in rows.scalars():
                        data = enrichment.get(lead.linkedin_url.lower().rstrip("/"), {})
                        for field in ("first_name", "last_name", "email", "company", "title"):
                            if not getattr(lead, field) and data.get(field):
                                setattr(lead, field, data[field])
                    await session.commit()
        state = store.update(list_id, status="done", error=None)
        logger.info("event_scrape.done", list_id=list_id, total=state["collected"])
        await _notify_event_scrape("*Event scrape complete*: *%s*\n%s attendees saved; %s enriched. No invitations sent." %
                     (list_name, state["collected"], len(enrichment)))
    except asyncio.CancelledError:
        store.update(list_id, status="error", error="Run interrupted; use Re-scrape to resume saved progress.")
        raise
    except Exception as error:
        # Do not include credentials/URLs from browser exceptions in notifications.
        from releasi.linkedin.scraper import EventScrapeStopped
        reason = str(error) if isinstance(error, (EventScrapeStopped, EventScrapeSetupError)) else type(error).__name__
        state = store.update(list_id, status="error", error=reason)
        logger.warning("event_scrape.stopped", list_id=list_id, saved=state.get("collected", 0), reason=reason)
        await _notify_event_scrape("*Event scrape stopped*: *%s*\n%s attendees saved. %s\nUse Re-scrape after resolving the issue; saved attendees stay available." %
                     (list_name, state.get("collected", 0), reason))
    finally:
        try:
            await browser.close()
        finally:
            if lease:
                lease.release()
            store.release(list_id)


async def _enrich_lead_list(
    repo: Repository, lead_list, include_archived: bool = False,
) -> LeadListOut:
    """Add campaign_count to a LeadList."""
    out = LeadListOut.model_validate(lead_list)
    links = await repo.get_list_campaigns(lead_list.id, include_archived=include_archived)
    out.campaign_count = len(links)
    return out


@router.get("/lead-lists", response_model=list[LeadListOut])
async def list_lead_lists(
    include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    lists = await repo.list_lead_lists(include_archived=include_archived)
    return [await _enrich_lead_list(repo, ll, include_archived=include_archived) for ll in lists]


@router.post("/lead-lists", response_model=LeadListOut, status_code=201)
async def create_lead_list(
    body: LeadListCreate,
    repo: Repository = Depends(get_repo),
):
    existing = await repo.get_lead_list_by_name(body.name)
    if existing:
        raise HTTPException(status_code=409, detail="Lead list name already exists")
    ll = await repo.create_lead_list(
        name=body.name,
        tg_enrich_enabled=body.tg_enrich_enabled,
        x_enrich_enabled=body.x_enrich_enabled,
    )
    return await _enrich_lead_list(repo, ll)


# Static routes must be registered before /{lead_list_id} to avoid 405s
@router.post("/lead-lists/event-import", response_model=LeadListOut, status_code=201)
async def event_import_list(
    body: EventImportRequest,
    background_tasks: BackgroundTasks,
    repo: Repository = Depends(get_repo),
):
    """Create a lead list and start scraping LinkedIn event attendees in the background."""
    _validate_event_url(body.url)
    await _scrape_account(repo, body.account_id, ready=body.start)
    store = get_scrape_job_store()
    if body.start and body.account_id in store.active.values():
        raise HTTPException(409, "This account already has a queued/running scrape")
    name = body.list_name or f"Event Attendees - {datetime.utcnow().strftime('%m/%d')}"
    if await repo.get_lead_list_by_name(name):
        raise HTTPException(409, "Lead list name already exists")
    ll = await repo.create_lead_list(name=name, csv_filename="scraping...")
    ll = await repo.update_lead_list(ll, source_url=body.url, scrape_account_id=body.account_id)
    if body.start:
        try:
            store.claim(ll.id, body.account_id, body.limit)
        except ValueError as error:
            await repo.delete_lead_list(ll.id)
            raise HTTPException(409, str(error)) from error
        background_tasks.add_task(_run_event_scrape, ll.id, body.account_id, body.url, body.limit)
    else:
        store.prepare(ll.id, body.account_id, body.limit)
    return await _enrich_lead_list(repo, ll)


class ReScrapeRequest(BaseModel):
    account_id: Optional[str] = None
    limit: Optional[int] = Field(None, ge=1)


@router.post("/lead-lists/{lead_list_id}/re-scrape", response_model=LeadListOut)
async def re_scrape_list(
    lead_list_id: str,
    body: ReScrapeRequest,
    background_tasks: BackgroundTasks,
    repo: Repository = Depends(get_repo),
):
    """Re-run the LinkedIn event scrape on an existing list, skipping already-imported leads."""
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if not ll.source_url:
        raise HTTPException(status_code=400, detail="No source URL stored for this list — cannot re-scrape")

    account_id = body.account_id or ll.scrape_account_id
    if not account_id:
        raise HTTPException(status_code=400, detail="No account ID provided and none stored on this list")

    _validate_event_url(ll.source_url)
    await _scrape_account(repo, account_id)
    store = get_scrape_job_store()
    try:
        store.claim(lead_list_id, account_id, body.limit)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

    try:
        # Only an unstarted list can safely switch account search ordering.
        if body.account_id and body.account_id != ll.scrape_account_id:
            ll = await repo.update_lead_list(ll, scrape_account_id=body.account_id)
        response = await _enrich_lead_list(repo, ll)
    except BaseException:
        store.update(lead_list_id, status="error", error="Failed to queue resume; no scraping started")
        store.release(lead_list_id)
        raise
    background_tasks.add_task(_run_event_scrape, ll.id, account_id, ll.source_url, body.limit)
    return response


@router.get("/lead-lists/{lead_list_id}/scrape-status", response_model=ScrapeStatusOut)
async def get_scrape_status(lead_list_id: str):
    """Poll the progress of an in-progress event attendee scrape."""
    job = get_scrape_job_store().read(lead_list_id)
    if not job:
        return ScrapeStatusOut(status="unknown", collected=0)
    return ScrapeStatusOut(**job)


@router.get("/lead-lists/{lead_list_id}", response_model=LeadListDetail)
async def get_lead_list(
    lead_list_id: str, include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    links = await repo.get_list_campaigns(lead_list_id, include_archived=include_archived)
    campaigns = []
    for link in links:
        campaign = await repo.get_campaign(link.campaign_id)
        if campaign:
            campaigns.append({"id": campaign.id, "name": campaign.name})

    stats_data = await repo.get_lead_list_stats(lead_list_id)
    stats = LeadListStats(**stats_data)

    out = LeadListDetail.model_validate(ll)
    out.campaign_count = len(campaigns)
    out.campaigns = campaigns
    out.stats = stats
    return out


@router.patch("/lead-lists/{lead_list_id}", response_model=LeadListOut)
async def update_lead_list(
    lead_list_id: str,
    body: LeadListUpdate,
    repo: Repository = Depends(get_repo),
):
    """Update a lead list's name and/or tg_enrich_enabled flag."""
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    updates = body.model_dump(exclude_none=True)
    if "name" in updates:
        existing = await repo.get_lead_list_by_name(updates["name"])
        if existing and existing.id != lead_list_id:
            raise HTTPException(status_code=409, detail="Lead list name already exists")
    if updates:
        ll = await repo.update_lead_list(ll, **updates)
    return await _enrich_lead_list(repo, ll)


@router.post("/lead-lists/{lead_list_id}/archive", response_model=LeadListOut)
async def archive_lead_list(
    lead_list_id: str,
    repo: Repository = Depends(get_repo),
    actor_user_id: Optional[str] = Depends(get_current_user_id),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    ll = await repo.update_lead_list(ll, archived=True)
    logger.info(
        "lead_list_archived",
        list_id=lead_list_id,
        list_name=ll.name,
        actor_user_id=actor_user_id or "system",
    )
    return await _enrich_lead_list(repo, ll)


@router.post("/lead-lists/{lead_list_id}/unarchive", response_model=LeadListOut)
async def unarchive_lead_list(
    lead_list_id: str,
    repo: Repository = Depends(get_repo),
    actor_user_id: Optional[str] = Depends(get_current_user_id),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    ll = await repo.update_lead_list(ll, archived=False)
    logger.info(
        "lead_list_unarchived",
        list_id=lead_list_id,
        list_name=ll.name,
        actor_user_id=actor_user_id or "system",
    )
    return await _enrich_lead_list(repo, ll)


@router.delete("/lead-lists/{lead_list_id}")
async def delete_lead_list(lead_list_id: str, repo: Repository = Depends(get_repo)):
    store = get_scrape_job_store()
    if lead_list_id in store.active:
        raise HTTPException(409, "Cannot delete a list while its scrape is running")
    deleted = await repo.delete_lead_list(lead_list_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Lead list not found")
    store.delete(lead_list_id)
    return {"ok": True}


@router.post("/lead-lists/{lead_list_id}/import", response_model=ImportResponse)
async def import_csv_to_list(
    lead_list_id: str,
    file: UploadFile = File(...),
    repo: Repository = Depends(get_repo),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    # Save uploaded file to temp location
    contents = await file.read()
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    # Get existing URLs in the list for dedup
    existing_urls = await repo.get_list_lead_urls(lead_list_id)

    # Parse CSV — leads created with lead_list_id (no campaign_id yet)
    from releasi.campaign.importer import parse_csv
    leads, result = parse_csv(tmp_path, lead_list_id=lead_list_id, existing_urls=existing_urls)

    if leads:
        count = await repo.bulk_create_leads(leads)
        await repo.update_lead_list(ll, total_leads=ll.total_leads + count)
        # Update csv_filename if not set
        if not ll.csv_filename and file.filename:
            await repo.update_lead_list(ll, csv_filename=file.filename)

    import os
    try:
        os.unlink(tmp_path)
    except OSError:
        pass

    return ImportResponse(
        total_rows=result.total_rows,
        imported=result.imported,
        duplicates_skipped=result.duplicates_skipped,
        no_url_skipped=result.no_url_skipped,
        no_url_imported=result.no_url_imported,
        errors=result.errors,
    )


@router.get("/lead-lists/{lead_list_id}/export")
async def export_lead_list_csv(lead_list_id: str, repo: Repository = Depends(get_repo)):
    """Download all leads in a list as a CSV file."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    leads, _ = await repo.get_list_leads(lead_list_id, page=1, per_page=10000)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["linkedin_url", "first_name", "last_name", "company", "title", "email", "phone"])
    for lead in leads:
        writer.writerow([
            lead.linkedin_url,
            lead.first_name or "",
            lead.last_name or "",
            lead.company or "",
            lead.title or "",
            lead.email or "",
            lead.phone or "",
        ])

    buf.seek(0)
    filename = f"{ll.name.replace(' ', '_')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/lead-lists/{lead_list_id}/leads", response_model=LeadPage)
async def list_leads_in_list(
    lead_list_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    repo: Repository = Depends(get_repo),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    leads, total = await repo.get_list_leads(lead_list_id, page=page, per_page=per_page)
    return LeadPage(
        items=[LeadOut.model_validate(l) for l in leads],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/lead-lists/{lead_list_id}/assign")
async def assign_list_to_campaign(
    lead_list_id: str,
    body: AssignListRequest,
    repo: Repository = Depends(get_repo),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    campaign = await repo.get_campaign(body.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    count = await repo.assign_list_to_campaign(lead_list_id, body.campaign_id)
    # Update campaign total_leads
    await repo.update_campaign(campaign, total_leads=campaign.total_leads + count)
    return {"leads_added": count}


@router.post("/lead-lists/{lead_list_id}/unassign")
async def unassign_list_from_campaign(
    lead_list_id: str,
    body: AssignListRequest,
    repo: Repository = Depends(get_repo),
    actor_user_id: Optional[str] = Depends(get_current_user_id),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    campaign = await repo.get_campaign(body.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    count = await repo.unassign_list_from_campaign(lead_list_id, body.campaign_id)
    await repo.update_campaign(campaign, total_leads=max(0, campaign.total_leads - count))
    logger.info(
        "lead_list_unassigned",
        list_id=lead_list_id,
        list_name=ll.name,
        campaign_id=body.campaign_id,
        campaign_name=campaign.name,
        leads_removed=count,
        actor_user_id=actor_user_id or "system",
    )
    return {"leads_removed": count}
