import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
import pytest

from releasi.campaign import withdrawal
from releasi.linkedin import pool as pool_module, withdrawal_jobs
from releasi.linkedin.browser import LinkedInBrowser
from releasi.linkedin.withdrawal_jobs import WithdrawalJobStore
from releasi.linkedin.withdrawal_safety import WithdrawalStopped
from releasi.scheduler import runner


@pytest.fixture
def setup(monkeypatch, tmp_path):
    account = SimpleNamespace(id=str(uuid4()), name="Fixture", status="active", archived=False, paused_until=None)
    store = WithdrawalJobStore(tmp_path)
    pool = SimpleNamespace(is_busy=lambda _: False, acquire=AsyncMock(), close_owned_page=AsyncMock())
    pool.acquire.return_value.new_page = AsyncMock(return_value=object())
    repo = SimpleNamespace(get_account=AsyncMock(return_value=account),
                           mark_lead_withdrawn_by_slug=AsyncMock(return_value=True), log_action=AsyncMock())
    monkeypatch.setattr(runner, "_account_proxy_url", AsyncMock(return_value="fixture-proxy"))
    monkeypatch.setattr(withdrawal, "notify_withdrawal_issue", AsyncMock(return_value=True))
    monkeypatch.setattr(withdrawal_jobs, "get_withdrawal_job_store", lambda: store)
    monkeypatch.setattr(pool_module, "get_browser_pool", lambda: pool)
    return account, store, pool, repo


def fake_actions(monkeypatch, callback):
    class Actions:
        def __init__(self, page):
            pass

        async def withdraw_invitations(self, count, **kwargs):
            return await callback(count, **kwargs)

    monkeypatch.setattr(withdrawal, "LinkedInActions", Actions)


@pytest.mark.parametrize("failure", ["timeout", "cancel", "database", "cleanup"])
async def test_failure_preserves_progress_and_requires_review(setup, monkeypatch, failure):
    account, store, pool, repo = setup
    job = store.claim(account.id, 100, "oldest")
    entered = asyncio.Event()

    async def work(count, **callbacks):
        await callbacks["on_attempt"]("1", "https://www.linkedin.com/in/one")
        await callbacks["on_withdrawn"]("https://www.linkedin.com/in/one")
        if failure == "cleanup":
            return []
        await callbacks["on_attempt"]("2", "https://www.linkedin.com/in/two")
        entered.set()
        await asyncio.Event().wait()

    if failure == "database":
        repo.mark_lead_withdrawn_by_slug.side_effect = RuntimeError("DB unavailable")
    if failure == "cleanup":
        pool.close_owned_page.side_effect = pool_module.BrowserQuarantinedError("blocked")
    if failure == "timeout":
        monkeypatch.setattr(withdrawal, "WITHDRAWAL_JOB_TIMEOUT", 0.02)
    fake_actions(monkeypatch, work)
    task = asyncio.create_task(withdrawal.execute_withdrawal(account, repo, job, 100, "oldest", store=store, pool=pool))
    if failure == "cancel":
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await asyncio.wait_for(task, timeout=1)
    state = store.read(account.id)
    assert state["status"] == "error" and state["needs_review"]
    assert state["withdrawn"] == ["https://www.linkedin.com/in/one"]
    assert state["db_updated"] == (0 if failure == "database" else 1)
    pool.close_owned_page.assert_awaited_once()
    assert not store.active


async def test_reservation_is_held_through_notification(setup, monkeypatch):
    account, store, pool, repo = setup
    job = store.claim(account.id, 100, "oldest")
    alerting, finish = asyncio.Event(), asyncio.Event()

    async def fail(count, **kwargs):
        raise WithdrawalStopped("pagination failed")

    async def notify(*args):
        alerting.set()
        await finish.wait()
        return True

    fake_actions(monkeypatch, fail)
    monkeypatch.setattr(withdrawal, "notify_withdrawal_issue", notify)
    task = asyncio.create_task(withdrawal.execute_withdrawal(account, repo, job, 100, "oldest", store=store, pool=pool))
    await alerting.wait()
    with pytest.raises(WithdrawalStopped, match="already queued"):
        store.claim(account.id, 100, "oldest")
    finish.set()
    await task
    newer = store.claim(account.id, 100, "oldest")
    store.release(account.id, job)
    assert store.active[account.id] == newer
    assert store.read_job(account.id, job)["notification_delivered"]
    assert store.read_job(str(uuid4()), job) == {}


async def test_manual_endpoint_keeps_100_and_does_not_run_browser(setup):
    from releasi.api.routes.accounts import start_withdrawal
    account, store, pool, repo = setup
    tasks = BackgroundTasks()
    result = await start_withdrawal(account.id, {"count": 100, "order": "oldest"}, tasks, repo)
    assert store.read(account.id)["count"] == 100
    assert len(tasks.tasks) == 1 and result["task_id"].startswith("wd-")
    pool.acquire.assert_not_awaited()


async def test_restriction_pauses_account_with_valid_enum_without_resetting_leads(setup, monkeypatch):
    from releasi.db.models import AccountStatus
    account, store, pool, repo = setup
    repo.update_account = AsyncMock()
    job = store.claim(account.id, 100, "oldest")

    async def fail(count, **kwargs):
        raise WithdrawalStopped("Restricted", account_action="paused")

    fake_actions(monkeypatch, fail)
    result = await withdrawal.execute_withdrawal(account, repo, job, 100, "oldest", store=store, pool=pool)
    repo.update_account.assert_awaited_once_with(account, status=AccountStatus.PAUSED)
    repo.mark_lead_withdrawn_by_slug.assert_not_awaited()
    assert result["status"] == "error" and not result["withdrawn"]


@pytest.mark.parametrize("count", [101, 0, True, 1.5, "invalid"])
async def test_bad_manual_count_cannot_queue_a_job(setup, count):
    from releasi.api.routes.accounts import start_withdrawal
    account, store, pool, repo = setup
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await start_withdrawal(account.id, {"count": count}, tasks, repo)
    assert exc.value.status_code == 422
    assert not tasks.tasks and not store.active
    pool.acquire.assert_not_awaited()


async def test_status_endpoint_is_account_scoped_and_keeps_completed_jobs(setup):
    from releasi.api.routes.accounts import get_withdrawal_status
    account, store, pool, repo = setup
    job = store.claim(account.id, 100, "oldest")
    store.update(account.id, status="done", withdrawn=["https://www.linkedin.com/in/one"])
    store.release(account.id, job)
    store.claim(account.id, 100, "oldest")
    assert (await get_withdrawal_status(account.id, job, repo))["status"] == "done"
    with pytest.raises(HTTPException) as exc:
        await get_withdrawal_status(str(uuid4()), job, repo)
    assert exc.value.status_code == 404


async def test_strict_browser_close_does_not_swallow_failure():
    browser = LinkedInBrowser.__new__(LinkedInBrowser)
    browser._context = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("hung")))
    browser._playwright = SimpleNamespace(stop=AsyncMock())
    with pytest.raises(RuntimeError, match="could not be verified"):
        await browser.force_close(strict=True)
    assert browser._context is not None
    assert browser._playwright is None


async def test_stall_alert_is_delayed_throttled_and_reset(setup, monkeypatch):
    account, store, pool, repo = setup
    clock = {"now": 1000.0}
    monkeypatch.setattr(withdrawal, "time", SimpleNamespace(monotonic=lambda: clock["now"], time=lambda: clock["now"]))
    monkeypatch.setattr(withdrawal, "get_withdrawal_job_store", lambda: store)
    withdrawal.clear_browser_wait(account.id)
    await withdrawal.report_browser_wait(account)
    withdrawal.notify_withdrawal_issue.assert_not_awaited()
    clock["now"] += 901
    await withdrawal.report_browser_wait(account)
    await withdrawal.report_browser_wait(account)
    withdrawal.notify_withdrawal_issue.assert_awaited_once()
    withdrawal.clear_browser_wait(account.id)
    await withdrawal.report_browser_wait(account)
    withdrawal.notify_withdrawal_issue.assert_awaited_once()


async def test_scheduled_withdrawal_uses_same_100_limit_and_holds_failed_jobs(setup, monkeypatch):
    account, store, pool, repo = setup
    account.withdraw_threshold = 1
    account.auto_withdraw_interval_days = 30
    account.auto_withdraw_last_run = None
    repo.list_active_accounts = AsyncMock(return_value=[account])
    repo.update_account = AsyncMock()
    session = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(runner, "_get_repo", AsyncMock(return_value=(repo, session)))
    monkeypatch.setattr(runner, "get_browser_pool", lambda: pool)
    monkeypatch.setattr(withdrawal, "count_invitations", AsyncMock(return_value=1500))

    async def run(account_id, job_id, count, order):
        assert count == 100 and order == "oldest"
        store.update(account_id, status="error", error="fixture pagination failure")
        store.release(account_id, job_id)
        return store.read(account_id)

    monkeypatch.setattr(withdrawal, "run_withdrawal_job", AsyncMock(side_effect=run))
    await runner.withdraw_invitations_sweep()
    await runner.withdraw_invitations_sweep()
    withdrawal.run_withdrawal_job.assert_awaited_once()
    withdrawal.count_invitations.assert_awaited_once()


async def test_database_updates_exact_profile_and_account_only():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from releasi.db.models import Account, Base, Campaign, CampaignLeadAssignment, Lead
    from releasi.db.repository import Repository
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            a, b = Account(name="A", li_at_cookie="fixture"), Account(name="B", li_at_cookie="fixture")
            session.add_all([a, b])
            await session.flush()
            ca, cb = Campaign(name="A", account_id=a.id), Campaign(name="B", account_id=b.id)
            exact = Lead(linkedin_url="https://www.linkedin.com/in/person/")
            prefix = Lead(linkedin_url="https://www.linkedin.com/in/person-other")
            session.add_all([ca, cb, exact, prefix])
            await session.flush()
            stamp = datetime(2026, 10, 24)
            own = CampaignLeadAssignment(campaign_id=ca.id, lead_id=exact.id, status="connection_requested", connection_requested_at=stamp)
            other = CampaignLeadAssignment(campaign_id=cb.id, lead_id=exact.id, status="connection_requested")
            similar = CampaignLeadAssignment(campaign_id=ca.id, lead_id=prefix.id, status="connection_requested")
            session.add_all([own, other, similar])
            await session.commit()
            repo = Repository(session)
            assert await repo.mark_lead_withdrawn_by_slug("person", account_id=a.id)
            for row in (own, other, similar):
                await session.refresh(row)
            assert own.status == "withdrawn" and own.connection_requested_at == stamp
            assert other.status == similar.status == "connection_requested"
    finally:
        await engine.dispose()
