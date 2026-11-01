"""Persisted retries and read-only DOM reproduction of the SDUI profile layout."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from releasi.db.models import Account, Base, Campaign, CampaignLeadAssignment, Lead, LeadList, LeadListMembership
from releasi.db.repository import Repository
from releasi.linkedin.actions import LinkedInActions
from releasi.safety.dispatch_decisions import classify_connection_result, AccountAction


@pytest_asyncio.fixture
async def retry_repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        account = Account(name="Retry test", li_at_cookie="test")
        session.add(account)
        await session.flush()
        campaigns = [Campaign(account_id=account.id, name=name) for name in ("A", "B")]
        leads = [Lead(linkedin_url=f"https://www.linkedin.com/in/{name}") for name in ("blocked", "next")]
        session.add_all(campaigns + leads)
        await session.flush()
        assignments = [CampaignLeadAssignment(campaign_id=campaigns[0].id, lead_id=lead.id) for lead in leads]
        assignments.append(CampaignLeadAssignment(campaign_id=campaigns[1].id, lead_id=leads[0].id))
        session.add_all(assignments)
        lead_list = LeadList(name="Retry test")
        session.add(lead_list)
        await session.flush()
        session.add_all([LeadListMembership(lead_id=lead.id, lead_list_id=lead_list.id) for lead in leads])
        await session.commit()
        yield Repository(session), factory, campaigns, leads, assignments
    await engine.dispose()


@pytest.mark.asyncio
async def test_backoff_survives_new_session_and_does_not_block_queue(retry_repo):
    repo, factory, campaigns, leads, assignments = retry_repo
    retry = await repo.defer_connection_attempt(campaigns[0].id, leads[0].id, "no_connect_button", 86400)
    assert retry["attempt"] == 1
    assert datetime.fromisoformat(retry["next_attempt_at"]) > datetime.utcnow() + timedelta(hours=23)
    async with factory() as fresh:
        eligible = await Repository(fresh).get_pending_leads_via_assignments(campaigns[0].id)
        assert [lead.id for lead in eligible] == [leads[1].id]
        other = (await fresh.execute(select(CampaignLeadAssignment).where(
            CampaignLeadAssignment.id == assignments[2].id))).scalar_one()
        assert other.retry_count == 0 and other.scheduled_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "scheduled"])
async def test_three_failures_require_manual_review(retry_repo, status):
    repo, _, campaigns, leads, assignments = retry_repo
    assignments[0].status = status
    await repo.session.commit()
    for attempt in range(1, 4):
        retry = await repo.defer_connection_attempt(campaigns[0].id, leads[0].id, "profile_dom", 86400)
        assert retry["attempt"] == attempt
        assert retry["exhausted"] == (attempt == 3)
    assert assignments[0].status == "error"
    assert assignments[0].scheduled_at is None
    assert leads[0].status.value == "pending"
    assert assignments[2].status == "pending"


@pytest.mark.asyncio
async def test_network_backoff_grows_and_terminal_state_is_untouched(retry_repo):
    repo, _, campaigns, leads, assignments = retry_repo
    first = await repo.defer_connection_attempt(campaigns[0].id, leads[0].id, "network", 900, max_attempts=6)
    second = await repo.defer_connection_attempt(campaigns[0].id, leads[0].id, "network", 900, max_attempts=6)
    assert datetime.fromisoformat(second["next_attempt_at"]) - datetime.fromisoformat(first["next_attempt_at"]) >= timedelta(minutes=15)
    assignments[0].status = "connection_requested"
    await repo.session.commit()
    await repo.defer_connection_attempt(campaigns[0].id, leads[0].id, "network", 900)
    assert assignments[0].status == "connection_requested"
    assert assignments[0].retry_count == 2


@pytest_asyncio.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


# Minimal structure from the observed live page; no cookie or personal data.
SDUI_PROFILE = '''<main>
  <aside><a href="/preload/custom-invite/?vanityName=sidebar-person"
    aria-label="Invite Roya Someone to connect">Connect</a></aside>
  <div><h2>Roya Gupta-Pourmand</h2>
    <a href="/preload/custom-invite/?vanityName=royapourmand"
       aria-label="Invite Roya Gupta-Pourmand to connect">Connect</a>
    <button>More</button></div>
  <div style="display:none"><a href="/preload/custom-invite/?vanityName=royapourmand"
       aria-label="Invite Roya Gupta-Pourmand to connect">Connect</a></div>
</main>'''


@pytest.mark.asyncio
async def test_h2_layout_uses_exact_invitation_target_not_sidebar(page):
    await page.set_content(SDUI_PROFILE)
    actions = LinkedInActions(page)
    assert await actions._get_profile_owner_name() == "Roya Gupta-Pourmand"
    candidate = await actions._find_primary_connect_button("https://www.linkedin.com/in/royapourmand/?isSelfProfile=false")
    assert await candidate.get_attribute("href") == "/preload/custom-invite/?vanityName=royapourmand"


@pytest.mark.asyncio
async def test_opaque_vanity_does_not_need_to_match_display_name(page):
    await page.set_content(SDUI_PROFILE.replace("royapourmand", "opaque-id-123"))
    candidate = await LinkedInActions(page)._find_primary_connect_button("https://www.linkedin.com/in/opaque-id-123")
    assert candidate is not None


@pytest.mark.asyncio
async def test_no_matching_target_fails_closed(page):
    await page.set_content(SDUI_PROFILE)
    assert await LinkedInActions(page)._find_primary_connect_button("https://www.linkedin.com/in/unknown") is None


@pytest.mark.asyncio
async def test_uninspectable_candidate_fails_closed(page):
    actions = LinkedInActions(page)
    candidate = SimpleNamespace(get_attribute=AsyncMock(side_effect=RuntimeError("stale")))
    assert not await actions._candidate_matches_owner(candidate, "Roya Gupta-Pourmand", "royapourmand")


@pytest.mark.asyncio
async def test_main_connect_finder_does_not_open_more_when_primary_is_verified(page):
    await page.set_content(SDUI_PROFILE)
    await page.locator("button").evaluate("e => e.onclick = () => { document.body.dataset.clicked = 'yes'; }")
    candidate = await LinkedInActions(page)._find_connect_button("https://www.linkedin.com/in/royapourmand")
    assert candidate is not None
    assert await page.locator("body").get_attribute("data-clicked") is None


@pytest.mark.asyncio
async def test_legacy_button_is_supported(page):
    await page.set_content('''<main><section class="pv-top-card"><h1>Roya Gupta-Pourmand</h1>
        <div class="pvs-profile-actions"><button aria-label="Invite Roya Gupta-Pourmand to connect">Connect</button></div>
        </section></main>''')
    candidate = await LinkedInActions(page)._find_primary_connect_button("https://www.linkedin.com/in/royapourmand")
    assert candidate is not None
    assert await candidate.inner_text() == "Connect"


def test_repeated_dom_errors_never_mark_cookie_expired():
    strikes = 0
    for _ in range(3):
        intent = classify_connection_result({"failure_kind": "profile_dom"}, strikes, 0, "test", "test")
        strikes = intent.consecutive_session
        assert intent.account_action == AccountAction.NONE
        assert intent.consecutive_network == 0
        assert intent.retry_delay_seconds == 86400
    assert intent.stop_account
    assert intent.account_backoff_seconds == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,details,kind", [
    ("no_connect_button", {}, "profile_dom"),
    ("profile_render_incomplete", {"pre_send": True}, "profile_dom"),
    ("Navigation failed: Timeout 15000ms exceeded", {"pre_send": True}, "network"),
    ("Navigation failed: net::ERR_TOO_MANY_REDIRECTS", {"pre_send": True}, "network"),
    ("Timeout 15000ms exceeded", {}, "terminal"),
    ("Some generic action failure", {}, "terminal"),
    ("Confirmed login redirect", {}, "auth"),
    ("CAPTCHA", {}, "captcha"),
])
async def test_executor_only_retries_known_pre_send_failures(monkeypatch, reason, details, kind):
    from releasi.campaign import executor as module
    from releasi.linkedin.actions import ActionResult, ActionStatus
    from releasi.db.models import LeadStatus
    monkeypatch.setattr(module.random, "random", lambda: 1)
    repo = SimpleNamespace(update_lead=AsyncMock(), increment_daily_stat=AsyncMock(), log_action=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=SimpleNamespace(goto=AsyncMock(), close=AsyncMock())))
    status = ActionStatus.SESSION_EXPIRED if kind == "auth" else ActionStatus.CAPTCHA if kind == "captcha" else ActionStatus.ERROR
    action = SimpleNamespace(send_connection_request=AsyncMock(return_value=ActionResult(status, reason, details)))
    monkeypatch.setattr(module, "LinkedInActions", lambda page: action)
    campaign = SimpleNamespace(id="campaign", connection_message_template=None, filter_no_photo=False,
                               filter_min_connections=0, filter_exclude_open_to_work=False)
    account = SimpleNamespace(id="account")
    lead = SimpleNamespace(id="lead", linkedin_url="https://www.linkedin.com/in/test", status=LeadStatus.PENDING, retry_count=0)
    if reason == "Some generic action failure":
        lead.status = LeadStatus.ERROR
    result = await module.CampaignExecutor(repo, context).execute_single_lead(account, campaign, lead)
    assert result["session_expired"] == (kind == "auth")
    if kind in ("auth", "captcha"):
        assert result["fatal"]
        assert result["captcha"] == (kind == "captcha")
        repo.update_lead.assert_not_awaited()
        return
    if kind == "profile_dom":
        assert result["failure_kind"] == kind
        assert not result.get("network_error")
        repo.update_lead.assert_not_awaited()
    elif kind == "network":
        assert result["network_error"]
        repo.update_lead.assert_not_awaited()
    else:
        assert not result.get("network_error")
        assert repo.update_lead.await_args.kwargs["status"] == LeadStatus.ERROR
        repo.increment_daily_stat.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_intent_logs_once_and_preserves_longer_account_backoff(monkeypatch):
    from releasi.scheduler import runner
    repo = SimpleNamespace(defer_connection_attempt=AsyncMock(return_value={"attempt": 1, "next_attempt_at": "later", "exhausted": False}),
                           increment_daily_stat=AsyncMock(), log_action=AsyncMock(), update_account=AsyncMock())
    account = SimpleNamespace(id="account", name="test", paused_until=datetime.utcnow() + timedelta(hours=2))
    campaign = SimpleNamespace(id="campaign", name="test")
    lead = SimpleNamespace(id="lead", linkedin_url="https://www.linkedin.com/in/test")
    intent = classify_connection_result({"failure_kind": "profile_dom"}, 2, 0, "test", "test")
    await runner._apply_connection_intent(intent, repo, account, campaign, lead, {"error": "no_connect_button"})
    repo.increment_daily_stat.assert_awaited_once_with("account", "errors")
    assert repo.log_action.await_args_list[0].kwargs["details"]["failure_kind"] == "profile_dom"
    assert repo.log_action.await_args_list[0].kwargs["lead_id"] == "lead"
    assert repo.update_account.await_args.kwargs["paused_until"] == account.paused_until


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_saved", [True, False])
async def test_login_save_holds_lease_until_database_save(monkeypatch, profile_saved):
    from fastapi import BackgroundTasks
    from releasi.api.routes import accounts
    from releasi.linkedin.login_session import LoginSessionManager
    order = []
    async def save(*args, **kwargs):
        order.append("save")
    manager = SimpleNamespace(is_active=True, _account_id="account",
        finish_session=AsyncMock(return_value={"li_at": "test", "profile_saved": profile_saved}),
        release_lease=Mock(side_effect=lambda: order.append("release")))
    monkeypatch.setattr(LoginSessionManager, "get_instance", lambda: manager)
    repo = SimpleNamespace(get_account=AsyncMock(return_value=SimpleNamespace(id="account")),
                           update_account=AsyncMock(side_effect=save), log_session_event=AsyncMock())
    result = await accounts.finish_login_session("account", BackgroundTasks(), repo)
    assert order == ["save", "release"]
    assert result["success"] == profile_saved
    assert result["cookies_saved"]
    manager.finish_session.assert_awaited_once_with(release_lease=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("success,valid,expected_update", [(False, True, False), (True, True, False), (True, False, True)])
async def test_health_check_needs_browser_auth_evidence(monkeypatch, success, valid, expected_update):
    from releasi.scheduler import runner
    from releasi.linkedin.navigator import LinkedInNavigator, NavigationResult
    from releasi.db.models import AccountStatus
    account = SimpleNamespace(id="account", name="test", li_at_cookie="test", paused_until=None, status=AccountStatus.ACTIVE)
    repo = SimpleNamespace(update_account=AsyncMock(), log_action=AsyncMock())
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [account]))), close=AsyncMock())
    page = SimpleNamespace(close=AsyncMock())
    pool = SimpleNamespace(is_busy=lambda _: False, acquire=AsyncMock(return_value=SimpleNamespace(new_page=AsyncMock(return_value=page))),
                           release_idle=AsyncMock(), confirm_session=Mock())
    monkeypatch.setattr(runner, "_get_repo", AsyncMock(return_value=(repo, session)))
    monkeypatch.setattr(runner, "get_browser_pool", lambda: pool)
    monkeypatch.setattr(runner, "_account_proxy_url", AsyncMock(return_value="http://test-proxy"))
    monkeypatch.setattr(runner, "_record_session_touch", AsyncMock())
    monkeypatch.setattr(LinkedInNavigator, "go_to_feed", AsyncMock(return_value=NavigationResult(success, "test", valid)))
    await runner.check_cookie_health("account")
    assert bool(repo.update_account.await_count) == expected_update
    if expected_update:
        repo.update_account.assert_awaited_once_with(account, status="cookie_expired")
    assert pool.confirm_session.call_count == int(success and valid)
    pool.release_idle.assert_awaited_once_with("account")
    page.close.assert_awaited_once()


def test_whole_action_timeout_needs_manual_reconciliation():
    intent = classify_connection_result({"error": "execute_lead_timeout"}, 0, 0, "test", "test")
    assert intent.failure_kind == "uncertain_outcome"
    assert intent.retry_delay_seconds is None
    assert intent.account_action == AccountAction.NONE
    assert intent.stop_account


def test_captcha_pauses_account_without_claiming_expired_cookie():
    intent = classify_connection_result({"fatal": True, "captcha": True}, 0, 0, "test", "test")
    assert intent.account_action == AccountAction.PAUSE
    assert intent.stop_account


# ---------------------------------------------------------------------------
# _attempt_li_rm_reauth — silent re-mint of li_at from the "remember me" token.
# This is the primary auto-recovery path: it fires *before* marking an account
# cookie_expired so a routine li_at expiry never requires manual re-login.
# ---------------------------------------------------------------------------

def _make_reauth_mocks(*, li_rm=None, feed_success=True, feed_valid=True,
                       account_status=None, li_rm_in_browser=False):
    """Build the minimal collaborator fakes needed by _attempt_li_rm_reauth."""
    from releasi.db.models import AccountStatus
    status = account_status if account_status is not None else AccountStatus.ACTIVE
    account = SimpleNamespace(
        id="acct-1",
        name="Test",
        li_rm_cookie=li_rm,
        status=status,
    )
    page = SimpleNamespace(close=AsyncMock())
    existing_cookies = [{"name": "li_rm", "value": li_rm}] if li_rm_in_browser and li_rm else []
    context = SimpleNamespace(
        cookies=AsyncMock(return_value=existing_cookies),
        add_cookies=AsyncMock(),
        new_page=AsyncMock(return_value=page),
    )
    repo = SimpleNamespace(
        update_account=AsyncMock(),
        log_session_event=AsyncMock(),
    )
    from releasi.linkedin.navigator import NavigationResult
    nav_result = NavigationResult(success=feed_success, url="https://www.linkedin.com/feed/", session_valid=feed_valid)
    return account, context, repo, page, nav_result


@pytest.mark.asyncio
async def test_reauth_skipped_when_no_li_rm(monkeypatch):
    """No li_rm cookie → return False immediately, no browser interaction."""
    from releasi.scheduler import runner
    account, context, repo, page, _ = _make_reauth_mocks(li_rm=None)
    result = await runner._attempt_li_rm_reauth(context, account, repo)
    assert result is False
    context.new_page.assert_not_called()
    repo.update_account.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_skipped_when_context_is_none(monkeypatch):
    """context=None → return False immediately, no DB writes."""
    from releasi.scheduler import runner
    account, _ctx, repo, _page, _ = _make_reauth_mocks(li_rm="some-token")
    result = await runner._attempt_li_rm_reauth(None, account, repo)
    assert result is False
    repo.update_account.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_injects_li_rm_when_missing_from_browser(monkeypatch):
    """li_rm not in browser cookies → add_cookies is called to inject it."""
    from releasi.scheduler import runner
    from releasi.linkedin.navigator import LinkedInNavigator
    account, context, repo, page, nav_result = _make_reauth_mocks(
        li_rm="my-li-rm", li_rm_in_browser=False,
    )
    monkeypatch.setattr(LinkedInNavigator, "go_to_feed", AsyncMock(return_value=nav_result))
    monkeypatch.setattr(runner, "_record_session_touch", AsyncMock())
    result = await runner._attempt_li_rm_reauth(context, account, repo)
    assert result is True
    context.add_cookies.assert_awaited_once()
    injected = context.add_cookies.call_args[0][0][0]
    assert injected["name"] == "li_rm"
    assert injected["value"] == "my-li-rm"


@pytest.mark.asyncio
async def test_reauth_skips_injection_when_li_rm_already_in_browser(monkeypatch):
    """li_rm already in the browser profile → add_cookies is NOT called."""
    from releasi.scheduler import runner
    from releasi.linkedin.navigator import LinkedInNavigator
    account, context, repo, page, nav_result = _make_reauth_mocks(
        li_rm="my-li-rm", li_rm_in_browser=True,
    )
    monkeypatch.setattr(LinkedInNavigator, "go_to_feed", AsyncMock(return_value=nav_result))
    monkeypatch.setattr(runner, "_record_session_touch", AsyncMock())
    result = await runner._attempt_li_rm_reauth(context, account, repo)
    assert result is True
    context.add_cookies.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_recovers_cookie_expired_account_to_active(monkeypatch):
    """When li_rm re-mint succeeds on a cookie_expired account, status is set to active."""
    from releasi.scheduler import runner
    from releasi.db.models import AccountStatus
    from releasi.linkedin.navigator import LinkedInNavigator
    account, context, repo, page, nav_result = _make_reauth_mocks(
        li_rm="my-li-rm", account_status=AccountStatus.COOKIE_EXPIRED,
    )
    monkeypatch.setattr(LinkedInNavigator, "go_to_feed", AsyncMock(return_value=nav_result))
    monkeypatch.setattr(runner, "_record_session_touch", AsyncMock())
    result = await runner._attempt_li_rm_reauth(context, account, repo)
    assert result is True
    repo.update_account.assert_awaited_once_with(account, status="active")


@pytest.mark.asyncio
async def test_reauth_does_not_write_status_when_already_active(monkeypatch):
    """When the account is already ACTIVE (routine li_at refresh), no DB write for status."""
    from releasi.scheduler import runner
    from releasi.db.models import AccountStatus
    from releasi.linkedin.navigator import LinkedInNavigator
    account, context, repo, page, nav_result = _make_reauth_mocks(
        li_rm="my-li-rm", account_status=AccountStatus.ACTIVE,
    )
    monkeypatch.setattr(LinkedInNavigator, "go_to_feed", AsyncMock(return_value=nav_result))
    monkeypatch.setattr(runner, "_record_session_touch", AsyncMock())
    result = await runner._attempt_li_rm_reauth(context, account, repo)
    assert result is True
    repo.update_account.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_returns_false_on_network_error(monkeypatch):
    """Feed navigation fails (proxy/network) → return False, log network_error, no cookie_expired."""
    from releasi.scheduler import runner
    from releasi.linkedin.navigator import LinkedInNavigator
    account, context, repo, page, nav_result = _make_reauth_mocks(
        li_rm="my-li-rm", feed_success=False, feed_valid=False,
    )
    monkeypatch.setattr(LinkedInNavigator, "go_to_feed", AsyncMock(return_value=nav_result))
    monkeypatch.setattr(runner, "_record_session_touch", AsyncMock())
    result = await runner._attempt_li_rm_reauth(context, account, repo)
    assert result is False
    repo.log_session_event.assert_awaited_once_with(account.id, "reauth", "network_error")
    repo.update_account.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_returns_false_when_li_rm_also_dead(monkeypatch):
    """Feed succeeds but session_valid=False → li_rm is revoked too, caller must mark expired."""
    from releasi.scheduler import runner
    from releasi.linkedin.navigator import LinkedInNavigator
    account, context, repo, page, nav_result = _make_reauth_mocks(
        li_rm="my-li-rm", feed_success=True, feed_valid=False,
    )
    monkeypatch.setattr(LinkedInNavigator, "go_to_feed", AsyncMock(return_value=nav_result))
    record_touch = AsyncMock()
    monkeypatch.setattr(runner, "_record_session_touch", record_touch)
    result = await runner._attempt_li_rm_reauth(context, account, repo)
    assert result is False
    repo.update_account.assert_not_called()
    record_touch.assert_awaited_once()
    _, kwargs = record_touch.call_args
    assert kwargs.get("result") == "reauth_failed"


@pytest.mark.asyncio
async def test_reauth_returns_false_on_exception(monkeypatch):
    """Any unexpected exception → return False (fail-safe), never propagates."""
    from releasi.scheduler import runner
    from releasi.linkedin.navigator import LinkedInNavigator
    account, context, repo, page, _ = _make_reauth_mocks(li_rm="my-li-rm")
    monkeypatch.setattr(LinkedInNavigator, "go_to_feed", AsyncMock(side_effect=RuntimeError("boom")))
    result = await runner._attempt_li_rm_reauth(context, account, repo)
    assert result is False
    repo.update_account.assert_not_called()
