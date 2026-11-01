"""Authentication timestamps and exclusive ownership of account browsers."""
import asyncio
import math
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from releasi.linkedin import pool as pool_module
from releasi.linkedin.pool import AccountSessionChangedError, BrowserPool, BrowserQuarantinedError


@pytest.fixture
def account():
    return SimpleNamespace(
        id="test-account", name="Test", li_at_cookie=None, user_agent=None,
        proxy_url=None, proxy_country="ca", timezone="America/Toronto",
    )


@pytest.fixture
def browsers(monkeypatch):
    created = []

    def factory(**kwargs):
        page = SimpleNamespace(close=AsyncMock(), goto=AsyncMock())
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), pages=[page])
        browser = SimpleNamespace(launch=AsyncMock(return_value=context), force_close=AsyncMock())
        created.append(browser)
        return browser

    monkeypatch.setattr(pool_module, "LinkedInBrowser", factory)
    return created


async def test_new_slot_is_not_authenticated(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    assert math.isinf(pool.time_since_validated(account.id))
    pool.release(account.id)
    await pool.shutdown()


async def test_withdrawal_cleanup_releases_only_after_closure(account, browsers):
    pool = BrowserPool()
    context = await pool.acquire(account)
    page = await context.new_page()
    closing = asyncio.Event()
    finish = asyncio.Event()

    async def close():
        closing.set()
        await finish.wait()

    page.close.side_effect = close
    task = asyncio.create_task(pool.close_owned_page(account.id, page))
    await closing.wait()
    assert pool.is_busy(account.id)
    waiter = asyncio.create_task(pool.acquire(account))
    await asyncio.sleep(0)
    assert not waiter.done()
    finish.set()
    await task
    await waiter
    pool.release(account.id)


async def test_failed_page_close_evicts_before_reuse(account, browsers):
    pool = BrowserPool()
    context = await pool.acquire(account)
    page = await context.new_page()
    page.close.side_effect = RuntimeError("page hung")
    await pool.close_owned_page(account.id, page)
    browsers[0].force_close.assert_awaited_once_with(strict=True)
    assert not pool.has_slot(account.id) and not pool.is_busy(account.id)


async def test_unverified_shutdown_quarantines_existing_waiter_and_external_browser(account, browsers):
    pool = BrowserPool()
    context = await pool.acquire(account)
    page = await context.new_page()
    page.close.side_effect = RuntimeError("page hung")
    browsers[0].force_close.side_effect = RuntimeError("browser still alive")
    waiter = asyncio.create_task(pool.acquire(account))
    await asyncio.sleep(0)
    with pytest.raises(BrowserQuarantinedError):
        await pool.close_owned_page(account.id, page)
    with pytest.raises(BrowserQuarantinedError):
        await waiter
    with pytest.raises(BrowserQuarantinedError):
        await pool.reserve_external(account.id)
    assert pool.is_busy(account.id)
    assert len(browsers) == 1


async def test_cleanup_deadlines_quarantine_instead_of_hanging(account, browsers, monkeypatch):
    monkeypatch.setattr(pool_module, "_PAGE_CLOSE_TIMEOUT", 0.01)
    monkeypatch.setattr(pool_module, "_BROWSER_CLOSE_TIMEOUT", 0.01)
    pool = BrowserPool()
    context = await pool.acquire(account)
    page = await context.new_page()

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    page.close.side_effect = hang
    browsers[0].force_close.side_effect = hang
    with pytest.raises(BrowserQuarantinedError):
        await asyncio.wait_for(pool.close_owned_page(account.id, page), timeout=1)
    assert pool.is_busy(account.id)
    assert not pool._account_lock(account.id).locked()


async def test_process_check_does_not_refresh_authentication(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    pool.confirm_session(account.id)
    slot = pool._slots[account.id]
    slot.last_validated -= 3600
    authenticated_at = slot.last_validated
    slot.last_process_checked = time.monotonic() - pool_module._PROCESS_CHECK_COOLDOWN - 1
    pool.release(account.id)

    await pool.acquire(account)
    browsers[0].launch.return_value.new_page.assert_awaited_once()
    assert slot.last_validated == authenticated_at
    assert pool.time_since_validated(account.id) >= 3600
    assert time.monotonic() - slot.last_process_checked < 1
    pool.release(account.id)
    await pool.shutdown()


async def test_recreated_process_requires_new_authentication(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    pool.confirm_session(account.id)
    pool._slots[account.id].last_process_checked = time.monotonic() - pool_module._PROCESS_CHECK_COOLDOWN - 1
    browsers[0].launch.return_value.new_page.side_effect = RuntimeError("Browser stopped")
    pool.release(account.id)

    await pool.acquire(account)
    assert len(browsers) == 2
    browsers[0].force_close.assert_awaited_once()
    assert math.isinf(pool.time_since_validated(account.id))
    assert pool.is_busy(account.id)
    pool.release(account.id)
    await pool.shutdown()


async def test_cancelled_process_check_releases_account_lock(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    pool._slots[account.id].last_process_checked = time.monotonic() - pool_module._PROCESS_CHECK_COOLDOWN - 1
    pool.release(account.id)
    checking = asyncio.Event()

    async def block(**kwargs):
        checking.set()
        await asyncio.Event().wait()

    context = browsers[0].launch.return_value
    context.new_page.side_effect = block
    task = asyncio.create_task(pool.acquire(account))
    await checking.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pool.is_busy(account.id)
    context.new_page.side_effect = None
    await asyncio.wait_for(pool.acquire(account), timeout=1)
    pool.release(account.id)
    await pool.shutdown()


async def test_cancelled_launch_closes_partial_browser_and_releases_lock(account, browsers):
    pool = BrowserPool()
    launch_started = asyncio.Event()
    factory = pool_module.LinkedInBrowser

    async def block(**kwargs):
        launch_started.set()
        await asyncio.Event().wait()

    def blocking_factory(**kwargs):
        browser = factory(**kwargs)
        browser.launch.side_effect = block
        return browser

    pool_module.LinkedInBrowser = blocking_factory
    try:
        task = asyncio.create_task(pool.acquire(account))
        await launch_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        browsers[0].force_close.assert_awaited_once()
        assert not pool.is_busy(account.id)
        assert not pool.has_slot(account.id)
    finally:
        pool_module.LinkedInBrowser = factory


async def test_hung_launch_cleanup_is_bounded_and_quarantines(account, browsers, monkeypatch):
    monkeypatch.setattr(pool_module, "_BROWSER_CLOSE_TIMEOUT", 0.01)
    pool = BrowserPool()
    factory = pool_module.LinkedInBrowser

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    def failing_factory(**kwargs):
        browser = factory(**kwargs)
        browser.launch.side_effect = RuntimeError("launch failed")
        browser.force_close.side_effect = hang
        return browser

    monkeypatch.setattr(pool_module, "LinkedInBrowser", failing_factory)
    with pytest.raises(RuntimeError, match="launch failed"):
        await asyncio.wait_for(pool.acquire(account), timeout=1)
    assert pool.is_busy(account.id)
    assert not pool._global_lock.locked() and not pool._account_lock(account.id).locked()


async def test_hung_eviction_is_bounded_and_quarantines(account, browsers, monkeypatch):
    monkeypatch.setattr(pool_module, "_BROWSER_CLOSE_TIMEOUT", 0.01)
    pool = BrowserPool()
    await pool.acquire(account)
    pool.release(account.id)

    async def hang(**kwargs):
        await asyncio.Event().wait()

    browsers[0].force_close.side_effect = hang
    with pytest.raises(BrowserQuarantinedError):
        await asyncio.wait_for(pool.evict(account.id), timeout=1)
    assert pool.is_busy(account.id) and pool.has_slot(account.id)


async def test_external_lease_waits_for_work_and_blocks_new_acquisitions(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    reserving = asyncio.create_task(pool.reserve_external(account.id))
    await asyncio.sleep(0)
    assert not reserving.done()
    browsers[0].force_close.assert_not_awaited()
    pool.release(account.id)

    lease = await asyncio.wait_for(reserving, timeout=1)
    browsers[0].force_close.assert_awaited_once()
    assert pool.is_busy(account.id)
    assert not pool.has_slot(account.id)
    waiting = asyncio.create_task(pool.acquire(account))
    await asyncio.sleep(0)
    assert not waiting.done()
    assert len(browsers) == 1
    # An unrelated pooled release must never release an external lease.
    pool.release(account.id)
    assert pool.is_busy(account.id)
    await lease.evict()
    lease.release()
    lease.release()
    with pytest.raises(AccountSessionChangedError):
        await asyncio.wait_for(waiting, timeout=1)
    # A new acquisition uses a freshly loaded account, not the queued object.
    assert len(browsers) == 1
    await pool.acquire(account)
    assert len(browsers) == 2
    pool.release(account.id)
    await pool.shutdown()


async def test_cancelled_external_wait_does_not_release_current_holder(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    task = asyncio.create_task(pool.reserve_external(account.id))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.is_busy(account.id)
    pool.release(account.id)
    assert not pool.is_busy(account.id)
    await pool.shutdown()


async def test_cancelled_eviction_finishes_close_before_releasing_lock(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    pool.release(account.id)
    closing = asyncio.Event()
    closed = asyncio.Event()

    async def block(**kwargs):
        closing.set()
        await closed.wait()

    browsers[0].force_close.side_effect = block
    reserving = asyncio.create_task(pool.reserve_external(account.id))
    await closing.wait()
    reserving.cancel()
    await asyncio.sleep(0)
    assert pool.is_busy(account.id)
    closed.set()
    with pytest.raises(asyncio.CancelledError):
        await reserving
    assert not pool.is_busy(account.id)
    assert not pool.has_slot(account.id)
    await asyncio.wait_for(pool.acquire(account), timeout=1)
    pool.release(account.id)
    await pool.shutdown()


async def test_eviction_waits_for_active_work(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    eviction = asyncio.create_task(pool.evict(account.id))
    await asyncio.sleep(0)
    browsers[0].force_close.assert_not_awaited()
    pool.release(account.id)
    await asyncio.wait_for(eviction, timeout=1)
    assert not pool.has_slot(account.id)
    await asyncio.wait_for(pool.acquire(account), timeout=1)
    pool.release(account.id)
    await pool.shutdown()


async def test_cancelled_idle_navigation_still_releases_lock(account, browsers):
    pool = BrowserPool()
    context = await pool.acquire(account)
    navigating = asyncio.Event()

    async def block(*args, **kwargs):
        navigating.set()
        await asyncio.Event().wait()

    context.pages[0].goto.side_effect = block
    releasing = asyncio.create_task(pool.release_idle(account.id))
    await navigating.wait()
    releasing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await releasing
    assert not pool.is_busy(account.id)
    await pool.shutdown()


async def test_external_context_manager_releases_on_cancellation(account, browsers):
    pool = BrowserPool()
    lease = await pool.reserve_external(account.id)
    entered = asyncio.Event()

    async def work():
        async with lease:
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pool.is_busy(account.id)


async def test_waiter_queued_before_external_session_rejects_stale_credentials(account, browsers):
    pool = BrowserPool()
    await pool.acquire(account)
    reserving = asyncio.create_task(pool.reserve_external(account.id))
    await asyncio.sleep(0)
    waiting = asyncio.create_task(pool.acquire(account))
    await asyncio.sleep(0)
    pool.release(account.id)
    lease = await asyncio.wait_for(reserving, timeout=1)
    lease.release()
    with pytest.raises(AccountSessionChangedError):
        await asyncio.wait_for(waiting, timeout=1)
    assert not pool.is_busy(account.id)
    assert len(browsers) == 1
    await pool.acquire(account)
    pool.release(account.id)
    await pool.shutdown()
