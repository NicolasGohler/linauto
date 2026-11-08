"""APScheduler-based scheduler daemon for unattended operation."""
from __future__ import annotations

import asyncio
import random
import signal
from datetime import date, datetime, timedelta, timezone as _dt_tz
from typing import Optional

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_MAX_INSTANCES

from releasi.config import get_settings
from releasi.db.engine import init_db, get_session_factory, close_db
from releasi.db.models import (
    Account, AccountStatus, Campaign, CampaignStatus,
    Lead, LeadStatus, ActionType, ActionLogStatus, BroadcastStatus,
)
from releasi.db.repository import Repository
from releasi.linkedin.pool import get_browser_pool, init_pool, shutdown_pool
from releasi.scheduler.planner import generate_daily_plan, SlotType
from releasi.safety.cooldown import is_cooldown_expired, calculate_cooldown_resume, push_cooldown_one_day
from releasi.safety import limits as rate_limits
from releasi.safety import suppression
from releasi.campaign.state_machine import validate_transition, InvalidTransition
from releasi.notifications.slack import notify as slack_notify
from releasi.safety.dispatch_decisions import (
    classify_connection_result,
    classify_followup_result,
    DispatchIntent,
    AccountAction,
    LeadAction,
)

logger = structlog.get_logger()


async def _apply_connection_intent(
    intent: DispatchIntent,
    repo: "Repository",
    account,
    campaign,
    lead=None,
    result: Optional[dict] = None,
) -> None:
    """Execute DB writes, Slack pings, and logging encoded in a DispatchIntent
    for connection-request dispatchers.  Does NOT set stop_account or break."""
    if lead is not None and intent.failure_kind == "uncertain_outcome":
        await repo.defer_connection_attempt(campaign.id, lead.id,
                                           "Uncertain action outcome after timeout; reconcile before retry", 0, max_attempts=1)
        await repo.increment_daily_stat(account.id, "errors")
        await repo.log_action(account_id=account.id, campaign_id=campaign.id, lead_id=lead.id,
                              action_type=ActionType.CONNECTION_REQUEST, status=ActionLogStatus.FAILED,
                              details={"failure_kind": "uncertain_outcome", "reason": "execute_lead_timeout"})
    if intent.retry_delay_seconds and lead is not None:
        retry = await repo.defer_connection_attempt(
            campaign.id, lead.id, f"[{intent.failure_kind}] {(result or {}).get('error') or intent.failure_kind}",
            intent.retry_delay_seconds,
            max_attempts=3 if intent.failure_kind == "profile_dom" else 6,
        )
        await repo.increment_daily_stat(account.id, "errors")
        await repo.log_action(
            account_id=account.id, campaign_id=campaign.id, lead_id=lead.id,
            action_type=ActionType.CONNECTION_REQUEST, status=ActionLogStatus.FAILED,
            details={"reason": (result or {}).get("error"), "failure_kind": intent.failure_kind, **retry},
        )
        if retry["exhausted"]:
            await slack_notify(f":warning: Connection retry exhausted for {lead.linkedin_url} "
                               f"on *{campaign.name}* ({intent.failure_kind}); manual review needed.")
    if intent.account_backoff_seconds:
        resume = datetime.utcnow() + timedelta(seconds=intent.account_backoff_seconds)
        if account.paused_until and account.paused_until > resume:
            resume = account.paused_until
        await repo.update_account(account, paused_until=resume)
    if intent.log_event:
        if "network" in intent.log_event or "proxy" in intent.log_event:
            logger.warning(
                intent.log_event,
                account=account.name,
                campaign=campaign.name,
                consecutive=intent.consecutive_network,
            )
        else:
            logger.error(
                intent.log_event,
                account=account.name,
                campaign=campaign.name,
                consecutive=max(intent.consecutive_session, intent.consecutive_network),
            )
        if intent.stop_account:
            await repo.log_action(
                account_id=account.id,
                campaign_id=campaign.id,
                action_type=ActionType.ERROR,
                status=ActionLogStatus.FAILED,
                details={k: v for k, v in {
                    "reason": intent.log_reason,
                    "failure_kind": intent.failure_kind,
                    "count": max(intent.consecutive_session, intent.consecutive_network) or None,
                }.items() if v is not None},
            )
    if intent.account_action == AccountAction.MARK_COOKIE_EXPIRED:
        await repo.update_account(account, status="cookie_expired")
    elif intent.account_action == AccountAction.PAUSE:
        await repo.update_account(account, status="paused")
    if intent.slack_message:
        await slack_notify(intent.slack_message)
    if intent.reset_scheduled_to_pending:
        await repo.bulk_update_lead_status(
            campaign.id,
            from_status=LeadStatus.SCHEDULED,
            to_status=LeadStatus.PENDING,
        )


async def _apply_followup_intent(
    intent: DispatchIntent,
    repo: "Repository",
    account,
    campaign,
    lead,
) -> None:
    """Execute DB writes, Slack pings, and logging for followup dispatchers."""
    if intent.account_backoff_seconds:
        resume = datetime.utcnow() + timedelta(seconds=intent.account_backoff_seconds)
        if account.paused_until and account.paused_until > resume:
            resume = account.paused_until
        await repo.update_account(account, paused_until=resume)
    if intent.lead_action == LeadAction.MARK_SENT:
        validate_transition(lead.status, LeadStatus.FOLLOWUP_SENT)
        await repo.update_lead(
            lead,
            campaign_id_override=campaign.id,
            status=LeadStatus.FOLLOWUP_SENT,
            followup_sent_at=datetime.utcnow(),
        )
    elif intent.lead_action == LeadAction.MARK_ERROR:
        validate_transition(lead.status, LeadStatus.ERROR)
        await repo.update_lead(
            lead,
            campaign_id_override=campaign.id,
            status=LeadStatus.ERROR,
            retry_count=1,
        )
    if intent.account_action == AccountAction.MARK_COOKIE_EXPIRED:
        await repo.update_account(account, status="cookie_expired")
    if intent.slack_message:
        await slack_notify(intent.slack_message)
    if intent.log_event:
        if "network" in intent.log_event or "proxy" in intent.log_event:
            logger.warning(
                intent.log_event,
                account=account.name,
                campaign=campaign.name,
                url=lead.linkedin_url,
                consecutive=intent.consecutive_network,
            )
        else:
            log_fn = logger.error if (
                "expired" in intent.log_event
                or "consecutive" in intent.log_event
                or "fatal" in intent.log_event
            ) else logger.warning
            log_fn(
                intent.log_event,
                account=account.name,
                campaign=campaign.name,
                url=lead.linkedin_url,
                consecutive=max(intent.consecutive_session, intent.consecutive_network),
            )
        await repo.log_action(
            account_id=account.id,
            campaign_id=campaign.id,
            lead_id=lead.id,
            action_type=ActionType.ERROR,
            status=ActionLogStatus.FAILED,
            details={k: v for k, v in {
                "reason": intent.log_reason,
                "count": max(intent.consecutive_session, intent.consecutive_network) or None,
            }.items() if v is not None},
        )

# Per-account session tracking: when the last dispatch session ended (UTC naive).
# Prevents rapid-fire dispatching across 5-min cycles — enforces inter-session gap.
_last_session_end: dict = {}

# Per-account "ran today" trackers for once-daily jobs.
# Key: account_id, Value: date in the account's local timezone.
# Reset on container restart (intentional — re-run keeps things fresh).
_keepalive_ran: dict = {}      # keep_alive: only once per local day, morning window
_acceptance_checked: dict = {} # check_acceptances: only once per local day

# Continuous dispatch pacing is stateless (see scheduler/pacing.py): the work window is
# derived from account id + local date + current timezone, and progress comes from the DB.
# Only watchdog alert de-duplication is held in memory: {(account_id, date, kind)}.
_pacing_alerts_sent: set = set()

# Telegram enrichment sweeper lock — ensures only one sweep runs at a time.
# Held for the duration of the find_telegram() call (and any flood-wait sleep).
# The per-lead Find button does NOT use this lock — it creates its own client.
_tg_sweep_lock = asyncio.Lock()

# UTC datetime until which the sweeper is flood-waiting, or None.
# Set before asyncio.sleep(); cleared after. Read by the status API endpoint.
# Safe to read without a lock — asyncio is single-threaded.
_tg_flood_wait_until: Optional[datetime] = None

# UTC timestamp of the last successfully committed sweep tick.
_tg_last_processed_at: Optional[datetime] = None


def _acct_local_now(account):
    """Return current datetime in the account's timezone (aware), or UTC if unset/invalid."""
    tz_str = account.timezone
    if tz_str:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_str))
        except Exception:
            pass
    return datetime.now(_dt_tz.utc)

import re as _re


def _normalize_li_url(url: Optional[str]) -> str:
    """Extract '/in/slug' from any LinkedIn profile URL variant for comparison."""
    if not url:
        return ""
    m = _re.search(r'/in/([^/?#\s]+)', url)
    if m:
        return f"/in/{m.group(1).rstrip('/')}"
    return ""


# Keep-alive activity rotation: (name, url) pairs with selection weights
_KEEPALIVE_ACTIVITIES = [
    ("feed", "https://www.linkedin.com/feed/"),
    ("notifications", "https://www.linkedin.com/notifications/"),
    ("network", "https://www.linkedin.com/mynetwork/"),
    ("messaging", "https://www.linkedin.com/messaging/"),
    ("jobs", "https://www.linkedin.com/jobs/"),
    ("learning", "https://www.linkedin.com/learning/"),
]
# Weights for step-2 selection (index 1 onwards): notifications/network/messaging/jobs/learning
_KEEPALIVE_STEP2_WEIGHTS = [3, 2, 2, 1, 1]


async def _account_proxy_url(account) -> Optional[str]:
    """Resolve the account's residential proxy URL (explicit, else built)."""
    proxy_url = account.proxy_url
    if not proxy_url and account.proxy_country:
        try:
            from releasi.linkedin.browser import _build_proxy_url
            proxy_url = _build_proxy_url(account.id, account.proxy_country)
        except Exception:
            proxy_url = None
    return proxy_url


_session_alerted: dict = {}  # (account_id, kind, utc_date) -> True; once/day dedup


async def _check_session_alerts(account, curr, prev) -> None:
    """Fire preventive Slack alerts by diffing consecutive ledger rows.

    Edge-triggered checks (egress IP change, li_rm vanishing) fire once on the
    transition; the level-triggered li_at-expiry warning is deduped to once per
    UTC day. Best-effort — never raises into the caller.
    """
    if curr is None:
        return
    today = datetime.utcnow().date()

    def first_today(kind: str) -> bool:
        key = (account.id, kind, today)
        if key in _session_alerted:
            return False
        _session_alerted[key] = True
        return True

    # Egress IP changed between two consecutive touches → impossible-travel risk.
    if prev and prev.egress_ip and curr.egress_ip and prev.egress_ip != curr.egress_ip:
        verdict = ""
        try:
            from releasi.linkedin.session_telemetry import vet_proxy_ip
            v = await vet_proxy_ip(curr.egress_ip)
            if v.get("risky"):
                verdict = f" :rotating_light: New IP looks risky ({v.get('reason')})."
        except Exception:
            pass
        await slack_notify(
            f":satellite_antenna: Egress IP changed for *{account.name}*: "
            f"`{prev.egress_ip}` → `{curr.egress_ip}`. A sudden IP/location shift "
            f"is a top session-invalidation trigger — check the proxy.{verdict}"
        )
    # li_rm token disappeared → silent re-auth will no longer work.
    if prev and prev.has_li_rm and not curr.has_li_rm:
        await slack_notify(
            f":warning: li_rm token disappeared for *{account.name}* — silent "
            "re-auth is no longer possible; a manual re-login (with 'Keep me "
            "logged in') will be needed when li_at next expires."
        )
    # li_at expiry approaching (level-triggered → once/day).
    if curr.li_at_expires_at:
        days = (curr.li_at_expires_at - datetime.utcnow()).days
        if days <= 7 and first_today("expiry"):
            await slack_notify(
                f":hourglass_flowing_sand: li_at for *{account.name}* expires in "
                f"~{days}d ({curr.li_at_expires_at.date()}). Consider a proactive re-login."
            )


async def _record_session_touch(
    context,
    account,
    repo,
    *,
    job: str,
    result: str,
    detail: Optional[dict] = None,
    consecutive_session_errors: Optional[int] = None,
    fetch_egress: bool = True,
) -> None:
    """Record one session-health ledger row + write back a rotated li_at.

    Captures, at the moment we learn the session's health, the live cookie
    fingerprint, the egress IP/geo LinkedIn actually saw, and the browser
    fingerprint. Also keeps ``accounts.li_at_cookie`` in sync with the live
    (possibly rotated) cookie so slot recreation never re-stamps a stale token.

    Best-effort: monitoring must never break the job it observes.
    """
    try:
        from releasi.linkedin.session_telemetry import cookie_snapshot, egress_identity

        cookies = []
        try:
            if context is not None:
                cookies = await context.cookies("https://www.linkedin.com")
        except Exception:
            cookies = []
        snap = cookie_snapshot(cookies)
        live_li_at = next(
            (c.get("value") for c in cookies if c.get("name") == "li_at"), None
        )

        egress = {"egress_ip": None, "geo": None, "asn": None}
        if fetch_egress:
            egress = await egress_identity(await _account_proxy_url(account))

        await repo.log_session_event(
            account_id=account.id,
            job=job,
            result=result,
            egress_ip=egress["egress_ip"],
            geo=egress["geo"],
            asn=egress["asn"],
            li_at_fp=snap["li_at_fp"],
            li_at_expires_at=snap["li_at_expires_at"],
            has_li_rm=snap["has_li_rm"],
            cookie_names=snap["cookie_names"],
            user_agent=account.user_agent,
            timezone=account.timezone,
            consecutive_session_errors=consecutive_session_errors,
            detail=detail,
        )

        # Write-back: keep the DB li_at in sync with the live cookie. Without
        # this the DB is a frozen login-time snapshot and a slot recreation can
        # re-inject a stale token over a fresher on-disk one.
        if live_li_at and live_li_at != account.li_at_cookie:
            kw = {"li_at_cookie": live_li_at}
            if snap["li_at_expires_at"]:
                kw["li_at_expires_at"] = snap["li_at_expires_at"]
            await repo.update_account(account, **kw)
            logger.info("session.li_at_written_back", account=account.name)

        # Preventive alerts: diff this row against the previous one.
        try:
            recent = await repo.list_session_events(account_id=account.id, limit=2)
            curr = recent[0] if recent else None
            prev = recent[1] if len(recent) > 1 else None
            await _check_session_alerts(account, curr, prev)
        except Exception:
            pass
    except Exception as e:
        logger.warning(
            "session_touch.record_failed",
            account=getattr(account, "name", "?"),
            error=str(e),
        )


async def _mark_cookie_expired(account, repo, reason: str) -> None:
    """Mark account cookie_expired and send Slack alert.

    Centralised so all four expiry-detection sites stay in sync. Each call
    site still owns its own control-flow (continue/break/stop_account) and
    any additional log_action calls that carry context specific to that job.
    """
    await repo.update_account(account, status="cookie_expired")
    await slack_notify(
        f":warning: *Cookie expired* — account *{account.name}* (detected by {reason}). "
        "Update the cookie in account settings."
    )


async def _attempt_li_rm_reauth(context, account, repo) -> bool:
    """Silently re-mint li_at from the stored li_rm token. Returns True on success.

    When a session redirects to login but the account has a li_rm ("remember me")
    token, LinkedIn will auto-issue a fresh li_at on the next navigation — no
    password needed. This is the mechanism behind "log in once, stay logged in
    for months": it removes the human from the loop for the common case where
    li_at aged out but li_rm is still valid.

    Best-effort and fail-safe: any failure returns False so the caller falls
    back to marking the account cookie_expired (manual re-login). li_rm itself
    can be revoked by the same security triggers as li_at, so this is not a
    guarantee — but it rescues the majority of routine expiries.
    """
    li_rm = getattr(account, "li_rm_cookie", None)
    if not li_rm or context is None:
        return False
    try:
        # Make sure li_rm is present in the context (inject from DB if the
        # on-disk profile lost it). li_at is intentionally left as-is: if it is
        # stale/invalid, li_rm drives the re-mint.
        try:
            cookies = await context.cookies("https://www.linkedin.com")
            if not any(c.get("name") == "li_rm" for c in cookies):
                await context.add_cookies([{
                    "name": "li_rm", "value": li_rm, "domain": ".linkedin.com",
                    "path": "/", "httpOnly": True, "secure": True, "sameSite": "None",
                }])
        except Exception:
            pass

        from releasi.linkedin.navigator import LinkedInNavigator
        page = await context.new_page()
        try:
            feed = await LinkedInNavigator(page).go_to_feed()
        finally:
            await page.close()

        if not feed.success:
            # Network/proxy issue — not a re-auth failure; don't mark expired.
            await repo.log_session_event(account.id, "reauth", "network_error")
            return False
        if not feed.session_valid:
            # li_rm is also dead → genuine expiry, caller will mark cookie_expired.
            await _record_session_touch(
                context, account, repo, job="reauth", result="reauth_failed",
            )
            return False

        # Healthy again. _record_session_touch captures the freshly minted li_at
        # and writes it back to the DB (li_at_cookie + expiry).
        await _record_session_touch(
            context, account, repo, job="reauth", result="reauth_ok",
        )
        if account.status != AccountStatus.ACTIVE:
            await repo.update_account(account, status="active")
        logger.info("session.reauth_ok", account=account.name)
        return True
    except Exception as e:
        logger.warning("session.reauth_error", account=account.name, error=str(e))
        return False


async def _get_repo() -> tuple:
    """Get a Repository + session."""
    session = get_session_factory()()
    return Repository(session), session


def _continuous_window(account):
    """Today's (work_start, work_end) in naive UTC for this account's current timezone."""
    from releasi.scheduler import pacing

    settings = get_settings()
    return pacing.work_window(
        account.id,
        _acct_local_now(account).date(),
        account.timezone,
        settings.work_start_hour,
        settings.work_end_hour,
        tuple(settings.work_start_variation),
        tuple(settings.work_end_variation),
    )


async def _dispatch_continuous(
    account,
    repo: "Repository",
    pool_context,
    remaining: int,
    batch_size: Optional[int] = None,
) -> int:
    """Run one continuous-mode dispatch session. Returns number of successful sends.

    batch_size comes from the pacing decision; when omitted it is drawn from the
    configured range.
    """
    from releasi.campaign.executor import CampaignExecutor
    from releasi.linkedin.noise import BrowsingNoise

    settings = get_settings()
    if batch_size is None:
        batch_size = random.randint(settings.continuous_batch_size[0], settings.continuous_batch_size[1])
    batch_size = min(batch_size, remaining)
    if batch_size <= 0:
        return 0

    campaigns = await repo.get_active_campaigns(account.id)
    total_sent = 0
    remaining_batch = batch_size
    stop_account = False

    for campaign in campaigns:
        if stop_account or remaining_batch <= 0:
            break

        # Phase 2 read cutover: use CampaignLeadAssignment as source of truth.
        pending = list(await repo.get_pending_leads_via_assignments(campaign.id, limit=remaining_batch * 4))
        if not pending:
            continue

        executor = CampaignExecutor(repo, browser_context=pool_context)
        successful_sends = 0
        consecutive_session_errors = 0
        consecutive_network_errors = 0
        max_attempts = remaining_batch * 4
        lead_queue = list(pending)
        attempts = 0

        while lead_queue and successful_sends < remaining_batch and attempts < max_attempts:
            attempts += 1
            lead = lead_queue.pop(0)
            if not await repo.is_lead_visible(lead.id):
                continue
            # Per-lead ceiling so one hung Playwright call can't freeze the whole
            # dispatcher for hours (see 2026-08-24 → 2026-08-27 hang). Real dispatches
            # complete in ~30–90s; 180s is a hard "something's wrong" wall.
            try:
                result = await asyncio.wait_for(
                    executor.execute_single_lead(account, campaign, lead),
                    timeout=180,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "dispatch.execute_lead_timeout",
                    account=account.name, campaign=campaign.name,
                    lead_id=lead.id, url=lead.linkedin_url,
                )
                # Treat as a per-lead error and move on so the dispatcher stays alive.
                result = {"success": False, "skipped": False, "fatal": False,
                          "limit_reached": False, "session_expired": False,
                          "error": "execute_lead_timeout"}

            if result.get("soft_limit_reached"):
                backoff_hours = random.uniform(2, 4)
                resume = datetime.utcnow() + timedelta(hours=backoff_hours)
                await repo.update_account(account, paused_until=resume)
                await repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    action_type=ActionType.COOLDOWN_STARTED,
                    status=ActionLogStatus.SUCCESS,
                    details={"paused_until": str(resume), "reason": "rate_limit_modal", "backoff_hours": round(backoff_hours, 1)},
                )
                logger.warning("dispatch.continuous_soft_limit", account=account.name, resume=str(resume))
                stop_account = True
                break

            if result.get("limit_reached"):
                resume = calculate_cooldown_resume(account.timezone)
                await repo.update_account(account, paused_until=resume)
                await repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    action_type=ActionType.COOLDOWN_STARTED,
                    status=ActionLogStatus.SUCCESS,
                    details={"paused_until": str(resume)},
                )
                logger.warning("dispatch.continuous_cooldown_started", account=account.name, resume=str(resume))
                stop_account = True
                break

            intent = classify_connection_result(
                result,
                consecutive_session_errors,
                consecutive_network_errors,
                account.name,
                campaign.name,
            )
            consecutive_session_errors = intent.consecutive_session
            consecutive_network_errors = intent.consecutive_network
            await _apply_connection_intent(intent, repo, account, campaign, lead, result)
            if intent.stop_account:
                stop_account = True
                break

            if result.get("success"):
                successful_sends += 1
                total_sent += 1
                await repo.add_proxy_mb(account.id, 2.0)
            # skipped and non-3-strike failures: no backfill in continuous mode

        remaining_batch -= successful_sends

        if successful_sends > 0:
            sent_today = await repo.get_daily_requests_sent(account.id)
            logger.info(
                "dispatch.continuous_cycle_complete",
                campaign=campaign.name,
                cycle_sent=successful_sends,
                day_sent=sent_today,
                daily_limit=account.daily_limit,
                batch_size=batch_size,
            )

    # Post-session noise: 1-2 actions in the same browser context
    if total_sent > 0 and not stop_account:
        noise_page = None
        try:
            noise_page = await pool_context.new_page()
            noise = BrowsingNoise(noise_page)
            noise_count = random.randint(1, 2)
            for _ in range(noise_count):
                action = random.choice(["profile", "feed"])
                try:
                    if action == "profile":
                        await asyncio.wait_for(noise.view_random_profile(), timeout=90)
                    else:
                        await asyncio.wait_for(noise.like_feed_post(), timeout=90)
                except asyncio.TimeoutError:
                    logger.debug("dispatch.continuous_noise_timeout", action=action)
                await asyncio.sleep(random.uniform(3, 8))
        except Exception as e:
            logger.debug("dispatch.continuous_noise_failed", error=str(e))
        finally:
            if noise_page:
                try:
                    await noise_page.close()
                except Exception:
                    pass

    return total_sent


async def _dispatch_planned(
    account,
    repo: "Repository",
    pool_context,
    pool,
    remaining: int,
    now: datetime,
    sent_today_count: int,
) -> None:
    """Legacy planned-mode dispatch: execute SCHEDULED leads past their slot time.

    Only called when account.dispatch_mode == 'planned'. Continuous mode (the
    default for all accounts) uses _dispatch_continuous() instead.

    Behaviour:
    - Fetches SCHEDULED leads with scheduled_at <= now for each active campaign.
    - Caps each cycle to actions_per_session[1] to prevent bursts on restart.
    - Backfills skipped/errored leads into future slots (inter_session_delay ahead).
    - Clamps backfill anchor to the account's work window (never schedules overnight).
    """
    from releasi.campaign.executor import CampaignExecutor

    campaigns = await repo.get_active_campaigns(account.id)
    for campaign in campaigns:
        due_leads = list(await repo.get_scheduled_leads(campaign.id, before=now))
        if not due_leads:
            continue

        # Target = how many *successful* sends we want this cycle.
        # Cap to actions_per_session[1] so one dispatch cycle never
        # sends more than one session's worth of leads — this prevents
        # the "10+ connections in 10 minutes" burst when restarting
        # mid-day with many slots clustered near now.
        _max_per_session = get_settings().actions_per_session[1]
        target = min(
            remaining if remaining is not None else len(due_leads),
            _max_per_session,
        )

        executor = CampaignExecutor(repo, browser_context=pool_context)

        successful_sends = 0
        attempts = 0
        consecutive_session_errors = 0   # non-network failures → cookie suspect
        consecutive_network_errors = 0   # timeouts/proxy → network suspect
        max_attempts = target * 4  # Safety cap: allow skips+errors without infinite loop
        lead_queue = list(due_leads)  # Fixed queue — no mid-session injections
        stop_account = False
        backfill_count = 0  # Skipped/errored leads needing future rescheduling

        while lead_queue and successful_sends < target and attempts < max_attempts:
            attempts += 1
            lead = lead_queue.pop(0)
            if not await repo.is_lead_visible(lead.id):
                continue
            # Per-lead ceiling — see _dispatch_continuous for rationale.
            try:
                result = await asyncio.wait_for(
                    executor.execute_single_lead(account, campaign, lead),
                    timeout=180,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "dispatch.execute_lead_timeout",
                    account=account.name, campaign=campaign.name,
                    lead_id=lead.id, url=lead.linkedin_url,
                )
                result = {"success": False, "skipped": False, "fatal": False,
                          "limit_reached": False, "session_expired": False,
                          "error": "execute_lead_timeout"}

            if result.get("soft_limit_reached"):
                # Rate-limit modal (soft block) — short 2–4 hour backoff.
                # SCHEDULED leads stay as-is; they'll be picked up on resume.
                backoff_hours = random.uniform(2, 4)
                resume = datetime.utcnow() + timedelta(hours=backoff_hours)
                await repo.update_account(account, paused_until=resume)
                await repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    action_type=ActionType.COOLDOWN_STARTED,
                    status=ActionLogStatus.SUCCESS,
                    details={"paused_until": str(resume), "reason": "rate_limit_modal", "backoff_hours": round(backoff_hours, 1)},
                )
                logger.warning(
                    "dispatch.soft_limit_backoff",
                    account=account.name,
                    resume=str(resume),
                    backoff_hours=round(backoff_hours, 1),
                )
                stop_account = True
                break

            if result.get("limit_reached"):
                resume = calculate_cooldown_resume(account.timezone)
                await repo.update_account(account, paused_until=resume)
                await repo.bulk_update_lead_status(
                    campaign.id,
                    from_status=LeadStatus.SCHEDULED,
                    to_status=LeadStatus.LIMIT_PAUSED,
                )
                await repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    action_type=ActionType.COOLDOWN_STARTED,
                    status=ActionLogStatus.SUCCESS,
                    details={"paused_until": str(resume)},
                )
                logger.warning(
                    "dispatch.cooldown_started",
                    account=account.name,
                    resume=str(resume),
                )
                stop_account = True
                break

            intent = classify_connection_result(
                result,
                consecutive_session_errors,
                consecutive_network_errors,
                account.name,
                campaign.name,
            )
            consecutive_session_errors = intent.consecutive_session
            consecutive_network_errors = intent.consecutive_network
            await _apply_connection_intent(intent, repo, account, campaign, lead, result)
            if intent.stop_account:
                stop_account = True
                break

            if result.get("success"):
                successful_sends += 1
                await repo.add_proxy_mb(account.id, 2.0)  # ~2 MB per connection request
            elif result.get("skipped"):
                backfill_count += 1
            elif not intent.retry_delay_seconds and not result.get("network_error") and not result.get("session_expired") and not result.get("fatal"):
                # Generic failure that didn't hit 3-strike stop — schedule a backfill slot
                backfill_count += 1

        # Post-session: schedule backfills for any skipped/errored leads.
        # Anchor the first backfill after the last future scheduled slot +
        # one inter-session gap, then space each subsequent backfill by
        # intra_session_delay[0] — so they form a natural future session
        # instead of firing immediately.
        # Cap by remaining daily budget to prevent over-scheduling.
        if backfill_count > 0 and not stop_account:
            _settings = get_settings()
            _inter_gap = _settings.inter_session_delay[0]   # seconds (e.g. 2700 = 45 min)
            _intra_gap = _settings.intra_session_delay[0]   # seconds (e.g. 120 = 2 min)
            _sent_today = await repo.get_daily_requests_sent(account.id)
            _future_scheduled = await repo.count_future_scheduled_leads(campaign.id)
            _available = max(0, account.daily_limit - _sent_today - _future_scheduled)
            _actual_backfills = min(backfill_count, _available)
            if _actual_backfills < backfill_count:
                logger.info(
                    "dispatch.backfills_budget_capped",
                    campaign=campaign.name,
                    requested=backfill_count,
                    scheduled=_actual_backfills,
                    daily_limit=account.daily_limit,
                    sent_today=_sent_today,
                    future_scheduled=_future_scheduled,
                )
            _anchor = datetime.utcnow() + timedelta(seconds=_inter_gap)
            # Clamp backfill anchor to account's work window so leads are never
            # scheduled overnight. If anchor falls before today's window, push
            # to window start. If after today's window, push to tomorrow's start.
            if account.timezone:
                try:
                    from zoneinfo import ZoneInfo
                    _bkf_tz = ZoneInfo(account.timezone)
                    _bkf_settings = get_settings()
                    _bkf_day = _anchor.date()
                    _ws = datetime(
                        _bkf_day.year, _bkf_day.month, _bkf_day.day,
                        _bkf_settings.work_start_hour, 0, tzinfo=_bkf_tz,
                    ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                    _we = datetime(
                        _bkf_day.year, _bkf_day.month, _bkf_day.day,
                        _bkf_settings.work_end_hour, 0, tzinfo=_bkf_tz,
                    ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                    if _anchor < _ws:
                        _anchor = _ws
                    elif _anchor >= _we:
                        _tmrw = _bkf_day + timedelta(days=1)
                        _anchor = datetime(
                            _tmrw.year, _tmrw.month, _tmrw.day,
                            _bkf_settings.work_start_hour, 0, tzinfo=_bkf_tz,
                        ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                except Exception:
                    pass  # keep original anchor if timezone logic fails
            _scheduled = 0
            for _i in range(_actual_backfills):
                _pending = await repo.get_pending_leads(campaign.id, limit=1)
                if not _pending:
                    break
                _bl = _pending[0]
                _slot_time = _anchor + timedelta(seconds=_i * _intra_gap)
                await repo.update_lead(
                    _bl,
                    campaign_id_override=campaign.id,
                    status=LeadStatus.SCHEDULED,
                    scheduled_at=_slot_time,
                )
                _scheduled += 1
            if _scheduled:
                logger.info(
                    "dispatch.backfills_scheduled",
                    campaign=campaign.name,
                    count=_scheduled,
                    first_slot=_anchor.strftime("%H:%M UTC"),
                )

        if successful_sends > 0:
            # At least one lead went through — session is confirmed healthy.
            # Record session end time so the inter-session gap is enforced
            # before the next dispatch cycle processes this account.
            pool.confirm_session(account.id)
            _last_session_end[account.id] = datetime.utcnow()
            day_sent = sent_today_count + successful_sends
            day_target = target + sent_today_count  # target was remaining at cycle start
            logger.info(
                "dispatch.cycle_complete",
                campaign=campaign.name,
                cycle_sent=successful_sends,
                day_sent=day_sent,
                day_target=day_target,
                day_remaining=max(0, day_target - day_sent),
            )
        _last_session_end[account.id] = datetime.utcnow()
        # Inter-session gap is enforced via _last_session_end (in-memory) in the
        # main dispatcher loop — no need to write paused_until here. Writing it
        # would block the broadcast dispatcher (which reads paused_until as a
        # cooldown guard) for the full inter-session window.

        if stop_account:
            break


async def daily_planning_sweep():
    """
    Generate today's plan for all active accounts/campaigns.

    Runs hourly (IntervalTrigger). Per-account logic:
    - Uses the account's local date (not server date) so a Tokyo account
      plans for the correct calendar day even when the server is in Berlin.
    - Skips if leads are already scheduled for today (future_scheduled > 0)
      and the work window has already started — the dispatcher and backfill
      logic handle the rest from that point.
    - Passes effective_start=now when called after the account's work window
      start (e.g. container restart mid-day) so the planner spreads remaining
      slots across the rest of the day rather than bursting them all at once.
    """
    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        for account in accounts:
            # Continuous-mode accounts (default) manage their own scheduling — no planning needed.
            # Only accounts with an explicit 'planned' dispatch_mode use the pre-scheduled slot system.
            if account.dispatch_mode != "planned":
                continue

            if account.paused_until and not is_cooldown_expired(account.paused_until):
                logger.info(
                    "planner.account_paused",
                    account=account.name,
                    until=str(account.paused_until),
                )
                continue

            # Compute account's local "today" so the plan is always anchored to
            # the correct calendar date regardless of the server's timezone.
            acct_now_aware = _acct_local_now(account)
            acct_today = acct_now_aware.date()

            campaigns = await repo.get_active_campaigns(account.id)
            for campaign in campaigns:
                now = datetime.utcnow()

                # Reset any stale past-scheduled leads back to PENDING.
                stale = await repo.reset_stale_scheduled_leads(campaign.id)
                if stale:
                    logger.info(
                        "planner.stale_reset",
                        campaign=campaign.name,
                        count=stale,
                    )

                # Determine the account's work window boundaries in UTC.
                _settings = get_settings()
                _ws_utc = None
                _we_utc = None
                try:
                    from zoneinfo import ZoneInfo
                    _acct_tz = ZoneInfo(account.timezone or "UTC")
                    _ws_utc = datetime(
                        acct_today.year, acct_today.month, acct_today.day,
                        _settings.work_start_hour, 0, tzinfo=_acct_tz,
                    ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                    _we_utc = datetime(
                        acct_today.year, acct_today.month, acct_today.day,
                        _settings.work_end_hour, 0, tzinfo=_acct_tz,
                    ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                except Exception:
                    pass

                # Past the work window — stale_reset already ran above, but
                # don't generate new plans. Tomorrow's morning run handles it.
                if _we_utc is not None and now > _we_utc:
                    continue

                pending = await repo.get_pending_leads(campaign.id)
                if not pending:
                    continue

                lead_ids = [l.id for l in pending]

                sent_today = await repo.get_daily_requests_sent(account.id)
                future_scheduled = await repo.count_future_scheduled_leads(campaign.id)
                remaining_budget = max(0, account.daily_limit - sent_today - future_scheduled)

                if remaining_budget == 0:
                    logger.info(
                        "planner.budget_exhausted_skip",
                        account=account.name,
                        daily_limit=account.daily_limit,
                        sent_today=sent_today,
                        future_scheduled=future_scheduled,
                    )
                    continue

                # If a full daily plan was already generated today, don't re-plan.
                # NOTE: do NOT use future_scheduled > 0 here — the dispatcher's
                # backfill mechanism keeps exactly 1 lead in SCHEDULED state at
                # all times, which would make future_scheduled always > 0 and
                # permanently block the planner from generating a real plan.
                if await repo.plan_generated_today(campaign.id, acct_today):
                    continue

                # effective_start: None before the work window (standard morning plan,
                # sessions distributed across the full window); now_utc after the
                # window has started (container restart, spread remaining slots forward).
                effective_start = now if (_ws_utc is not None and now > _ws_utc) else None

                plan = generate_daily_plan(
                    account_id=account.id,
                    day=acct_today,
                    pending_lead_ids=lead_ids,
                    daily_limit=account.daily_limit,
                    timezone_str=account.timezone,
                    campaign_weekend_enabled=campaign.weekend_enabled,
                    effective_start=effective_start,
                    remaining_budget=remaining_budget,
                )

                scheduled_count = 0
                for slot in plan:
                    if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                        await repo.update_lead_schedule(
                            slot.lead_id, slot.scheduled_at, LeadStatus.SCHEDULED
                        )
                        scheduled_count += 1

                # Log the plan (skip empty plans to avoid activity clutter)
                if scheduled_count > 0:
                    await repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        action_type=ActionType.DAILY_PLAN_GENERATED,
                        status=ActionLogStatus.SUCCESS,
                        details={
                            "date": acct_today.isoformat(),
                            "total_slots": len(plan),
                            "connection_requests": scheduled_count,
                        },
                    )

                logger.info(
                    "planner.campaign_planned",
                    campaign=campaign.name,
                    slots=len(plan),
                )

    except Exception as e:
        logger.error("planner.sweep_failed", error=str(e))
    finally:
        await session.close()


async def dispatch():
    """
    Execute due actions.

    Runs every 5 minutes. Picks up leads where scheduled_at <= now()
    and executes them.
    """
    repo, session = await _get_repo()
    try:
        now = datetime.utcnow()  # planner stores UTC naive; compare with utcnow()
        accounts = await repo.list_active_accounts()

        for account in accounts:
            # Skip paused accounts
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            # Continuous is the default. Only 'planned' opts into the legacy pre-scheduled slot system.
            _is_continuous = account.dispatch_mode != "planned"

            if _is_continuous:
                # ── Continuous mode: stateless pacing from DB progress ────────
                from releasi.scheduler import pacing

                _settings = get_settings()
                _window = _continuous_window(account)
                _sent_now = await repo.get_daily_requests_sent(account.id)
                _last_send = await repo.get_last_connection_request_at(account.id)
                # A session that sent nothing (errors, no pending) still counts for the
                # min-gap so a failing account is not retried every 5 minutes.
                _mem_end = _last_session_end.get(account.id)
                if _mem_end is not None and (_last_send is None or _mem_end > _last_send):
                    _last_send = _mem_end
                _decision = pacing.decide(
                    now, _window, _sent_now, account.daily_limit, _last_send,
                    tuple(_settings.continuous_batch_size),
                )
                if not _decision.run:
                    logger.debug(
                        "dispatch.pacing_wait",
                        account=account.name,
                        reason=_decision.reason,
                        sent=_sent_now,
                        target=round(_decision.target_now, 1),
                    )
                    continue
                logger.info(
                    "dispatch.pacing_run",
                    account=account.name,
                    reason=_decision.reason,
                    sent=_sent_now,
                    target=round(_decision.target_now, 1),
                    batch=_decision.batch_size,
                    urgent=_decision.urgent,
                )
            else:
                # ── Planned mode: fixed minimum inter-session gap ─────────────
                _min_gap = get_settings().inter_session_delay[0]  # e.g. 2700s = 45 min
                _last_end = _last_session_end.get(account.id)
                if _last_end is not None:
                    _elapsed = (now - _last_end).total_seconds()
                    if _elapsed < _min_gap:
                        logger.debug(
                            "dispatch.inter_session_gap_enforced",
                            account=account.name,
                            elapsed_min=round(_elapsed / 60, 1),
                            gap_min=round(_min_gap / 60, 1),
                        )
                        continue

            # Check daily limits (both modes)
            sent_today_count = await repo.get_daily_requests_sent(account.id)
            if _is_continuous:
                # Continuous mode targets exactly daily_limit (no +/-15% variation).
                remaining = max(0, account.daily_limit - sent_today_count)
                can_send = remaining > 0
            else:
                can_send, remaining = await rate_limits.can_send_today(
                    account.id,
                    account.daily_limit,
                    sent_today_count,
                )

            if not can_send:
                logger.info("dispatch.daily_limit_reached", account=account.name)
                continue

            # Quick DB pre-check: skip browser entirely if no work to do.
            campaigns_preview = await repo.get_active_campaigns(account.id)

            # For continuous accounts, reset any stale scheduled leads back to
            # pending here since the planner (which normally does this) skips them.
            if _is_continuous:
                for _c in campaigns_preview:
                    stale = await repo.reset_stale_scheduled_leads(_c.id)
                    if stale:
                        logger.info(
                            "dispatch.stale_scheduled_reset",
                            account=account.name,
                            campaign=_c.name,
                            count=stale,
                        )

            any_due = False
            if _is_continuous:
                # Continuous: look for any PENDING leads.
                # Phase 2 read cutover: use CampaignLeadAssignment as source of truth.
                for _c in campaigns_preview:
                    _pending_check = await repo.get_pending_leads_via_assignments(_c.id, limit=1)
                    if _pending_check:
                        any_due = True
                        break
            else:
                # Planned: look for SCHEDULED leads past their slot time
                for _c in campaigns_preview:
                    if await repo.get_scheduled_leads(_c.id, before=now):
                        any_due = True
                        break
            if not any_due:
                continue

            # Acquire pool browser once per account. Timeout prevents a stuck
            # per-slot asyncio.Lock (from an earlier crashed/hung acquirer) from
            # blocking this dispatcher cycle forever.
            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await asyncio.wait_for(pool.acquire(account), timeout=90)
            except asyncio.TimeoutError:
                logger.error(
                    "dispatch.pool_acquire_timeout",
                    account=account.name,
                    hint="stuck slot lock — will retry next cycle",
                )
                from releasi.campaign.withdrawal import report_browser_wait
                await report_browser_wait(account)
                continue
            except Exception as e:
                logger.error("dispatch.pool_acquire_failed", account=account.name, error=str(e))
                continue

            from releasi.campaign.withdrawal import clear_browser_wait
            clear_browser_wait(account.id)
            try:
                # Pre-dispatch browser session check: navigate to feed once
                # using the real Playwright browser before touching any leads.
                # Skipped when the session was recently confirmed (< 30 min ago)
                # to avoid loading LinkedIn on every 5-min cycle.
                #   - redirect to /login  → SESSION_EXPIRED  → mark cookie_expired
                #   - navigation timeout  → network/proxy error → skip cycle, retry in 5 min
                from releasi.linkedin.pool import _VALIDATION_COOLDOWN as _VC
                _seconds_since_check = pool.time_since_validated(account.id)
                if _seconds_since_check >= _VC:
                    _pre_page = await pool_context.new_page()
                    try:
                        from releasi.linkedin.navigator import LinkedInNavigator
                        _nav = LinkedInNavigator(_pre_page)
                        _feed = await _nav.go_to_feed()
                    finally:
                        await _pre_page.close()

                    if not _feed.success:
                        logger.warning(
                            "dispatch.pre_check_network_error",
                            account=account.name,
                            error=_feed.error,
                        )
                        # Proxy/network issue — skip this cycle, will retry in 5 min.
                        # Skip egress lookup (proxy itself may be down).
                        await _record_session_touch(
                            pool_context, account, repo,
                            job="dispatcher", result="network_error",
                            detail={"error": _feed.error}, fetch_egress=False,
                        )
                        continue
                    if not _feed.session_valid:
                        logger.error("dispatch.pre_check_session_expired", account=account.name)
                        # Capture the full context at the moment of expiry — egress
                        # IP/geo + cookie fingerprint — this is the row that explains *why*.
                        await _record_session_touch(
                            pool_context, account, repo,
                            job="dispatcher", result="redirect_login",
                            detail={"phase": "pre_dispatch_feed_ping"},
                        )
                        # Try silent re-auth from li_rm before giving up. If it
                        # works the account stays active — no manual re-login.
                        if await _attempt_li_rm_reauth(pool_context, account, repo):
                            logger.info("dispatch.reauth_ok", account=account.name)
                            pool.confirm_session(account.id)
                            await slack_notify(
                                f":white_check_mark: *Session auto-healed* — *{account.name}* "
                                "re-minted li_at from li_rm (no manual login needed)."
                            )
                            continue  # session healthy again; dispatch resumes next cycle
                        await _mark_cookie_expired(account, repo, "dispatch pre-check (li_rm re-auth failed)")
                        await repo.log_action(
                            account_id=account.id,
                            action_type=ActionType.ERROR,
                            status=ActionLogStatus.FAILED,
                            details={"reason": "pre_dispatch_session_expired"},
                        )
                        continue
                    # Feed navigation confirmed session healthy — stamp validated time
                    pool.confirm_session(account.id)
                    await repo.add_proxy_mb(account.id, 1.0)  # feed pre-check page
                    # Ledger: healthy touch. Runs once per validation window
                    # (~30 min), so the egress lookup cost is bounded. Also
                    # writes back a rotated li_at to the DB.
                    await _record_session_touch(
                        pool_context, account, repo,
                        job="dispatcher", result="ok",
                    )
                else:
                    logger.debug(
                        "dispatch.pre_check_skipped",
                        account=account.name,
                        seconds_since_check=round(_seconds_since_check),
                    )

                # ── Route to continuous or planned dispatch ───────────────
                if _is_continuous:
                    sent = await _dispatch_continuous(
                        account, repo, pool_context, remaining, _decision.batch_size,
                    )
                    if sent > 0:
                        pool.confirm_session(account.id)
                    _last_session_end[account.id] = datetime.utcnow()
                    # Inter-session gap is derived from the last CONNECTION_REQUEST
                    # row in action_log (pacing.decide), so it survives restarts.
                    # Don't write paused_until — broadcast dispatcher reads it
                    # as a real cooldown and would skip for the full gap window.
                else:
                    # ── Legacy planned-mode dispatch (explicit dispatch_mode='planned' only) ──
                    await _dispatch_planned(account, repo, pool_context, pool, remaining, now, sent_today_count)
            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("dispatch.failed", error=str(e))
    finally:
        await session.close()


async def pacing_watchdog():
    """Alert when connection-request volume is falling behind, instead of discovering it later.

    Runs every 30 min. Per continuous-mode account, during the work window it flags:
      - sent < 60% of the linear expectation (after 25% of the window has passed)
      - nothing to send (no pending leads)
      - dispatch blocked by a cooldown/pause
    After the window closes it posts one daily recap with the week-to-date count.
    Each alert fires at most once per account per local day per kind.
    """
    from releasi.scheduler import pacing

    repo, session = await _get_repo()
    try:
        now = datetime.utcnow()
        for account in await repo.list_active_accounts():
            if account.dispatch_mode == "planned":
                continue
            local_date = _acct_local_now(account).date()
            window = _continuous_window(account)
            work_start, work_end = window
            sent = await repo.get_daily_requests_sent(account.id)
            limit = account.daily_limit
            if sent == 0 and local_date.weekday() not in get_settings().work_days:
                continue  # non-work day with no activity: nothing to flag

            def _once(kind: str) -> bool:
                key = (account.id, local_date, kind)
                if key in _pacing_alerts_sent:
                    return False
                _pacing_alerts_sent.add(key)
                return True

            if work_start <= now < work_end:
                span = (work_end - work_start).total_seconds()
                frac = (now - work_start).total_seconds() / span
                expected = pacing.expected_by_now(now, window, limit)
                if account.paused_until and not is_cooldown_expired(account.paused_until):
                    if frac >= 0.25 and _once("paused"):
                        await slack_notify(
                            f":pause_button: *{account.name}* paused until {account.paused_until} UTC "
                            f"with {sent}/{limit} sent today."
                        )
                    continue
                if frac >= 0.25 and sent < 0.6 * expected and _once("behind"):
                    await slack_notify(
                        f":warning: *{account.name}* is behind pace: {sent}/{limit} sent, "
                        f"expected about {round(expected)} by now ({round(frac * 100)}% of the work window)."
                    )
                if sent < limit:
                    has_pending = False
                    for c in await repo.get_active_campaigns(account.id):
                        if await repo.get_pending_leads_via_assignments(c.id, limit=1):
                            has_pending = True
                            break
                    if not has_pending and _once("no_pending"):
                        await slack_notify(
                            f":inbox_tray: *{account.name}* has no pending leads to send "
                            f"({sent}/{limit} today). Add or unpause a lead list."
                        )
            elif now >= work_end and _once("recap"):
                week_start = local_date - timedelta(days=local_date.weekday())
                week_sent = await repo.get_weekly_request_count(account.id, week_start)
                mark = ":white_check_mark:" if sent >= limit else ":warning:"
                await slack_notify(
                    f"{mark} *{account.name}* daily recap: {sent}/{limit} connection requests. "
                    f"Week to date: {week_sent} (full week at this limit: {limit * 5} on weekdays)."
                )
    except Exception as e:
        logger.error("pacing_watchdog.failed", error=str(e))
    finally:
        await session.close()


async def prune_debug_screenshots(max_age_days: int = 7) -> None:
    """Delete debug screenshots older than `max_age_days`.

    We take screenshots on every DOM-mismatch failure path (Connect not
    found, dropdown empty, send button missing, etc.). Left unattended
    the directory grows without bound — it was ~55 MB / ~1,600 files
    the day this pruner shipped. Anything older than a week is beyond
    the useful investigation window.
    """
    from pathlib import Path
    ss_dir = Path("data/debug_screenshots")
    if not ss_dir.exists():
        return
    cutoff = datetime.utcnow().timestamp() - max_age_days * 86400
    removed = 0
    freed = 0
    for p in ss_dir.iterdir():
        if not p.is_file():
            continue
        try:
            st = p.stat()
            if st.st_mtime < cutoff:
                freed += st.st_size
                p.unlink()
                removed += 1
        except Exception:
            continue
    if removed:
        logger.info(
            "screenshots.pruned",
            removed=removed,
            freed_mb=round(freed / (1024 * 1024), 2),
            max_age_days=max_age_days,
        )


async def check_cooldowns():
    """
    Check if paused accounts can resume.

    Runs daily. When cooldown expires, verifies with LinkedIn
    invitation manager. If still blocked, pushes to next day.
    """
    repo, session = await _get_repo()
    try:
        accounts = await repo.list_paused_accounts()
        for account in accounts:
            if not is_cooldown_expired(account.paused_until):
                continue

            logger.info("cooldown.checking", account=account.name)

            # For now, just clear cooldown and let the dispatcher verify
            # A future improvement could check the invitation manager page
            await repo.update_account(account, paused_until=None)
            await repo.bulk_update_lead_status_for_account(
                account.id,
                from_status=LeadStatus.LIMIT_PAUSED,
                to_status=LeadStatus.PENDING,
            )

            await repo.log_action(
                account_id=account.id,
                action_type=ActionType.COOLDOWN_ENDED,
                status=ActionLogStatus.SUCCESS,
                details={"resumed_at": datetime.utcnow().isoformat()},
            )
            logger.info("cooldown.resumed", account=account.name)

    except Exception as e:
        logger.error("cooldown.check_failed", error=str(e))
    finally:
        await session.close()


async def check_acceptances():
    """
    Check for accepted connection requests.

    Strategy (zero individual profile visits):
      1. Connections page (sorted by recently added): scroll until we hit cards
         older than CUTOFF_HOURS. Every slug found → mark lead CONNECTED.
      2. Invitation manager diff: find CONNECTION_REQUESTED leads that have
         disappeared from the pending list AND were not found in step 1 → mark
         WITHDRAWN (declined or expired). No profile visits needed.
      3. Auto-withdraw: if withdraw_threshold set, withdraw oldest invitations
         while already on the invitation manager page.

    Runs hourly (IntervalTrigger). Per-account: fires once per local day at
    acceptance_check_hour (default 10 AM in account timezone).
    """
    from releasi.linkedin.actions import LinkedInActions

    # Cutoff: how far back to scan the connections page.  72h (3-day window) means
    # we catch connections accepted even if the checker missed yesterday's run, and
    # covers accounts that only connected in the last 1-2 days.
    CUTOFF_HOURS = 72.0

    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            acct_now = _acct_local_now(account)
            acct_today = acct_now.date()
            check_hour = get_settings().acceptance_check_hour
            if acct_now.hour != check_hour:
                continue
            if _acceptance_checked.get(account.id) == acct_today:
                continue

            # Include paused campaigns — acceptances while paused still need recording
            campaigns = await repo.get_operational_campaigns(account.id)
            # Phase 2 read cutover: filter via CampaignLeadAssignment.
            # Store (lead, campaign) tuples so the campaign is always available
            # even after Phase 3a collapse when lead.campaign_id is NULL.
            requested_lead_pairs: list[tuple] = []
            for campaign in campaigns:
                leads = await repo.get_leads_by_status_via_assignments(
                    campaign.id, LeadStatus.CONNECTION_REQUESTED
                )
                for lead in leads:
                    requested_lead_pairs.append((lead, campaign))

            needs_browser = bool(requested_lead_pairs) or bool(account.withdraw_threshold)
            if not needs_browser:
                _acceptance_checked[account.id] = acct_today
                continue

            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await pool.acquire(account)
            except Exception as e:
                logger.error("acceptance.pool_acquire_failed", account=account.name, error=str(e))
                continue

            try:
                # ── Step 1: connections page → find recent acceptances ────────
                conn_page = await pool_context.new_page()
                conn_actions = LinkedInActions(conn_page)
                conn_result = await conn_actions.get_recent_connections(
                    cutoff_hours=CUTOFF_HOURS
                )
                await conn_page.close()

                if not conn_result.session_valid:
                    logger.error("acceptance.session_expired", account=account.name)
                    await _record_session_touch(
                        pool_context, account, repo,
                        job="acceptance", result="redirect_login",
                    )
                    # Try silent re-auth from li_rm before marking expired.
                    if await _attempt_li_rm_reauth(pool_context, account, repo):
                        logger.info("acceptance.reauth_ok", account=account.name)
                        pool.confirm_session(account.id)
                        await slack_notify(
                            f":white_check_mark: *Session auto-healed* — *{account.name}* "
                            "re-minted li_at from li_rm (detected by acceptance checker)."
                        )
                        continue
                    await _mark_cookie_expired(account, repo, "acceptance checker")
                    await repo.log_action(
                        account_id=account.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": "acceptance_checker_session_expired"},
                    )
                    continue

                # Build set of recently-accepted slugs from the connections page
                recent_slugs = set(conn_result.slugs)  # e.g. {"/in/john-doe", ...}
                newly_connected = []  # (lead, campaign) pairs

                if requested_lead_pairs and conn_result.success:
                    for lead, campaign in requested_lead_pairs:
                        slug = _normalize_li_url(lead.linkedin_url)
                        if not slug or slug not in recent_slugs:
                            continue
                        try:
                            validate_transition(lead.status, LeadStatus.CONNECTED)
                        except InvalidTransition:
                            # Lead already in a post-connection state (e.g. connected,
                            # followup_scheduled). Sync the CLA so the dispatcher won't
                            # re-queue this lead, then skip — no new stats or event.
                            logger.warning(
                                "acceptance.already_past_connected_skip",
                                url=lead.linkedin_url,
                                lead_status=lead.status.value if hasattr(lead.status, "value") else str(lead.status),
                            )
                            await repo.update_lead(lead, campaign_id_override=campaign.id, status=lead.status)
                            continue
                        await repo.update_lead(
                            lead,
                            campaign_id_override=campaign.id,
                            status=LeadStatus.CONNECTED,
                            connection_accepted_at=datetime.utcnow(),
                        )
                        await repo.increment_daily_stat(account.id, "connections_accepted")
                        newly_connected.append((lead, campaign))
                        logger.info("acceptance.connected", url=lead.linkedin_url)

                pool.confirm_session(account.id)
                logger.info(
                    "acceptance.connections_page_done",
                    account=account.name,
                    recent_slugs=len(recent_slugs),
                    newly_connected=len(newly_connected),
                    hit_cutoff=conn_result.hit_cutoff,
                )

                # ── One summary log entry for this run (replaces per-lead entries) ──
                await repo.log_action(
                    account_id=account.id,
                    action_type=ActionType.ACCEPTANCE_CHECK_SUMMARY,
                    status=ActionLogStatus.SUCCESS,
                    details={
                        "accepted": len(newly_connected),
                        "scanned": len(recent_slugs),
                        "hit_cutoff": conn_result.hit_cutoff,
                        "cutoff_hours": CUTOFF_HOURS,
                    },
                )

                # ── Step 1b: harvest Contact Info (email + phone) for newly connected leads ──
                # One page load per accepted lead using the already-acquired browser context.
                # Fail-open: any error is logged but does NOT abort the acceptance run.
                if newly_connected:
                    contact_page = await pool_context.new_page()
                    contact_actions = LinkedInActions(contact_page)
                    for lead, _campaign in newly_connected:
                        try:
                            slug = lead.linkedin_url.split("/in/")[-1].strip("/")
                            info = await contact_actions.get_contact_info(slug)
                            if info.get("email") or info.get("phone"):
                                await repo.update_lead(
                                    lead,
                                    campaign_id_override=_campaign.id,
                                    email=info.get("email"),
                                    phone=info.get("phone"),
                                )
                                logger.info(
                                    "acceptance.contact_info_saved",
                                    url=lead.linkedin_url,
                                    has_email=bool(info.get("email")),
                                    has_phone=bool(info.get("phone")),
                                )
                            await asyncio.sleep(random.uniform(2.0, 3.5))
                        except Exception as e:
                            logger.warning(
                                "acceptance.contact_info_failed",
                                url=lead.linkedin_url,
                                error=str(e),
                            )
                    await contact_page.close()

                # ── Step 2: schedule follow-ups for newly connected leads ────
                for lead, campaign in newly_connected:
                    if not campaign.followup_enabled:
                        continue
                    _dnc = await suppression.check_lead(repo.session, lead)
                    if _dnc:
                        logger.warning(
                            "followup.suppressed_do_not_contact",
                            url=lead.linkedin_url, rule=_dnc,
                        )
                        continue  # stays CONNECTED, no follow-up scheduled
                    has_messages = any(
                        getattr(campaign, f"followup_message_{i}", None)
                        for i in (1, 2, 3)
                    )
                    if not has_messages:
                        continue

                    delay_hours = campaign.followup_delay_hours or 0
                    if delay_hours > 0:
                        followup_time = datetime.utcnow() + timedelta(hours=delay_hours)
                        validate_transition(lead.status, LeadStatus.FOLLOWUP_SCHEDULED)
                        await repo.update_lead(
                            lead,
                            campaign_id_override=campaign.id,
                            status=LeadStatus.FOLLOWUP_SCHEDULED,
                            scheduled_at=followup_time,
                        )
                        logger.info(
                            "followup.scheduled",
                            url=lead.linkedin_url,
                            campaign=campaign.name,
                            scheduled_at=followup_time.isoformat(),
                            delay_hours=delay_hours,
                        )
                    else:
                        # Send immediately (delay=0) — use existing pool context (already acquired)
                        if not await repo.is_lead_visible(lead.id):
                            continue
                        try:
                            from releasi.campaign.executor import CampaignExecutor
                            executor = CampaignExecutor(repo, browser_context=pool_context)
                            logger.info(
                                "followup.starting_sequence",
                                url=lead.linkedin_url,
                                campaign=campaign.name,
                            )
                            fu_result = await executor.execute_followup_sequence(
                                account, campaign, lead
                            )
                            if fu_result["success"]:
                                validate_transition(lead.status, LeadStatus.FOLLOWUP_SENT)
                                await repo.update_lead(
                                    lead,
                                    campaign_id_override=campaign.id,
                                    status=LeadStatus.FOLLOWUP_SENT,
                                    followup_sent_at=datetime.utcnow(),
                                )
                                logger.info(
                                    "followup.sequence_done",
                                    url=lead.linkedin_url,
                                    messages_sent=fu_result["messages_sent"],
                                )
                            else:
                                logger.warning("followup.sequence_failed", url=lead.linkedin_url)
                            if fu_result.get("fatal"):
                                break
                        except Exception as e:
                            logger.error("followup.immediate_failed", url=lead.linkedin_url, error=str(e))

                # Mark as checked for today so subsequent hourly runs skip this account
                _acceptance_checked[account.id] = acct_today

            except Exception as e:
                # Classify per-account exceptions so a redirect loop on the
                # connections page surfaces as cookie_expired instead of
                # silently logging and moving on. Other accounts in the
                # loop continue uninterrupted.
                err_str = str(e)
                logger.error(
                    "acceptance.account_failed",
                    account=account.name,
                    error=err_str,
                )
                from releasi.safety.error_signals import is_session_expired_signal
                if is_session_expired_signal(err_str):
                    logger.error("acceptance.session_expired_detected", account=account.name)
                    try:
                        await _mark_cookie_expired(account, repo, "acceptance checker (exception)")
                    except Exception as inner:
                        logger.warning("acceptance.cookie_expired_update_failed", error=str(inner))
            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("acceptance.check_failed", error=str(e))
    finally:
        await session.close()


async def dispatch_followups():
    """
    Send follow-up messages for leads where the delay has elapsed.

    Runs every 30 minutes. Picks up FOLLOWUP_SCHEDULED leads where
    scheduled_at <= now and sends the follow-up message sequence.
    """
    repo, session = await _get_repo()
    try:
        now = datetime.utcnow()
        accounts = await repo.list_active_accounts()

        settings = get_settings()

        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            campaigns = await repo.get_active_campaigns(account.id)

            # ── Unified per-lead daily cap pre-check ──────────────────────────
            # Distinct leads that received a message today (follow-up OR
            # broadcast). Migrated 2026-08-30 from the legacy
            # settings.followup_daily_cap + get_followup_sequences_started_today
            # pair — semantics preserved (3-message sequence to one lead = 1
            # against the cap) while now correctly co-counting broadcast sends.
            sent_today = await repo.get_distinct_message_recipients_today(account.id)
            daily_cap = account.daily_message_limit or 15
            if sent_today >= daily_cap:
                logger.info(
                    "followup.daily_cap_reached",
                    account=account.name,
                    sent_today=sent_today,
                    cap=daily_cap,
                )
                continue

            # Check if any campaign has due follow-ups before acquiring pool
            # Phase 2 read cutover: use CampaignLeadAssignment as source of truth.
            has_due = False
            for campaign in campaigns:
                if campaign.followup_enabled:
                    due = await repo.get_followup_due_leads_via_assignments(campaign.id, before=now)
                    if due:
                        has_due = True
                        break
            if not has_due:
                continue

            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await pool.acquire(account)
            except Exception as e:
                logger.error("followup_dispatch.pool_acquire_failed", account=account.name, error=str(e))
                continue

            try:
                from releasi.campaign.executor import CampaignExecutor
                executor = CampaignExecutor(repo, browser_context=pool_context)

                # Track sends in this dispatch cycle so we respect the cap even
                # if DailyStat writes haven't been flushed yet.
                sent_this_cycle = 0
                remaining_cap = daily_cap - sent_today

                # Mirror the connection-request dispatcher's session-health
                # heuristics. Before this was added, 5+ followups would fail
                # in a row with ERR_TOO_MANY_REDIRECTS (login redirect loop =
                # expired cookie) and each lead was just marked ERROR without
                # ever flagging the account as cookie_expired.
                consecutive_session_errors = 0
                consecutive_network_errors = 0
                stop_account = False

                for campaign in campaigns:
                    if stop_account:
                        break
                    if not campaign.followup_enabled:
                        continue
                    if remaining_cap <= 0:
                        logger.info(
                            "followup.daily_cap_reached_mid_cycle",
                            account=account.name,
                            sent_today=sent_today,
                            sent_this_cycle=sent_this_cycle,
                            cap=daily_cap,
                        )
                        break

                    due_leads = await repo.get_followup_due_leads_via_assignments(campaign.id, before=now)
                    if not due_leads:
                        continue

                    for lead in due_leads:
                        if remaining_cap <= 0:
                            break

                        # ── Guard: re-fetch lead status before acting ──────────
                        # Prevents acting on stale data from the batch query above
                        # (race condition, concurrent job, or a prior cycle that
                        # updated the status after the batch was fetched).
                        fresh = await repo.get_lead_by_id(lead.id)
                        if fresh is None:
                            logger.warning("followup.lead_vanished", url=lead.linkedin_url)
                            continue
                        if fresh.status != LeadStatus.FOLLOWUP_SCHEDULED:
                            logger.warning(
                                "followup.stale_status_skipped",
                                url=lead.linkedin_url,
                                expected="FOLLOWUP_SCHEDULED",
                                actual=fresh.status.value,
                            )
                            continue
                        if fresh.followup_sent_at is not None:
                            logger.warning(
                                "followup.already_sent_skipped",
                                url=lead.linkedin_url,
                                followup_sent_at=str(fresh.followup_sent_at),
                            )
                            continue
                        # Use the freshly-fetched object for all subsequent ops
                        lead = fresh

                        if not await repo.is_lead_visible(lead.id):
                            continue

                        logger.info(
                            "followup.starting_sequence",
                            url=lead.linkedin_url,
                            campaign=campaign.name,
                        )
                        fu_result = await executor.execute_followup_sequence(
                            account, campaign, lead
                        )
                        await repo.add_proxy_mb(account.id, 1.0)  # messaging page

                        intent = classify_followup_result(
                            fu_result,
                            consecutive_session_errors,
                            consecutive_network_errors,
                            account.name,
                            campaign.name,
                        )
                        consecutive_session_errors = intent.consecutive_session
                        consecutive_network_errors = intent.consecutive_network
                        await _apply_followup_intent(intent, repo, account, campaign, lead)
                        if intent.stop_account:
                            stop_account = True
                            break

                        if intent.lead_action == LeadAction.MARK_SENT:
                            if fu_result.get("success"):
                                sent_this_cycle += 1
                                remaining_cap -= 1
                                logger.info(
                                    "followup.sequence_done",
                                    url=lead.linkedin_url,
                                    messages_sent=fu_result["messages_sent"],
                                )
                            else:
                                logger.info(
                                    "followup.skipped_marked_sent",
                                    url=lead.linkedin_url,
                                )
            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("followup_dispatch.failed", error=str(e))
    finally:
        await session.close()


async def dispatch_broadcasts():
    """Send broadcast messages (message-only campaigns) for each active account.

    Runs every 30 minutes. For each active account:
      1. Skip if in cooldown or over the unified daily_message_limit.
      2. Cheap SQL pre-check: any due broadcast work at all? Skip cycle if not.
      3. Acquire per-account browser pool slot (serialises with connect + followup).
      4. Iterate active broadcasts, dispatching:
         a) First-message sends for status='pending' broadcast_leads.
         b) Sequence continuations for status='sent' broadcast_leads whose
            next_message_at <= now.
      5. Respect the unified message cap per lead-per-day (broadcasts + follow-ups).

    Mirrors dispatch_followups' shape but with:
      - Broadcast + broadcast_lead state (not campaign + lead)
      - Unified per-lead cap via account.daily_message_limit
      - Direct message action_log rows tagged with broadcast_id
    """
    repo, session = await _get_repo()
    try:
        now = datetime.utcnow()
        accounts = await repo.list_active_accounts()

        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            # ── Unified per-lead daily cap pre-check ──────────────────────
            # Distinct leads that received a message today (broadcast OR
            # follow-up). A 3-message sequence to one lead counts as one.
            distinct_today = await repo.get_distinct_message_recipients_today(account.id)
            daily_cap = account.daily_message_limit or 15
            remaining_cap = daily_cap - distinct_today
            if remaining_cap <= 0:
                logger.info(
                    "broadcast.daily_cap_reached",
                    account=account.name,
                    distinct_today=distinct_today,
                    cap=daily_cap,
                )
                continue

            # ── Cheap pre-check: any broadcast work due at all? ───────────
            if not await repo.account_has_broadcast_work(account.id, now):
                continue

            # ── Acquire browser pool slot ─────────────────────────────────
            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await pool.acquire(account)
            except Exception as e:
                logger.error(
                    "broadcast_dispatch.pool_acquire_failed",
                    account=account.name, error=str(e),
                )
                continue

            try:
                from releasi.campaign.executor import CampaignExecutor
                executor = CampaignExecutor(repo, browser_context=pool_context)

                # Load active broadcasts for this account
                broadcasts = await repo.list_active_broadcasts(account_id=account.id)

                # Mirror the followup dispatcher's session-health heuristics:
                # 3 consecutive session errors → mark account cookie_expired.
                # 3 consecutive network errors → skip cycle, don't penalise.
                consecutive_session_errors = 0
                consecutive_network_errors = 0
                sent_this_cycle = 0
                stop_account = False

                for broadcast in broadcasts:
                    if stop_account or remaining_cap <= 0:
                        break

                    # ── (a) First-message sends for pending leads ─────────
                    # Also pull continuations. Do first-sends first so a
                    # slow-to-drain sequence can't starve fresh leads.
                    #
                    # Fetch a small batch (2–5 leads per broadcast per cycle)
                    # to keep sends spread across the work window rather than
                    # blasted at cap in one cycle.
                    per_cycle_target = min(random.randint(2, 5), remaining_cap)

                    # Trial-sends baseline: count leads (not messages) contacted
                    # before this cycle so we can auto-pause after trial_sends_limit leads.
                    trial_prior_sent = 0
                    trial_first_sends_this_cycle = 0
                    if broadcast.trial_sends_limit is not None:
                        trial_prior_sent = await repo.count_broadcast_sent_leads(broadcast.id)

                    pending = await repo.get_pending_broadcast_leads(
                        broadcast.id, limit=per_cycle_target * 2  # oversample for skips
                    )
                    due_continuations = await repo.get_due_broadcast_leads_for_next_message(
                        broadcast.id, before=now, limit=per_cycle_target * 2
                    )

                    # Interleave: alternate pending / continuations so both progress.
                    work_queue: list = []
                    p_iter = iter(pending)
                    c_iter = iter(due_continuations)
                    while True:
                        p = next(p_iter, None)
                        c = next(c_iter, None)
                        if p is None and c is None:
                            break
                        if p is not None:
                            work_queue.append(p)
                        if c is not None:
                            work_queue.append(c)

                    if not work_queue:
                        continue

                    successful_sends_this_broadcast = 0
                    attempts = 0
                    max_attempts = per_cycle_target * 3

                    for bl in work_queue:
                        if remaining_cap <= 0:
                            break
                        if successful_sends_this_broadcast >= per_cycle_target:
                            break
                        if attempts >= max_attempts:
                            break
                        attempts += 1

                        # Pre-send status re-verification — guard against stale
                        # rows if a concurrent cycle already handled this lead.
                        fresh = await repo.get_broadcast_lead_by_id(bl.id)
                        if fresh is None:
                            continue
                        # Fresh must be in a dispatchable state
                        expected_next_index = (fresh.last_message_index or 0) + 1
                        if fresh.status not in ("pending", "sent"):
                            continue
                        if fresh.status == "sent" and (fresh.next_message_at is None or fresh.next_message_at > now):
                            continue

                        # Bounded per-lead execution — one hung Playwright
                        # can't freeze the dispatcher.
                        if not await repo.is_lead_visible(fresh.lead_id):
                            continue
                        try:
                            bc_result = await asyncio.wait_for(
                                executor.execute_broadcast_lead(account, broadcast, fresh),
                                timeout=180,
                            )
                        except asyncio.TimeoutError:
                            logger.error(
                                "broadcast_dispatch.execute_timeout",
                                broadcast=broadcast.name,
                                broadcast_lead_id=fresh.id,
                            )
                            bc_result = {
                                "success": False, "skipped": False, "fatal": False,
                                "session_expired": False, "network_error": False,
                                "reason": "execute_broadcast_lead_timeout",
                            }

                        # Session-health heuristics
                        if bc_result.get("network_error"):
                            consecutive_network_errors += 1
                            consecutive_session_errors = 0
                            if consecutive_network_errors >= 3:
                                logger.warning(
                                    "broadcast_dispatch.proxy_connectivity_issues",
                                    account=account.name,
                                )
                                stop_account = True
                                break
                        elif bc_result.get("session_expired") or bc_result.get("fatal"):
                            consecutive_session_errors += 1
                            consecutive_network_errors = 0
                            if bc_result.get("session_expired") or consecutive_session_errors >= 3:
                                logger.error(
                                    "broadcast_dispatch.session_expired",
                                    account=account.name,
                                    consecutive=consecutive_session_errors,
                                )
                                await _mark_cookie_expired(account, repo, "broadcast dispatch")
                                stop_account = True
                                break
                        else:
                            consecutive_session_errors = 0
                            consecutive_network_errors = 0

                        if bc_result.get("success"):
                            successful_sends_this_broadcast += 1
                            sent_this_cycle += 1
                            # Only count against remaining_cap on the FIRST
                            # message per lead per day — msg 2/3 to the same
                            # lead don't consume additional per-lead capacity.
                            if bc_result.get("message_index") == 1:
                                remaining_cap -= 1
                            await repo.add_proxy_mb(account.id, 1.0)  # messaging page

                            # Trial sends auto-pause: count leads (first messages only),
                            # not individual messages. Pause when trial_sends_limit
                            # leads have been contacted. Clears the limit so
                            # re-activating runs the full list without the cap.
                            if broadcast.trial_sends_limit is not None:
                                if bc_result.get("message_index") == 1:
                                    trial_first_sends_this_cycle += 1
                                total_leads_contacted = trial_prior_sent + trial_first_sends_this_cycle
                                if total_leads_contacted >= broadcast.trial_sends_limit:
                                    await repo.update_broadcast(
                                        broadcast,
                                        status=BroadcastStatus.PAUSED,
                                        trial_sends_limit=None,
                                    )
                                    await slack_notify(
                                        f":test_tube: *Broadcast trial paused* — "
                                        f"*{broadcast.name}* auto-paused after "
                                        f"{total_leads_contacted} trial lead(s). "
                                        f"Review the messages and re-activate when ready."
                                    )
                                    logger.info(
                                        "broadcast_dispatch.trial_pause",
                                        broadcast=broadcast.name,
                                        leads_contacted=total_leads_contacted,
                                    )
                                    stop_account = True
                                    break

                        # When delay_between_hours == 0, complete the full sequence
                        # in the same dispatch pass so msg2 fires immediately after
                        # msg1 rather than waiting for the next cycle (20-30 min gap).
                        if (
                            bc_result.get("success")
                            and broadcast.delay_between_hours == 0
                            and not stop_account
                        ):
                            while True:
                                cont = await repo.get_broadcast_lead_by_id(fresh.id)
                                if cont is None or cont.status != "sent":
                                    break
                                await asyncio.sleep(random.uniform(3, 8))
                                try:
                                    cont_result = await asyncio.wait_for(
                                        executor.execute_broadcast_lead(account, broadcast, cont),
                                        timeout=180,
                                    )
                                except asyncio.TimeoutError:
                                    logger.error(
                                        "broadcast_dispatch.execute_timeout",
                                        broadcast=broadcast.name,
                                        broadcast_lead_id=cont.id,
                                    )
                                    break
                                if cont_result.get("network_error"):
                                    consecutive_network_errors += 1
                                    consecutive_session_errors = 0
                                    if consecutive_network_errors >= 3:
                                        stop_account = True
                                    break
                                elif cont_result.get("session_expired") or cont_result.get("fatal"):
                                    consecutive_session_errors += 1
                                    consecutive_network_errors = 0
                                    if cont_result.get("session_expired") or consecutive_session_errors >= 3:
                                        await _mark_cookie_expired(account, repo, "broadcast dispatch")
                                        stop_account = True
                                    break
                                else:
                                    consecutive_session_errors = 0
                                    consecutive_network_errors = 0
                                if cont_result.get("success"):
                                    successful_sends_this_broadcast += 1
                                    sent_this_cycle += 1
                                    await repo.add_proxy_mb(account.id, 1.0)
                                    bc_result = cont_result
                                else:
                                    break

                        # Small delay between leads
                        await asyncio.sleep(random.uniform(3, 8))

                    # After processing this broadcast, check if it completed
                    if broadcast.status.value == "active":
                        try:
                            await repo.mark_broadcast_completed_if_done(broadcast.id)
                        except Exception:
                            pass  # non-fatal

                if sent_this_cycle > 0:
                    logger.info(
                        "broadcast_dispatch.cycle_done",
                        account=account.name,
                        sent=sent_this_cycle,
                        distinct_today_after=distinct_today + sent_this_cycle,
                    )
            finally:
                await pool.release_idle(account.id)
    except Exception as e:
        logger.error("broadcast_dispatch.failed", error=str(e))
    finally:
        await session.close()


async def withdraw_invitations_sweep():
    """Run configured auto-withdrawal through the same bounded, durable lifecycle."""
    from releasi.campaign.withdrawal import count_invitations, run_withdrawal_job, notify_withdrawal_issue
    from releasi.linkedin.withdrawal_jobs import get_withdrawal_job_store
    from releasi.linkedin.withdrawal_safety import MAX_WITHDRAWALS, WithdrawalStopped
    from releasi.linkedin.pool import BrowserQuarantinedError

    repo, session = await _get_repo()
    store = get_withdrawal_job_store()
    try:
        accounts = await repo.list_active_accounts()
        now = datetime.utcnow()
        for account in accounts:
            if account.withdraw_threshold is None:
                continue
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue
            interval_days = account.auto_withdraw_interval_days or 30
            if account.auto_withdraw_last_run is not None:
                if (now - account.auto_withdraw_last_run).total_seconds() < interval_days * 86400:
                    continue
            previous = store.read(account.id)
            if previous.get("status") in ("running", "error") or previous.get("needs_review"):
                continue
            pool = get_browser_pool()
            if pool.is_busy(account.id):
                continue
            try:
                job_id = store.claim(account.id, MAX_WITHDRAWALS, "oldest", automatic=True)
            except WithdrawalStopped:
                continue
            try:
                live_count = await count_invitations(account, pool=pool)
                if live_count < 0:
                    raise WithdrawalStopped("LinkedIn did not confirm the invitation count; no withdrawal was attempted.")
                await repo.update_account(
                    account, pending_invitations_count=live_count, auto_withdraw_last_run=now,
                )
                threshold = account.withdraw_threshold
                target = random.randint(int(threshold * 0.95), threshold)
                to_withdraw = min(max(0, live_count - target), MAX_WITHDRAWALS)
                if not to_withdraw:
                    store.update(account.id, status="done", count=0)
                    continue
                store.update(account.id, count=to_withdraw)
                result = await run_withdrawal_job(account.id, job_id, to_withdraw, "oldest")
                await repo.update_account(
                    account, pending_invitations_count=max(0, live_count - len(result["withdrawn"])),
                )
                logger.info("withdraw_sweep.finished", account=account.name,
                            status=result["status"], withdrawn=len(result["withdrawn"]))
            except asyncio.CancelledError:
                state = store.read(account.id)
                if state.get("job_id") == job_id:
                    store.update(account.id, status="error", needs_review=bool(state.get("in_flight")),
                                 error="Automatic withdrawal interrupted; no automatic retry will run.")
                raise
            except Exception as exc:
                state = store.read(account.id)
                if state.get("job_id") == job_id:
                    store.update(account.id, status="error",
                                 needs_review=bool(state.get("in_flight") or isinstance(exc, BrowserQuarantinedError)),
                                 error="Automatic withdrawal stopped; manual review is required before retrying.")
                await notify_withdrawal_issue(
                    account.name, f"Automatic withdrawal stopped for {account.name}. "
                    f"Job: {job_id}. Saved progress retained; automatic retries are held.",
                )
            finally:
                store.release(account.id, job_id)
    finally:
        await session.close()


async def telegram_enrichment_sweep():
    """Background Telegram enrichment: process one lead every 20 minutes.

    Picks the next unenriched lead (no tg_sweep_searched event, telegram_username
    IS NULL, in a list with tg_enrich_enabled=True), calls find_telegram(), and
    persists the result.

    If Telegram returns a FloodWaitError, the function sleeps for the requested
    duration inside the lock — subsequent ticks see the lock is held and exit
    immediately, so there is no pile-up. The lead is NOT marked as searched so
    it will be retried on the next attempt after the flood wait ends.

    Failures are fully isolated: an exception here does not affect any other
    APScheduler job.
    """
    settings = get_settings()
    if not all([settings.telegram_api_id, settings.telegram_api_hash, settings.telegram_session]):
        return  # Telegram not configured

    if _tg_sweep_lock.locked():
        return  # Still processing previous lead (e.g. mid flood-wait sleep)

    async with _tg_sweep_lock:
        # ── 1. Pick next lead ────────────────────────────────────────────────
        session_factory = get_session_factory()
        async with session_factory() as session:
            repo = Repository(session)
            lead = await repo.get_next_lead_for_tg_sweep()
            if not lead:
                return

            lead_id    = lead.id
            lead_name  = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
            twitter_url = lead.twitter_url
            company    = lead.company
            linkedin_url = lead.linkedin_url

        # ── 2. Resolve Telegram (DB session closed — can take tens of seconds) ─
        from releasi.telegram.resolver import find_telegram
        try:
            result = await find_telegram(
                name=lead_name,
                twitter_url=twitter_url,
                company=company,
                linkedin_url=linkedin_url,
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_str=settings.telegram_session,
            )
        except Exception as exc:
            logger.error("tg_sweep.find_error", lead_id=lead_id, error=str(exc))
            return

        # ── 3. Flood wait: sleep inside the lock, DO NOT write the event ──────
        if result.flood_wait_seconds:
            wait = result.flood_wait_seconds + 10
            logger.warning(
                "tg_sweep.flood_wait",
                lead_id=lead_id,
                lead_name=lead_name,
                wait_seconds=wait,
            )
            global _tg_flood_wait_until
            _tg_flood_wait_until = datetime.utcnow() + timedelta(seconds=wait)
            await asyncio.sleep(wait)
            _tg_flood_wait_until = None
            return  # Lead will be retried on the next tick

        # ── 4. Persist result ────────────────────────────────────────────────
        async with session_factory() as session:
            repo = Repository(session)
            lead_obj = await repo.get_lead_by_id(lead_id)
            if not lead_obj:
                return

            await repo.log_lead_event(
                lead_id=lead_id,
                event_type="tg_sweep_searched",
                details={
                    "found": result.best_match is not None,
                    "match": result.best_match,
                    "alternatives": result.alternatives,
                    "logs": result.logs[-5:],  # keep last 5 for brevity
                },
            )

            if result.best_match:
                await repo.update_lead(lead_obj, telegram_username=result.best_match)
                await repo.log_lead_event(
                    lead_id=lead_id,
                    event_type="telegram_found",
                    details={"username": result.best_match, "source": "sweep"},
                )
                logger.info(
                    "tg_sweep.found",
                    lead_id=lead_id,
                    lead_name=lead_name,
                    username=result.best_match,
                )
            else:
                logger.info("tg_sweep.no_match", lead_id=lead_id, lead_name=lead_name)

            await session.commit()
            global _tg_last_processed_at
            _tg_last_processed_at = datetime.utcnow()


async def check_cookie_health(account_id: Optional[str] = None):
    """Startup/post-login observation through the exclusive, proxied browser.

    Never reactivates an account, and inconclusive/network failures never
    become authentication failures. Post-login callers target one account.
    """
    from sqlalchemy import select as sa_select
    from releasi.db.models import AccountStatus

    repo, session = await _get_repo()
    try:
        stmt = sa_select(Account).where(
                Account.status == AccountStatus.ACTIVE,
                Account.archived == False,  # noqa: E712
            )
        if account_id:
            stmt = stmt.where(Account.id == account_id)
        result = await session.execute(stmt)
        accounts = result.scalars().all()
        pool = get_browser_pool()

        for account in accounts:
            if not account.li_at_cookie:
                continue

            # Skip accounts that are temporarily paused (rate-limit, manual pause, etc.)
            # No point checking cookie health for accounts that can't dispatch anyway.
            if account.paused_until and account.paused_until > datetime.utcnow():
                logger.debug("cookie_health.skipped_paused", account=account.name)
                continue

            if not await _account_proxy_url(account) or pool.is_busy(account.id):
                logger.info("cookie_health.skipped_unavailable", account=account.name)
                continue
            acquired = False
            page = None
            try:
                from releasi.linkedin.navigator import LinkedInNavigator
                context = await asyncio.wait_for(pool.acquire(account), timeout=15)
                acquired = True
                page = await context.new_page()
                nav = await LinkedInNavigator(page).go_to_feed()
                await _record_session_touch(
                    context, account, repo, job="cookie_health",
                    result="ok" if nav.success and nav.session_valid else
                    "redirect_login" if nav.success else "network_error",
                    detail={"phase": "browser_validation", "error": nav.error}, fetch_egress=False,
                )
            except Exception as e:
                logger.warning("cookie_health.check_error", account=account.name, error_type=type(e).__name__)
                continue
            finally:
                try:
                    if page:
                        await page.close()
                finally:
                    if acquired:
                        await pool.release_idle(account.id)

            if not nav.success:
                logger.warning("cookie_health.inconclusive", account=account.name)
            elif nav.session_valid:
                pool.confirm_session(account.id)
                logger.info("cookie_health.valid", account=account.name, status=account.status)
            else:
                if account.status == AccountStatus.ACTIVE:
                    logger.warning("cookie_health.expired", account=account.name)
                    await repo.update_account(account, status="cookie_expired")
                    await repo.log_action(
                        account_id=account.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": "cookie_expired_detected_by_health_check"},
                    )
                else:
                    logger.debug("cookie_health.still_expired", account=account.name)

    except Exception as e:
        logger.error("cookie_health.sweep_failed", error=str(e))
    finally:
        await session.close()


async def keep_alive():
    """
    Daily organic morning session — simulates a user opening LinkedIn in the morning.

    Runs hourly (IntervalTrigger). Per-account logic: only fires once per local day
    during the account's 7–10 AM window. The exact trigger time varies by ≤1 hour
    depending on when the hourly job happens to land in that window.

    Session expiry is detected reactively by the dispatcher (3 consecutive errors),
    not by this job. This job's purpose is organic-looking activity, not health checking.
    Skips accounts whose browser is currently in use by the dispatcher.
    """
    repo, session = await _get_repo()
    try:
        pool = get_browser_pool()
        accounts = await repo.list_active_accounts()

        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            if not account.li_at_cookie:
                continue

            # Only run during the account's 7–10 AM morning window.
            acct_now = _acct_local_now(account)
            if not (7 <= acct_now.hour < 10):
                continue

            # Only once per local day — prevents running again if the job fires
            # multiple times within the same window (e.g. near the boundary).
            acct_today = acct_now.date()
            if _keepalive_ran.get(account.id) == acct_today:
                continue

            # Skip if browser is busy with another job — try next cycle
            if pool.is_busy(account.id):
                logger.debug("keepalive.skipped_busy", account=account.name)
                continue

            try:
                context = await pool.acquire(account)
            except Exception as e:
                logger.warning("keepalive.acquire_failed", account=account.name, error=str(e))
                continue

            try:
                page = await context.new_page()
                try:
                    from releasi.linkedin.noise import BrowsingNoise
                    noise = BrowsingNoise(page)

                    # Step 1: Always start with the feed (most natural morning action)
                    await noise.scroll_feed(duration_seconds=random.uniform(15, 35))

                    # Step 2: Visit one more page — notifications, network, messaging, jobs, or learning
                    second_name, second_url = random.choices(
                        _KEEPALIVE_ACTIVITIES[1:], weights=_KEEPALIVE_STEP2_WEIGHTS, k=1
                    )[0]
                    await page.goto(second_url, wait_until="domcontentloaded", timeout=20000)

                    # Check for login redirect (session expired)
                    if any(p in page.url for p in ["/login", "/uas/login", "/signup", "/checkpoint/"]):
                        logger.warning("keepalive.session_expired_detected", account=account.name)
                        await repo.update_account(account, status="cookie_expired")
                        await repo.log_action(
                            account_id=account.id,
                            action_type=ActionType.ERROR,
                            status=ActionLogStatus.FAILED,
                            details={"reason": "session_expired_detected_by_morning_warmup"},
                        )
                    else:
                        await asyncio.sleep(random.uniform(5, 15))
                        await page.mouse.wheel(0, random.randint(200, 500))
                        await asyncio.sleep(random.uniform(2, 5))
                        await repo.add_proxy_mb(account.id, 3.0)  # feed scroll + second page
                        _keepalive_ran[account.id] = acct_today
                        logger.info("keepalive.morning_done", account=account.name, second_page=second_name)

                except Exception as e:
                    logger.warning("keepalive.scroll_failed", account=account.name, error=str(e))
                finally:
                    await page.close()
            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("keepalive.failed", error=str(e))
    finally:
        await session.close()


async def _start_api_server():
    """Start embedded FastAPI server if API is enabled."""
    settings = get_settings()
    if not settings.api_enabled:
        return

    try:
        import uvicorn
        from releasi.api.app import create_app
    except ImportError:
        logger.warning("api.missing_deps", msg="Install with pip install '.[api]' to enable the API")
        return

    app = create_app()
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.api_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    logger.info("api.starting", port=settings.api_port)
    asyncio.create_task(server.serve())


async def check_no_activity_alert():
    """Alert via Slack if zero connection requests were sent in the last 24 hours."""
    from sqlalchemy import select, func
    from releasi.db.models import ActionLog, ActionLogStatus
    repo, session = await _get_repo()
    try:
        cutoff = datetime.now(_dt_tz.utc) - timedelta(hours=24)
        result = await session.execute(
            select(func.count()).select_from(ActionLog).where(
                ActionLog.action_type == ActionType.CONNECTION_REQUEST,
                ActionLog.status == ActionLogStatus.SUCCESS,
                ActionLog.created_at >= cutoff,
            )
        )
        count = result.scalar() or 0
        if count == 0:
            await slack_notify(
                ":warning: *No connection requests sent in the last 24 hours.* "
                "Check if the dispatcher is running and accounts are active."
            )
            logger.warning("alert.no_connections_24h")
        else:
            logger.info("alert.connections_24h_ok", count=count)
    finally:
        await session.close()


async def daily_summary():
    """Send end-of-day Slack summary of activity across all active accounts."""
    from datetime import date as date_type
    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        lines = [f"*Daily Summary — {date_type.today().strftime('%b %d')}*"]
        for account in accounts:
            stat = await repo.get_or_create_daily_stat(account.id)
            campaigns = await repo.list_campaigns(account_id=account.id)
            remaining = 0
            skipped_today = 0
            for campaign in campaigns:
                remaining += await repo.count_leads_by_status(
                    campaign.id, [LeadStatus.PENDING, LeadStatus.SCHEDULED]
                )
                skipped_today += await repo.count_leads_updated_today_with_status(
                    campaign.id, LeadStatus.SKIPPED
                )
            lines.append(
                f"\n*{account.name}*\n"
                f"• Connections sent: {stat.connection_requests_sent}\n"
                f"• Skipped: {skipped_today}\n"
                f"• Errors: {stat.errors}\n"
                f"• Remaining: {remaining}"
            )
        await slack_notify("\n".join(lines))
    finally:
        await session.close()


async def resume_timed_pauses():
    """Auto-resume campaigns whose timed pause has expired."""
    from releasi.db.models import CampaignStatus
    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = Repository(session)
        now = datetime.utcnow()
        campaigns = await repo.get_campaigns_due_for_resume(now)
        for campaign in campaigns:
            await repo.update_campaign(
                campaign, status=CampaignStatus.ACTIVE, paused_at=None, resume_at=None,
            )
            logger.info(
                "scheduler.timed_pause_resumed",
                campaign_id=campaign.id,
                campaign_name=campaign.name,
            )


async def start_scheduler():
    """Start the APScheduler daemon. Blocks until interrupted."""
    await init_db()

    # Pre-warm browser pool for active accounts
    repo, session = await _get_repo()
    try:
        active_accounts = await repo.list_active_accounts()
    finally:
        await session.close()
    await init_pool(active_accounts)

    # Start API server if enabled
    await _start_api_server()

    settings = get_settings()
    # Scheduler runs in UTC. Jobs that need per-account timezone awareness (planner,
    # keepalive, acceptance checker) fire hourly and decide internally whether to act
    # for each account based on that account's local time. This scales to any timezone
    # without hardcoded offsets — a Tokyo account and a Montreal account are both
    # handled correctly by the same job.
    scheduler = AsyncIOScheduler(timezone='UTC')

    # Daily planning sweep — fires hourly; per-account logic handles local date/time.
    scheduler.add_job(
        daily_planning_sweep,
        IntervalTrigger(hours=1),
        id="daily_planner",
        name="Daily Planning Sweep",
        replace_existing=True,
    )

    # Dispatcher every 5 minutes with ±75s jitter to avoid predictable cadence.
    # misfire_grace_time=600: if the previous run was still going (processing leads),
    # APScheduler waits and runs when free without logging a spurious "missed" warning.
    scheduler.add_job(
        dispatch,
        IntervalTrigger(minutes=5, jitter=75),
        id="dispatcher",
        name="Action Dispatcher",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Pacing watchdog — flags behind-pace days, empty queues and paused accounts, and
    # posts a daily recap. Alerts are de-duplicated per account per local day.
    scheduler.add_job(
        pacing_watchdog,
        IntervalTrigger(minutes=30),
        id="pacing_watchdog",
        name="Pacing Watchdog",
        replace_existing=True,
    )

    # Acceptance checker — fires hourly; per-account logic checks if it's
    # acceptance_check_hour in the account's local timezone (once per local day).
    scheduler.add_job(
        check_acceptances,
        IntervalTrigger(hours=1),
        id="acceptance_checker",
        name="Daily Acceptance Checker",
        replace_existing=True,
    )

    # Debug-screenshot pruner — keep the last 7 days only. Fires nightly at
    # 03:30 UTC, well outside the dispatch window.
    scheduler.add_job(
        prune_debug_screenshots,
        CronTrigger(hour=3, minute=30),
        id="prune_debug_screenshots",
        name="Prune Debug Screenshots",
        replace_existing=True,
    )

    # Cooldown checker daily at midnight EDT (04:00 UTC)
    scheduler.add_job(
        check_cooldowns,
        CronTrigger(hour=4, minute=0),
        id="cooldown_checker",
        name="Cooldown Checker",
        replace_existing=True,
    )

    # Follow-up dispatcher every 30 minutes
    scheduler.add_job(
        dispatch_followups,
        IntervalTrigger(minutes=30),
        id="followup_dispatcher",
        name="Follow-up Dispatcher",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Broadcast dispatcher every 30 minutes — offset by 15 minutes from
    # the follow-up dispatcher so they don't compete for the pool at the
    # same instant. Both check account.daily_message_limit (unified cap).
    scheduler.add_job(
        dispatch_broadcasts,
        IntervalTrigger(minutes=30),
        id="broadcast_dispatcher",
        name="Broadcast Dispatcher",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Session keep-alive — fires hourly; per-account logic checks if it's within
    # the 7–10 AM window in the account's local timezone (once per local day).
    scheduler.add_job(
        keep_alive,
        IntervalTrigger(hours=1),
        id="keepalive",
        name="Morning Session Warm-Up",
        replace_existing=True,
    )

    # Auto-withdraw sweep — fires hourly; per-account logic checks interval_days elapsed.
    # Only runs for accounts with withdraw_threshold set.
    scheduler.add_job(
        withdraw_invitations_sweep,
        IntervalTrigger(hours=1),
        id="withdraw_sweep",
        name="Auto-Withdraw Sweep",
        replace_existing=True,
    )

    # Telegram enrichment sweep — one lead per tick, 20-minute interval.
    # A flood wait inside the job holds the asyncio.Lock for the wait duration;
    # subsequent ticks see the lock and exit immediately (no pile-up).
    # Requires RELEASI_TELEGRAM_* credentials to be configured.
    scheduler.add_job(
        telegram_enrichment_sweep,
        IntervalTrigger(minutes=20),
        id="tg_enrichment_sweep",
        name="Telegram Enrichment Sweep",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Daily summary Slack notification disabled
    # scheduler.add_job(
    #     daily_summary,
    #     CronTrigger(hour=1, minute=0, jitter=300),
    #     id="daily_summary",
    #     name="Daily Slack Summary",
    #     replace_existing=True,
    # )

    # No-activity alert: fires at 10:00 UTC daily (noon Berlin / 6am EDT).
    # Sends a Slack ping if zero successful connection requests were recorded
    # in the previous 24 hours across all accounts.
    scheduler.add_job(
        check_no_activity_alert,
        CronTrigger(hour=10, minute=0),
        id="no_activity_alert",
        name="No-Activity Alert",
        replace_existing=True,
    )

    scheduler.add_job(
        resume_timed_pauses,
        IntervalTrigger(minutes=1),
        id="resume_timed_pauses",
        name="Resume Timed Pauses",
        replace_existing=True,
    )

    # Alert on dispatcher hang: fire Slack after 3 consecutive max_instances skips
    # (~15 min stuck). Counter resets whenever the job actually runs (i.e., the lock
    # is released and APScheduler stops emitting MAX_INSTANCES events).
    _dispatcher_skip_count = 0
    _DISPATCHER_SKIP_ALERT_THRESHOLD = 3

    def _on_max_instances(event):
        nonlocal _dispatcher_skip_count
        if event.job_id != "dispatcher":
            return
        _dispatcher_skip_count += 1
        logger.warning(
            "dispatcher.hang_detected",
            consecutive_skips=_dispatcher_skip_count,
        )
        if _dispatcher_skip_count == _DISPATCHER_SKIP_ALERT_THRESHOLD:
            asyncio.get_event_loop().create_task(
                slack_notify(
                    f":rotating_light: *Dispatcher hang detected* — skipped "
                    f"{_DISPATCHER_SKIP_ALERT_THRESHOLD} consecutive ticks "
                    f"(~{_DISPATCHER_SKIP_ALERT_THRESHOLD * 5} min). "
                    f"A coroutine is holding the lock. Restart the container to recover: "
                    f"`ssh <server> 'cd /root/linauto && bash scripts/deploy.sh'`"
                )
            )

    def _on_dispatcher_executed(event):
        nonlocal _dispatcher_skip_count
        if event.job_id == "dispatcher":
            _dispatcher_skip_count = 0

    scheduler.add_listener(_on_max_instances, EVENT_JOB_MAX_INSTANCES)
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    scheduler.add_listener(_on_dispatcher_executed, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    scheduler.start()
    logger.info("scheduler.started", jobs=len(scheduler.get_jobs()))

    # On startup: validate all account sessions once (catches expired cookies
    # from before this deploy without waiting for the morning warm-up).
    await check_cookie_health()

    # Also run planning sweep immediately on startup
    await daily_planning_sweep()

    # Keep running until interrupted
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("scheduler.stopping")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    scheduler.shutdown(wait=True)
    await shutdown_pool()
    await close_db()
    logger.info("scheduler.stopped")
