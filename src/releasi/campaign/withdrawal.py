"""Shared lifecycle for manual and scheduled invitation withdrawals."""
from __future__ import annotations

import asyncio
from datetime import datetime
import time

import structlog

from releasi.db.models import AccountStatus, ActionLogStatus, ActionType
from releasi.linkedin.actions import LinkedInActions
from releasi.linkedin.pool import get_browser_pool
from releasi.linkedin.withdrawal_jobs import get_withdrawal_job_store
from releasi.linkedin.withdrawal_safety import (
    WITHDRAWAL_JOB_TIMEOUT, WITHDRAWAL_STEP_TIMEOUT, WithdrawalStopped, profile_url,
)

logger = structlog.get_logger()


async def notify_withdrawal_issue(account_name, message):
    from releasi.config import get_settings
    from releasi.notifications.slack import notify
    settings = get_settings()
    if settings.slack_bot_token and settings.slack_user_id:
        return await notify(message)
    import yaml
    for path in ("/app/config/settings.yaml", "config/settings.yaml"):
        try:
            with open(path) as file:
                funding = (yaml.safe_load(file) or {}).get("fundraising") or {}
        except FileNotFoundError:
            continue
        return await notify(message, bot_token=funding.get("slack_bot_token", ""),
                            channel=funding.get("slack_channel") or settings.slack_user_id)
    return False


async def count_invitations(account, *, pool=None):
    """The count-only route must not become another unbounded browser holder."""
    pool = pool or get_browser_pool()
    if pool.is_busy(account.id):
        raise WithdrawalStopped("Account browser is busy.")
    from releasi.scheduler.runner import _account_proxy_url
    if not await _account_proxy_url(account):
        raise WithdrawalStopped("An account proxy is required for invitation counting.")
    context = await asyncio.wait_for(pool.acquire(account), timeout=WITHDRAWAL_STEP_TIMEOUT)
    page = None
    try:
        page = await asyncio.wait_for(context.new_page(), timeout=WITHDRAWAL_STEP_TIMEOUT)
        return await asyncio.wait_for(
            LinkedInActions(page).get_pending_invitation_count(),
            timeout=WITHDRAWAL_STEP_TIMEOUT,
        )
    finally:
        await pool.close_owned_page(account.id, page)


_browser_waits = {}


def clear_browser_wait(account_id):
    _browser_waits.pop(account_id, None)


async def report_browser_wait(account):
    """Alert on prolonged dispatcher blockage; never force-release ownership."""
    now = time.monotonic()
    state = _browser_waits.setdefault(account.id, {"since": now, "alerted": None})
    job = get_withdrawal_job_store().read(account.id)
    if job.get("status") == "running" and time.time() - job["started_at"] < WITHDRAWAL_JOB_TIMEOUT + 60:
        return
    if now - state["since"] < 15 * 60:
        return
    if state["alerted"] is not None and now - state["alerted"] < 60 * 60:
        return
    state["alerted"] = now
    try:
        await asyncio.wait_for(notify_withdrawal_issue(
            account.name,
            f"Campaign dispatch for {account.name} has been blocked waiting for its browser for at least 15 minutes. "
            "Operator attention is needed. No browser lock was forcibly released.",
        ), timeout=15)
    except Exception:
        logger.warning("dispatch.blocked_alert_failed", account_id=account.id)


async def execute_withdrawal(account, repo, job_id, count, order, *, store=None, pool=None):
    """Execute a previously claimed job, preserving every confirmed result."""
    store = store or get_withdrawal_job_store()
    pool = pool or get_browser_pool()
    state = store.read(account.id)
    if state.get("job_id") != job_id or store.active.get(account.id) != job_id:
        raise WithdrawalStopped("Withdrawal reservation is no longer valid.")
    owned, page, cancelled, protection_failed = False, None, False, False
    error = None

    async def attempted(invitation_id, url):
        store.update(account.id, in_flight={"invitation_id": invitation_id, "url": url})

    async def confirmed(url):
        canonical = profile_url(url)
        if not canonical:
            raise WithdrawalStopped("Confirmed profile could not be matched; operator review is required.")
        state = store.read(account.id)
        withdrawn = state["withdrawn"] + [canonical]
        # Record confirmation before DB synchronization, so a DB failure cannot
        # erase the remote result or cause an automatic retry.
        store.update(account.id, withdrawn=withdrawn)
        updated = await repo.mark_lead_withdrawn_by_slug(
            canonical.split("/in/", 1)[1], account_id=account.id,
        )
        await repo.log_action(
            account_id=account.id, action_type=ActionType.INVITATION_WITHDRAWN,
            status=ActionLogStatus.SUCCESS,
            details={"withdrawal_job": job_id, "profile_url": canonical},
        )
        store.update(account.id, db_updated=state["db_updated"] + int(bool(updated)), in_flight=None)

    async def work():
        nonlocal owned, page
        if account.status != "active" or account.archived:
            raise WithdrawalStopped("Account is not active; withdrawal was not started.")
        if account.paused_until and account.paused_until > datetime.utcnow():
            raise WithdrawalStopped("Account is in cooldown; withdrawal was not started.")
        from releasi.scheduler.runner import _account_proxy_url
        if not await _account_proxy_url(account):
            raise WithdrawalStopped("An account proxy is required for withdrawal.")
        if pool.is_busy(account.id):
            raise WithdrawalStopped("Account browser is busy; withdrawal was not started.")
        context = await asyncio.wait_for(pool.acquire(account), timeout=WITHDRAWAL_STEP_TIMEOUT)
        owned = True
        page = await asyncio.wait_for(context.new_page(), timeout=WITHDRAWAL_STEP_TIMEOUT)
        actions = LinkedInActions(page)
        await actions.withdraw_invitations(count, order=order, on_attempt=attempted, on_withdrawn=confirmed)

    try:
        await asyncio.wait_for(work(), timeout=WITHDRAWAL_JOB_TIMEOUT)
    except asyncio.CancelledError:
        cancelled = True
        error = "Withdrawal interrupted; saved progress was retained. No automatic retry will run."
    except asyncio.TimeoutError:
        error = "Withdrawal timed out; saved progress was retained. No automatic retry will run."
    except WithdrawalStopped as exc:
        error = str(exc)
        if exc.account_action:
            try:
                await repo.update_account(account, status=AccountStatus(exc.account_action))
            except Exception:
                # Do not continue if the protection cannot be persisted.
                protection_failed = True
                pool.quarantine(account.id)
                error = "Account protection could not be saved; browser access is blocked pending operator recovery."
    except Exception:
        error = "Withdrawal stopped after an unexpected error; review saved progress before retrying."
        logger.error("withdrawal.failed", account_id=account.id, job_id=job_id)
    finally:
        try:
            if owned:
                await pool.close_owned_page(account.id, page)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                cancelled = True
            error = "Browser cleanup could not be verified; the account is blocked pending operator recovery."
            store.update(account.id, needs_review=True)
        try:
            state = store.read(account.id)
            if protection_failed:
                pool.quarantine(account.id)
            store.update(account.id, status="error" if error else "done", error=error,
                         needs_review=bool(protection_failed or state.get("needs_review") or state.get("in_flight")))
        except BaseException:
            store.release(account.id, job_id)
            raise

    try:
        if error:
            state = store.read(account.id)
            try:
                delivered = await asyncio.wait_for(notify_withdrawal_issue(
                    account.name,
                    f"Withdrawal stopped for {account.name}: {error} "
                    f"Confirmed: {len(state['withdrawn'])}/{count}. Job: {job_id}. No automatic retry.",
                ), timeout=15)
            except Exception:
                delivered = False
            store.update(account.id, notification_delivered=bool(delivered))
        if cancelled:
            raise asyncio.CancelledError()
        return store.read(account.id)
    finally:
        store.release(account.id, job_id)


async def run_withdrawal_job(account_id, job_id, count, order):
    """Background work owns its DB session instead of retaining a request dependency."""
    from releasi.db.engine import get_session_factory
    from releasi.db.repository import Repository
    store = get_withdrawal_job_store()
    try:
        async with get_session_factory()() as session:
            repo = Repository(session)
            account = await repo.get_account(account_id)
            if not account:
                raise WithdrawalStopped("Account no longer exists.")
            return await execute_withdrawal(account, repo, job_id, count, order, store=store)
    except Exception:
        state = store.read(account_id)
        if state.get("job_id") == job_id and state.get("status") == "running":
            store.update(account_id, status="error", needs_review=bool(state.get("in_flight")),
                         error="Withdrawal could not complete; no automatic retry will run.")
        raise
    finally:
        store.release(account_id, job_id)
