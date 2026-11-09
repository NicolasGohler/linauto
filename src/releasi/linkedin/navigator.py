"""Page navigation helpers with safety checks."""
from __future__ import annotations

import random
import asyncio
from dataclasses import dataclass
from typing import Optional

import structlog
from playwright.async_api import Page

from releasi.config import get_settings
from releasi.linkedin.selectors import (
    FEED_URL, LOGIN_URL_PATTERNS, INVITATION_MANAGER_URL,
    CONNECTIONS_URL, PROFILE_ACTION_BUTTONS,
    PROFILE_OWNER_HEADINGS,
)

logger = structlog.get_logger()


@dataclass
class NavigationResult:
    success: bool
    url: str
    session_valid: bool = True
    error: Optional[str] = None


class LinkedInNavigator:
    """Handles all page navigation with human-like delays and safety checks."""

    def __init__(self, page: Page):
        self.page = page
        self._settings = get_settings()

    async def _random_delay(self, min_s: Optional[float] = None, max_s: Optional[float] = None):
        """Wait a random amount of time to simulate human behavior."""
        lo = min_s or self._settings.page_load_delay_min
        hi = max_s or self._settings.page_load_delay_max
        delay = random.uniform(lo, hi)
        await asyncio.sleep(delay)

    def _check_session(self, url: str) -> bool:
        """Check if the current URL indicates we're still logged in."""
        for pattern in LOGIN_URL_PATTERNS:
            if pattern in url:
                return False
        return True

    async def _is_authwall_showing(self) -> bool:
        """
        Detect LinkedIn's authwall overlay (session expired without URL redirect).

        LinkedIn sometimes renders the authwall on top of a profile URL instead of
        redirecting to /login. The URL stays at linkedin.com/in/... so _check_session
        passes, but the page body is actually the login/join wall.

        Checks for distinctive authwall elements that are never present on a real
        logged-in profile page.
        """
        try:
            count = await self.page.locator(
                'button.join-form__form-body-submit-button, '
                '.authwall-join-form__form-toggle--bottom, '
                'button.authwall-sign-in-form__form-toggle--bottom'
            ).count()
            return count > 0
        except Exception:
            return False

    async def _wait_for_profile_rendered(self, timeout_ms: int = 10000):
        """Wait until LinkedIn profile action buttons are visible (page fully rendered)."""
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        try:
            await self.page.locator(PROFILE_OWNER_HEADINGS).first.wait_for(
                state="visible", timeout=timeout_ms)
        except Exception:
            return False
        per_sel_timeout = max(timeout_ms // len(PROFILE_ACTION_BUTTONS), 2000)
        for sel in PROFILE_ACTION_BUTTONS:
            try:
                await self.page.locator(sel).first.wait_for(
                    state="visible", timeout=per_sel_timeout
                )
                logger.debug("navigator.profile_rendered", indicator=sel)
                # Brief extra pause so secondary buttons (e.g. "More") finish
                # rendering on Follow-primary profiles where "Follow" appears
                # before "More actions" is fully in the DOM.
                await asyncio.sleep(0.5)
                # Do not stop loading as soon as a sidebar action appears.
                return True
            except (PlaywrightTimeout, Exception):
                continue
        logger.warning("navigator.profile_render_timeout")
        return False

    async def go_to_profile(self, profile_url: str) -> NavigationResult:
        """Navigate to a LinkedIn profile page."""
        try:
            await self._random_delay(0.5, 2.0)
            # Use domcontentloaded instead of load: fires as soon as the DOM is
            # ready, before all resources finish loading.  This means an expired
            # session (which triggers a /login redirect) is detected in <2s
            # rather than causing a 30s timeout waiting for the login page to
            # fully load.  Profile content rendering is handled separately by
            # _wait_for_profile_rendered below.
            await self.page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
            await self._random_delay()

            session_valid = self._check_session(self.page.url)
            if not session_valid:
                logger.warning("navigator.session_expired", target=profile_url)
                return NavigationResult(
                    success=True, url=self.page.url, session_valid=False
                )
            if "/404" in self.page.url or "page not found" in (await self.page.title()).lower():
                return NavigationResult(success=True, url=self.page.url)

            # Wait for profile to be fully rendered by JS
            rendered = await self._wait_for_profile_rendered()

            # Secondary session check: LinkedIn may overlay the authwall on the
            # profile URL without redirecting (soft session expiry). The URL check
            # above passes but the page body is a login wall.
            #
            # Distinguish two authwall scenarios:
            # 1. Session-expired authwall: cookie is invalid — ALL navigations fail.
            # 2. Per-profile restriction: new/low-connection accounts are blocked
            #    from viewing certain profiles even with a valid session. The same
            #    authwall CSS selectors fire for both cases.
            #
            # To tell them apart, do a quick feed check. If the feed loads fine the
            # session is alive and this is a profile-level restriction (return as a
            # navigation failure so the executor marks the lead SKIPPED). If the
            # feed also fails the session is truly dead (return session_valid=False).
            if await self._is_authwall_showing():
                try:
                    feed_result = await self.go_to_feed()
                    if feed_result.success and feed_result.session_valid:
                        logger.warning(
                            "navigator.authwall_per_profile",
                            target=profile_url,
                        )
                        return NavigationResult(
                            success=False,
                            url=profile_url,
                            session_valid=True,
                            error="authwall_per_profile",
                        )
                    if not feed_result.success:
                        return NavigationResult(success=False, url=profile_url,
                                                error=feed_result.error or "feed_check_inconclusive")
                except Exception:
                    pass  # Feed check failed — fall through to session_valid=False
                logger.warning("navigator.authwall_overlay_detected", target=profile_url)
                return NavigationResult(
                    success=True, url=self.page.url, session_valid=False
                )

            if not rendered:
                return NavigationResult(success=False, url=self.page.url,
                                        error="profile_render_incomplete")
            return NavigationResult(
                success=True, url=self.page.url, session_valid=session_valid
            )
        except Exception as e:
            logger.error("navigator.profile_failed", url=profile_url, error=str(e))
            return NavigationResult(
                success=False, url=profile_url, error=str(e)
            )

    async def go_to_feed(self) -> NavigationResult:
        """Navigate to LinkedIn feed (useful for session validation)."""
        try:
            await self.page.goto(FEED_URL, wait_until="domcontentloaded", timeout=15000)
            await self._random_delay()
            if not self._check_session(self.page.url) or await self._is_authwall_showing():
                return NavigationResult(success=True, url=self.page.url, session_valid=False)
            # A feed URL alone is not proof of authentication: wait for member navigation.
            await self.page.locator(
                'a[href*="/mynetwork/"], a[href*="/messaging/"], .global-nav__me'
            ).first.wait_for(state="visible", timeout=10000)
            return NavigationResult(
                success=True,
                url=self.page.url,
                session_valid=self._check_session(self.page.url),
            )
        except Exception as e:
            return NavigationResult(success=False, url=FEED_URL, error=str(e))

    async def go_to_connections(self) -> NavigationResult:
        """Navigate to connections list sorted by most recently added."""
        try:
            await self._random_delay(0.5, 2.0)
            await self.page.goto(
                CONNECTIONS_URL,
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await self._random_delay()
            return NavigationResult(
                success=True,
                url=self.page.url,
                session_valid=self._check_session(self.page.url),
            )
        except Exception as e:
            return NavigationResult(
                success=False, url=CONNECTIONS_URL, error=str(e)
            )

    async def go_to_invitation_manager(self) -> NavigationResult:
        """Navigate to sent invitations filtered to People (connection requests).

        LinkedIn's invitation manager is a SPA.  Navigating directly to
        /sent/CONNECTION/ gets redirected back to /sent/ with no cards.
        The correct flow is: load /sent/, then click the "People (N)" filter
        pill — that triggers the SPA to load connection-request cards.
        """
        try:
            await self._random_delay(0.5, 2.0)
            await self.page.goto(
                INVITATION_MANAGER_URL,
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(2)

            if not self._check_session(self.page.url):
                return NavigationResult(
                    success=True, url=self.page.url, session_valid=False
                )

            # Click the "People (N)" filter pill to show connection invites only.
            people_el = await self.page.evaluate_handle("""
            () => {
                const all = document.querySelectorAll('a, li, span, button, [role="tab"]');
                for (const el of all) {
                    if (/^People\\s*\\(?\\s*\\d/.test((el.textContent || '').trim())) return el;
                }
                return null;
            }
            """)
            el = people_el.as_element()
            if el:
                await el.click()
                await asyncio.sleep(3)

            return NavigationResult(
                success=True,
                url=self.page.url,
                session_valid=self._check_session(self.page.url),
            )
        except Exception as e:
            return NavigationResult(
                success=False, url=INVITATION_MANAGER_URL, error=str(e)
            )
