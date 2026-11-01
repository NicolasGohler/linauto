import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from releasi.linkedin import actions as actions_module
from releasi.linkedin.actions import LinkedInActions
from releasi.linkedin.withdrawal_jobs import WithdrawalJobStore
from releasi.linkedin.withdrawal_safety import (
    WithdrawalStopped, invitation_pairs, profile_url, withdrawal_receipt,
)


def card(index):
    return {"invitation": f"InvitationUrn(invitationId={index})",
            "profile": f"https://www.linkedin.com/in/person-{index}/"}


def test_pairs_do_not_guess_by_document_position():
    body = json.dumps({"ids": ["InvitationUrn(invitationId=1)", "InvitationUrn(invitationId=2)"],
                       "urls": [card(2)["profile"], card(1)["profile"]]})
    with pytest.raises(WithdrawalStopped):
        invitation_pairs(body)


def test_pairs_require_card_level_identity():
    body = "1:" + json.dumps({"cards": [card(1), card(2)]})
    assert invitation_pairs(body) == [("1", profile_url(card(1)["profile"])),
                                      ("2", profile_url(card(2)["profile"]))]


@pytest.mark.parametrize("body", ["<html>Login</html>", "", '{"success":true}',
                                   '{"invitationId":"2","success":true}',
                                   '{"invitationId":"1","success":false}'])
def test_http_success_without_identified_receipt_is_not_confirmation(body):
    assert not withdrawal_receipt(body, "1")


def test_identified_receipt_and_errors():
    assert withdrawal_receipt('1:{"invitationId":"1","success":true}', "1")
    assert not withdrawal_receipt('{"invitationId":"1","success":true,"error":"blocked"}', "1")


@pytest.mark.parametrize("value", ["https://evil.test/in/person", "https://www.linkedin.com/company/foo",
                                    "https://www.linkedin.com/in/person/extra", None])
def test_invalid_profile_rejected(value):
    assert profile_url(value) is None


class FakePage:
    url = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"

    def __init__(self, *, total=1500, failure=None):
        self.total = total
        self.failure = failure
        self.context = SimpleNamespace(cookies=AsyncMock(return_value=[{"name": "JSESSIONID", "value": "fixture"}]))
        self.reads = []
        self.mutations = []

    async def evaluate(self, script, arg=None):
        assert "scrollTo" not in script and "scrollIntoView" not in script
        if arg is None:
            return self.total
        url, headers, body = arg
        assert "AbortController" in script and "20000" in script
        if "pagination" in url:
            start = body["clientArguments"]["payload"]["startIndex"]
            self.reads.append(start)
            assert body["paginationRequest"]["retryCount"] == 0
            assert body["paginationRequest"]["trigger"]["itemDistanceTrigger"]["preloadLength"] == 10
            if self.failure == "pagination":
                return {"status": 500, "body": "failure"}
            return {"status": 200, "body": json.dumps({"cards": [card(i) for i in range(start + 1, min(start + 11, self.total + 1))]})}
        index = body["requestedArguments"]["payload"]["invitationUrn"]["invitationId"]
        self.mutations.append(index)
        if self.failure == "mutation":
            return {"status": 429, "body": "rate limited"}
        if self.failure == "unknown":
            return {"status": 200, "body": "not an acknowledgement"}
        if self.failure == "hang":
            await asyncio.Event().wait()
        return {"status": 200, "body": json.dumps({"invitationId": index, "success": True})}


def actions_for(page):
    actions = LinkedInActions(page)
    actions.navigator = SimpleNamespace(go_to_invitation_manager=AsyncMock(
        return_value=SimpleNamespace(success=True, session_valid=True)))
    actions.delay = SimpleNamespace(micro_delay=AsyncMock())
    return actions


@pytest.mark.parametrize("order", ["oldest", "newest"])
async def test_100_withdrawals_with_bounded_pagination_and_progress(monkeypatch, order):
    monkeypatch.setattr(actions_module.asyncio, "sleep", AsyncMock())
    page = FakePage()
    confirmed, attempted = AsyncMock(), AsyncMock()
    result = await actions_for(page).withdraw_invitations(100, order, on_attempt=attempted, on_withdrawn=confirmed)
    assert len(result) == confirmed.await_count == attempted.await_count == len(page.mutations) == 100
    assert len(page.reads) == (11 if order == "oldest" else 10)
    assert page.mutations[0] == ("1500" if order == "oldest" else "1")


async def test_pagination_failure_stops_without_fallback_or_mutation():
    page = FakePage(failure="pagination")
    with pytest.raises(WithdrawalStopped, match="no scrolling"):
        await actions_for(page).withdraw_invitations(100)
    assert page.reads == [0] and not page.mutations


@pytest.mark.parametrize("failure", ["mutation", "unknown", "hang"])
async def test_uncertain_mutation_is_not_retried_or_confirmed(monkeypatch, failure):
    monkeypatch.setattr(actions_module, "WITHDRAWAL_STEP_TIMEOUT", 0.01)
    page = FakePage(failure=failure)
    confirmed, attempted = AsyncMock(), AsyncMock()
    with pytest.raises(WithdrawalStopped):
        await actions_for(page).withdraw_invitations(1, "newest", on_attempt=attempted, on_withdrawn=confirmed)
    assert len(page.mutations) == attempted.await_count == 1
    confirmed.assert_not_awaited()


@pytest.mark.parametrize("count", [0, 101, True, 1.1])
async def test_invalid_count_never_opens_linkedin(count):
    page = FakePage()
    actions = actions_for(page)
    with pytest.raises(WithdrawalStopped):
        await actions.withdraw_invitations(count)
    actions.navigator.go_to_invitation_manager.assert_not_awaited()


@pytest.mark.parametrize("status", [401, 403, 429])
async def test_restriction_stops_and_requests_account_pause(status):
    page = FakePage()
    page.evaluate = AsyncMock(return_value={"status": status})
    with pytest.raises(WithdrawalStopped) as exc:
        await actions_for(page)._withdrawal_evaluate("fixture")
    assert exc.value.account_action == "paused"


async def test_login_redirect_is_not_classified_as_generic_network_failure():
    page = FakePage()
    page.evaluate = AsyncMock(return_value={"status": 200, "final_url": "https://www.linkedin.com/login"})
    with pytest.raises(WithdrawalStopped) as exc:
        await actions_for(page)._withdrawal_evaluate("fixture")
    assert exc.value.account_action == "cookie_expired"


def test_durable_interruption_and_uncertain_retry_hold(tmp_path):
    account = str(uuid4())
    store = WithdrawalJobStore(tmp_path / "jobs")
    job = store.claim(account, 100, "oldest")
    with pytest.raises(WithdrawalStopped, match="already queued"):
        store.claim(account, 100, "oldest")
    store.update(account, withdrawn=[profile_url(card(1)["profile"])], in_flight={"invitation_id": "2"})
    restarted = WithdrawalJobStore(store.directory)
    state = restarted.read(account)
    assert state["job_id"] == job and state["status"] == "error" and state["needs_review"]
    assert len(state["withdrawn"]) == 1
    with pytest.raises(WithdrawalStopped, match="operator review"):
        restarted.claim(account, 100, "oldest")
    assert (store.directory / (account + ".json")).stat().st_mode & 0o777 == 0o600


def test_failure_blocks_auto_but_allows_explicit_manual_retry_when_no_uncertainty(tmp_path):
    account = str(uuid4())
    store = WithdrawalJobStore(tmp_path)
    store.claim(account, 100, "oldest")
    store.update(account, status="error", error="pagination failed")
    store.release(account)
    with pytest.raises(WithdrawalStopped, match="Automatic withdrawals are held"):
        store.claim(account, 100, "oldest", automatic=True)
    assert store.claim(account, 100, "oldest").startswith("wd-")


async def test_job_keeps_partial_progress_and_closes_before_alert(monkeypatch, tmp_path):
    from releasi.campaign import withdrawal
    from releasi.scheduler import runner
    monkeypatch.setattr(runner, "_account_proxy_url", AsyncMock(return_value="fixture-proxy"))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(withdrawal, "notify_withdrawal_issue", notify)
    account = SimpleNamespace(id=str(uuid4()), name="Fixture", status="active", archived=False, paused_until=None)
    store = WithdrawalJobStore(tmp_path)
    job = store.claim(account.id, 100, "oldest")
    pool = SimpleNamespace(is_busy=lambda _: False, acquire=AsyncMock(), close_owned_page=AsyncMock())
    pool.acquire.return_value.new_page = AsyncMock(return_value=object())
    repo = SimpleNamespace(mark_lead_withdrawn_by_slug=AsyncMock(return_value=True), log_action=AsyncMock())

    class Partial:
        def __init__(self, page):
            pass

        async def withdraw_invitations(self, count, **kwargs):
            await kwargs["on_attempt"]("1", card(1)["profile"])
            await kwargs["on_withdrawn"](card(1)["profile"])
            await kwargs["on_attempt"]("2", card(2)["profile"])
            raise WithdrawalStopped("Uncertain second result")

    monkeypatch.setattr(withdrawal, "LinkedInActions", Partial)
    state = await withdrawal.execute_withdrawal(account, repo, job, 100, "oldest", store=store, pool=pool)
    assert state["status"] == "error" and state["needs_review"]
    assert state["withdrawn"] == [profile_url(card(1)["profile"])] and state["db_updated"] == 1
    repo.mark_lead_withdrawn_by_slug.assert_awaited_once_with("person-1", account_id=account.id)
    pool.close_owned_page.assert_awaited_once()
    notify.assert_awaited_once()
    assert not store.active
