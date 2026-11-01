"""Persistent browser pool for long-lived LinkedIn sessions.

Keeps one Chromium instance per account alive across scheduler jobs,
preserving cookies (bcookie, bscookie, JSESSIONID, li_rm, etc.) that
accumulate naturally during browsing. Authentication is confirmed by callers
after visiting an authenticated page, independently of browser process health.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import structlog
from playwright.async_api import BrowserContext

from releasi.config import get_settings
from releasi.db.models import Account
from releasi.linkedin.browser import LinkedInBrowser

logger = structlog.get_logger()

# A process check never establishes that LinkedIn accepted the session.
_PROCESS_CHECK_COOLDOWN = 30 * 60  # 30 minutes
# Publicly imported by dispatchers to decide when to validate authentication.
_VALIDATION_COOLDOWN = 30 * 60
_PAGE_CLOSE_TIMEOUT = 10
_BROWSER_CLOSE_TIMEOUT = 15


class AccountSessionChangedError(RuntimeError):
    """An external session completed while waiting; reload the account first."""


class BrowserQuarantinedError(RuntimeError):
    """Closure could not be verified; do not reuse this account's profile."""


@dataclass
class PoolSlot:
    """One browser slot per LinkedIn account."""

    account_id: str
    browser: LinkedInBrowser
    context: BrowserContext
    in_use: bool = False
    last_activity: float = field(default_factory=time.monotonic)
    last_validated: Optional[float] = None
    last_process_checked: float = field(default_factory=time.monotonic)


class ExternalBrowserLease:
    """Exclusive account reservation for a browser outside the pool.

    Acquired with ``await pool.reserve_external(account_id)``. Keep the lease
    until the external browser is closed AND new credentials are persisted.
    Use ``release()`` in finally, or ``async with lease``. Do not call
    ``pool.acquire()`` / ``pool.evict()`` while holding this lease; they wait
    on its lock. ``await lease.evict()`` is safe without reacquiring it.
    """

    def __init__(self, pool: BrowserPool, account_id: str):
        self._pool = pool
        self.account_id = account_id
        self._released = False

    async def __aenter__(self) -> ExternalBrowserLease:
        if self._released:
            raise RuntimeError("External browser lease has already been released")
        return self

    async def __aexit__(self, *args) -> None:
        self.release()

    async def evict(self) -> None:
        if self._released:
            raise RuntimeError("External browser lease has already been released")
        await self._pool._evict_locked(self.account_id)

    def release(self) -> None:
        """Idempotently release ownership (can be called by a teardown task)."""
        if not self._released:
            self._released = True
            self._pool._external_leases.pop(self.account_id, None)
            self._pool._session_generations[self.account_id] = (
                self._pool._session_generations.get(self.account_id, 0) + 1
            )
            self._pool._account_lock(self.account_id).release()


class BrowserPool:
    """Manages persistent browser instances for active accounts.

    - One slot per account, up to ``pool_max_browsers``.
    - Stable per-account ``asyncio.Lock`` for concurrency (APScheduler jobs are async
      on the same event loop).
    - Global lock only for slot creation / deletion.
    """

    def __init__(self, max_browsers: int = 3):
        self._slots: Dict[str, PoolSlot] = {}
        self._max_browsers = max_browsers
        self._global_lock = asyncio.Lock()
        self._account_locks: Dict[str, asyncio.Lock] = {}
        self._pool_holders = set()
        self._external_leases: Dict[str, ExternalBrowserLease] = {}
        self._session_generations: Dict[str, int] = {}
        self._quarantined: set = set()

    def _assert_available(self, account_id: str) -> None:
        if account_id in self._quarantined:
            raise BrowserQuarantinedError("Account browser requires operator recovery before reuse")

    def quarantine(self, account_id: str) -> None:
        self._quarantined.add(account_id)

    def _account_lock(self, account_id: str) -> asyncio.Lock:
        # Locks outlive slots so eviction cannot strand waiters on an old lock.
        return self._account_locks.setdefault(account_id, asyncio.Lock())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, accounts: list) -> None:
        """Pre-warm browsers for the given active accounts."""
        to_warm = accounts[: self._max_browsers]
        for account in to_warm:
            try:
                async with self._account_lock(account.id):
                    async with self._global_lock:
                        if account.id not in self._slots:
                            await self._create_slot(account)
                logger.info("pool.prewarm_ok", account=account.name)
            except Exception as e:
                logger.error("pool.prewarm_failed", account=account.name, error=str(e))
        logger.info("pool.started", slots=len(self._slots), max=self._max_browsers)

    async def shutdown(self) -> None:
        """Close all browser slots."""
        for account_id in list(self._slots):
            await self.evict(account_id)
        logger.info("pool.shutdown")

    # ------------------------------------------------------------------
    # Acquire / Release
    # ------------------------------------------------------------------

    async def acquire(self, account: Account) -> BrowserContext:
        """Get (or create) a browser context for an account.

        Performs a process check periodically, independently of authentication.
        Blocks if the slot is already in use by another job.
        Raises AccountSessionChangedError if a completed external lease means
        the supplied account credentials may have become stale while waiting.
        """
        lock = self._account_lock(account.id)
        self._assert_available(account.id)
        generation = self._session_generations.get(account.id, 0)
        await lock.acquire()
        try:
            self._assert_available(account.id)
            if generation != self._session_generations.get(account.id, 0):
                raise AccountSessionChangedError("Account session changed while waiting; reload account before retrying")
            async with self._global_lock:
                slot = self._slots.get(account.id)
                if slot is None:
                    slot = await self._create_slot(account)

            if time.monotonic() - slot.last_process_checked > _PROCESS_CHECK_COOLDOWN:
                if not await self._health_check(slot):
                    logger.warning("pool.recreating_unhealthy_slot", account=account.name)
                    await self._evict_locked(account.id)
                    async with self._global_lock:
                        slot = await self._create_slot(account)
                slot.last_process_checked = time.monotonic()

            slot.in_use = True
            slot.last_activity = time.monotonic()
            self._pool_holders.add(account.id)
            logger.debug("pool.acquired", account=account.name)
            return slot.context
        except BaseException:
            lock.release()
            raise

    async def reserve_external(self, account_id: str) -> ExternalBrowserLease:
        """Wait for account work, evict its browser, and reserve exclusivity.

        Cancellation while waiting or evicting leaves no reservation behind.
        Callers must close their browser before releasing the returned lease.
        """
        lock = self._account_lock(account_id)
        self._assert_available(account_id)
        await lock.acquire()
        try:
            self._assert_available(account_id)
            await self._evict_locked(account_id)
            lease = ExternalBrowserLease(self, account_id)
            self._external_leases[account_id] = lease
            return lease
        except BaseException:
            lock.release()
            raise

    async def release_idle(self, account_id: str) -> None:
        """Navigate to about:blank to stop background JS, then release slot.

        LinkedIn's JavaScript makes continuous background requests (notification
        polling, WebSocket, feed updates) even when idle.  Navigating to
        about:blank before releasing stops all network activity and saves
        proxy bandwidth (~1-2 GB/day for a persistent context).
        """
        if account_id not in self._pool_holders:
            return
        try:
            slot = self._slots.get(account_id)
            if slot and slot.context:
                pages = slot.context.pages
                if pages:
                    await pages[0].goto("about:blank", timeout=5000)
        except Exception:
            pass  # Best-effort; don't block release
        finally:
            self.release(account_id)

    async def close_owned_page(self, account_id: str, page) -> None:
        """Bound withdrawal cleanup; never hand an uncertain browser to a waiter."""
        if account_id not in self._pool_holders:
            return
        slot = self._slots.get(account_id)
        try:
            if page is None:
                raise RuntimeError("Page creation did not complete")
            await asyncio.wait_for(page.close(), timeout=_PAGE_CLOSE_TIMEOUT)
            if slot and slot.context.pages:
                await asyncio.wait_for(
                    slot.context.pages[0].goto("about:blank", timeout=5000),
                    timeout=_PAGE_CLOSE_TIMEOUT,
                )
        except BaseException as original:
            # Block current waiters as well as new callers before yielding.
            self._quarantined.add(account_id)
            try:
                if slot is None:
                    raise RuntimeError("Missing owned browser slot")
                await asyncio.wait_for(slot.browser.force_close(strict=True), timeout=_BROWSER_CLOSE_TIMEOUT)
            except BaseException as exc:
                logger.error("pool.browser_quarantined", account_id=account_id)
                raise BrowserQuarantinedError(
                    "Browser closure could not be verified; operator recovery is required"
                ) from exc
            else:
                self._slots.pop(account_id, None)
                self._quarantined.discard(account_id)
                if isinstance(original, asyncio.CancelledError):
                    raise
        finally:
            self.release(account_id)

    def release(self, account_id: str) -> None:
        """Release pooled ownership without releasing an external reservation."""
        if account_id not in self._pool_holders:
            return
        self._pool_holders.remove(account_id)
        slot = self._slots.get(account_id)
        if slot:
            slot.in_use = False
            slot.last_activity = time.monotonic()
        self._account_lock(account_id).release()
        logger.debug("pool.released", account_id=account_id)

    def is_busy(self, account_id: str) -> bool:
        """Check if an account's browser is currently in use."""
        lock = self._account_locks.get(account_id)
        return account_id in self._quarantined or (lock.locked() if lock else False)

    def has_slot(self, account_id: str) -> bool:
        """Return True if a live browser slot already exists for the account.

        Used to decide whether a one-off session check can reuse the existing
        context (preferred) or must open an ephemeral one — never both at once,
        to avoid concurrent contexts for the same account.
        """
        return account_id in self._slots

    def time_since_validated(self, account_id: str) -> float:
        """Seconds since the slot's session was last confirmed healthy.

        Returns infinity if the slot doesn't exist (forces a check).
        """
        slot = self._slots.get(account_id)
        if not slot or slot.last_validated is None:
            return float("inf")
        return time.monotonic() - slot.last_validated

    def confirm_session(self, account_id: str) -> None:
        """Mark the session as confirmed healthy right now.

        Called after a successful dispatch so the next cycle doesn't
        re-navigate to the feed unnecessarily.
        """
        slot = self._slots.get(account_id)
        if slot:
            slot.last_validated = time.monotonic()

    async def evict(self, account_id: str) -> None:
        """Close and remove a slot (e.g. when an account is removed)."""
        async with self._account_lock(account_id):
            await self._evict_locked(account_id)

    async def _evict_locked(self, account_id: str) -> None:
        async with self._global_lock:
            slot = self._slots.get(account_id)
        if slot:
            # Finish closing even if the reserving task is cancelled, before
            # another task can acquire the same profile and open a browser.
            closing = asyncio.create_task(asyncio.wait_for(
                slot.browser.force_close(strict=True), timeout=_BROWSER_CLOSE_TIMEOUT,
            ))
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                try:
                    await closing
                except BaseException:
                    self.quarantine(account_id)
                raise
            except Exception as exc:
                self.quarantine(account_id)
                raise BrowserQuarantinedError("Browser eviction could not be verified; operator recovery is required") from exc
            finally:
                if closing.done() and not closing.cancelled() and closing.exception() is None:
                    self._slots.pop(account_id, None)
            logger.info("pool.evicted", account_id=account_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _create_slot(self, account: Account) -> PoolSlot:
        """Launch a new browser and register it as a pool slot.

        No HTTP pre-check — check_cookie_health() already validates at
        scheduler startup (routed through the account's proxy).  A bare
        httpx request from the server IP is a session-invalidation trigger
        and must never be sent.
        """
        browser = LinkedInBrowser(pool_managed=True)
        try:
            context = await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
                cookies_json=getattr(account, "cookies_json", None),
            )
        except BaseException:
            try:
                await asyncio.wait_for(browser.force_close(strict=True), timeout=_BROWSER_CLOSE_TIMEOUT)
            except BaseException:
                self.quarantine(account.id)
            raise
        # Launching a browser does not prove authentication. The caller must
        # validate an authenticated page before calling confirm_session().

        slot = PoolSlot(
            account_id=account.id,
            browser=browser,
            context=context,
        )
        self._slots[account.id] = slot
        logger.info("pool.slot_created", account=account.name)
        return slot

    async def _health_check(self, slot: PoolSlot) -> bool:
        """Quick health check: try opening and closing a page."""
        try:
            page = await slot.context.new_page()
            await page.close()
            return True
        except Exception as e:
            logger.warning("pool.health_check_failed", account=slot.account_id, error=str(e))
            return False


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_pool: Optional[BrowserPool] = None


def get_browser_pool() -> BrowserPool:
    """Return the global BrowserPool instance (must be initialized first)."""
    if _pool is None:
        raise RuntimeError("BrowserPool not initialized. Call init_pool() first.")
    return _pool


async def init_pool(accounts: list) -> BrowserPool:
    """Create and pre-warm the global BrowserPool."""
    global _pool
    settings = get_settings()
    _pool = BrowserPool(max_browsers=settings.pool_max_browsers)
    await _pool.start(accounts)
    return _pool


async def shutdown_pool() -> None:
    """Shut down the global BrowserPool if initialized."""
    global _pool
    if _pool:
        await _pool.shutdown()
        _pool = None
