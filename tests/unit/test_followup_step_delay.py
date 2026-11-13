"""Follow-up sequence with a step-2 delay, reply check and missing-variable guard."""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from releasi.campaign import executor as executor_mod
from releasi.linkedin.actions import ActionResult, ActionStatus


class FakeRepo:
    def __init__(self, sent_index: int = 0):
        self.session = None
        self.sent_index = sent_index
        self.logged = []

    async def get_last_successful_followup_index(self, lead_id):
        return self.sent_index

    async def log_action(self, **kw):
        self.logged.append(kw)

    async def increment_daily_stat(self, *a, **kw):
        pass


class FakePage:
    async def goto(self, *a, **kw):
        pass

    async def close(self):
        pass


class FakeContext:
    async def new_page(self):
        return FakePage()


def _install(monkeypatch, replies):
    calls = []

    class FakeActions:
        def __init__(self, page):
            pass

        async def send_message(self, url, msg, skip_prior_conversation_check=False,
                               conversation_routing="skip", message_prior_only=None):
            calls.append({"msg": msg, "skip_check": skip_prior_conversation_check,
                          "routing": conversation_routing})
            return replies.pop(0) if replies else ActionResult(ActionStatus.SUCCESS)

    async def no_dnc(session, lead):
        return None

    async def no_sleep(_):
        pass

    monkeypatch.setattr(executor_mod, "LinkedInActions", FakeActions)
    monkeypatch.setattr(executor_mod.suppression, "check_lead", no_dnc)
    monkeypatch.setattr(executor_mod.asyncio, "sleep", no_sleep)
    return calls


def _campaign(step2=2.0):
    return SimpleNamespace(
        name="c", id="camp",
        followup_message_1="Hi {{first_name}}",
        followup_message_2="they're all {{signal_line}}",
        followup_message_3="{{example_1}} and {{example_2}}, want more?",
        followup_step2_delay_hours=step2,
    )


def _lead(**extra):
    data = {"signal_line": "hiring SDRs", "example_1": "Acme", "example_2": "Beta"}
    data.update(extra)
    return SimpleNamespace(id="l1", first_name="Sam", last_name=None, company="Co", title=None,
                           linkedin_url="https://linkedin.com/in/sam", extra_data=data)


def _run(repo, campaign, lead):
    ex = executor_mod.CampaignExecutor(repo, browser_context=FakeContext())
    return asyncio.run(ex.execute_followup_sequence(SimpleNamespace(id="a", name="acct"), campaign, lead))


def test_first_visit_sends_message_1_then_schedules_rest(monkeypatch):
    calls = _install(monkeypatch, [])
    before = datetime.utcnow()
    res = _run(FakeRepo(0), _campaign(), _lead())
    assert [c["msg"] for c in calls] == ["Hi Sam"]
    assert calls[0]["routing"] == "skip" and calls[0]["skip_check"] is False
    assert res["success"] and res["started_new"]
    hours = (res["continue_at"] - before).total_seconds() / 3600
    assert 1.6 - 0.01 <= hours <= 2.6 + 0.01


def test_second_visit_checks_for_reply_then_sends_2_and_3(monkeypatch):
    calls = _install(monkeypatch, [])
    res = _run(FakeRepo(1), _campaign(), _lead())
    assert [c["msg"] for c in calls] == ["they're all hiring SDRs", "Acme and Beta, want more?"]
    assert calls[0]["routing"] == "reply_only" and calls[0]["skip_check"] is False
    assert calls[1]["skip_check"] is True
    assert res["success"] and res["continue_at"] is None and not res["started_new"]


def test_second_visit_stops_when_lead_replied(monkeypatch):
    calls = _install(monkeypatch, [ActionResult(ActionStatus.SKIPPED, reason="existing_conversation_replied")])
    res = _run(FakeRepo(1), _campaign(), _lead())
    assert len(calls) == 1
    assert res["skipped"] and not res["success"]


def test_missing_variable_stops_before_sending(monkeypatch):
    calls = _install(monkeypatch, [])
    res = _run(FakeRepo(1), _campaign(), _lead(example_2=""))
    assert [c["msg"] for c in calls] == ["they're all hiring SDRs"]
    assert res["skipped"] and res["reason"] == "missing_variables:example_2"
    assert res["success"]  # message 2 went out; sequence ends there


def test_no_step_delay_keeps_legacy_back_to_back(monkeypatch):
    calls = _install(monkeypatch, [])
    res = _run(FakeRepo(0), _campaign(step2=0), _lead())
    assert len(calls) == 3 and res["continue_at"] is None
