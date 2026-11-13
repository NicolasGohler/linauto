"""Tests for the pure dispatcher decision functions.

These guard the per-result control flow that decides whether to mark
cookie_expired, burn a lead to ERROR, increment counters, etc. The
dispatcher itself is a thin layer of side effects around these — testing
the pure functions catches regressions without mocking the browser pool
and DB.
"""
from __future__ import annotations

import pytest

from releasi.safety.dispatch_decisions import (
    AccountAction,
    LeadAction,
    classify_connection_result,
    classify_followup_result,
)


# ── Followup classifier ────────────────────────────────────────────────────

class TestFollowupClassifier:
    def _classify(self, result, session=0, network=0):
        return classify_followup_result(
            result, session, network,
            account_name="Nicolas", campaign_name="C1",
        )

    def test_success_resets_counters_and_marks_sent(self):
        intent = self._classify({"success": True}, session=2, network=2)
        assert intent.lead_action == LeadAction.MARK_SENT
        assert intent.account_action == AccountAction.NONE
        assert intent.consecutive_session == 0
        assert intent.consecutive_network == 0
        assert intent.stop_account is False

    def test_skipped_resets_counters_and_marks_sent(self):
        """Prior conversation detected — treat as sent, don't retry."""
        intent = self._classify({"skipped": True}, session=2, network=1)
        assert intent.lead_action == LeadAction.MARK_SENT
        assert intent.consecutive_session == 0
        assert intent.consecutive_network == 0

    def test_session_expired_marks_cookie_expired_immediately(self):
        """One explicit session signal → flag now, don't wait for 3."""
        intent = self._classify({"session_expired": True, "fatal": True})
        assert intent.account_action == AccountAction.MARK_COOKIE_EXPIRED
        assert intent.lead_action == LeadAction.NONE  # preserve FOLLOWUP_SCHEDULED
        assert intent.stop_account is True
        assert intent.slack_message is not None
        assert "Cookie expired" in intent.slack_message
        assert "Nicolas" in intent.slack_message

    def test_session_expired_resets_counters(self):
        """Even if we had pending session strikes, an explicit signal
        supersedes — the cookie IS expired, no need to count anymore."""
        intent = self._classify({"session_expired": True}, session=2)
        assert intent.consecutive_session == 0
        assert intent.consecutive_network == 0

    def test_network_error_increments_network_counter(self):
        intent = self._classify({"network_error": True}, network=0)
        assert intent.consecutive_network == 1
        assert intent.consecutive_session == 0
        assert intent.account_action == AccountAction.NONE
        assert intent.lead_action == LeadAction.NONE  # don't burn lead on network
        assert intent.stop_account is False

    def test_network_error_third_strike_stops_account(self):
        intent = self._classify({"network_error": True}, network=2)
        assert intent.consecutive_network == 3
        assert intent.stop_account is True
        assert intent.account_action == AccountAction.NONE  # network ≠ cookie
        assert intent.lead_action == LeadAction.NONE

    def test_fatal_captcha_stops_with_slack_no_cookie_flag(self):
        intent = self._classify({"fatal": True})
        assert intent.stop_account is True
        assert intent.account_action == AccountAction.NONE
        assert intent.slack_message is not None
        assert "CAPTCHA" in intent.slack_message
        assert intent.lead_action == LeadAction.MARK_ERROR

    def test_generic_failure_marks_error_and_increments_session_counter(self):
        intent = self._classify({}, session=0)
        assert intent.lead_action == LeadAction.MARK_ERROR
        assert intent.consecutive_session == 1
        assert intent.consecutive_network == 0
        assert intent.account_action == AccountAction.NONE
        assert intent.stop_account is False

    def test_generic_failure_third_strike_stops_without_claiming_expiry(self):
        intent = self._classify({}, session=2)
        assert intent.consecutive_session == 3
        assert intent.account_action == AccountAction.NONE
        assert intent.stop_account is True
        assert intent.lead_action == LeadAction.MARK_ERROR  # the failing lead still burns
        assert "3 consecutive" in intent.slack_message

    def test_priority_session_beats_network(self):
        """If both flags are set (defensive coding in executor), session wins."""
        intent = self._classify({"session_expired": True, "network_error": True})
        assert intent.account_action == AccountAction.MARK_COOKIE_EXPIRED


# ── Connection-request classifier ──────────────────────────────────────────

class TestConnectionClassifier:
    def _classify(self, result, session=0, network=0):
        return classify_connection_result(
            result, session, network,
            account_name="Nicolas", campaign_name="C1",
        )

    def test_success_resets_counters_no_lead_write(self):
        """Connection-request executor already writes the lead; dispatcher
        only resets counters."""
        intent = self._classify({"success": True}, session=2, network=2)
        assert intent.lead_action == LeadAction.NONE
        assert intent.consecutive_session == 0
        assert intent.consecutive_network == 0

    def test_skipped_resets_counters_no_lead_write(self):
        intent = self._classify({"skipped": True}, session=1)
        assert intent.lead_action == LeadAction.NONE
        assert intent.consecutive_session == 0

    def test_session_expired_preserves_scheduled_leads(self):
        intent = self._classify({"session_expired": True, "fatal": True})
        assert intent.account_action == AccountAction.MARK_COOKIE_EXPIRED
        assert intent.reset_scheduled_to_pending is False
        assert intent.stop_account is True
        assert "preserved" in intent.slack_message

    def test_generic_errors_do_not_claim_cookie_expiry(self):
        """The 3-strike path should also revert scheduled leads, matching
        the historical behaviour of the connection dispatcher."""
        intent = self._classify({}, session=2)
        assert intent.account_action == AccountAction.NONE
        assert intent.reset_scheduled_to_pending is False
        assert intent.stop_account
        assert intent.account_backoff_seconds == 3600

    def test_dom_failures_are_not_network_or_session_errors(self):
        intent = self._classify({"failure_kind": "profile_dom", "error": "no_connect_button"}, session=2)
        assert intent.account_action == AccountAction.NONE
        assert intent.consecutive_network == 0
        assert intent.retry_delay_seconds == 86400
        assert intent.stop_account
        assert intent.account_backoff_seconds == 3600

    def test_network_error_does_not_revert_scheduled(self):
        """Network failures aren't a cookie problem — don't reset leads."""
        intent = self._classify({"network_error": True}, network=2)
        assert intent.reset_scheduled_to_pending is False
        assert intent.account_action == AccountAction.NONE

    def test_fatal_no_cookie_flag_no_revert(self):
        intent = self._classify({"fatal": True})
        assert intent.stop_account is True
        assert intent.account_action == AccountAction.NONE
        assert intent.reset_scheduled_to_pending is False
        assert "CAPTCHA" in intent.slack_message


# ── End-to-end counter sequencing ──────────────────────────────────────────

class TestCounterSequencing:
    """Walks a sequence of results through the followup classifier and
    asserts the counters and final state. Catches off-by-one bugs in the
    consecutive-counter logic."""

    def _run(self, results):
        session, network = 0, 0
        intents = []
        for r in results:
            intent = classify_followup_result(
                r, session, network,
                account_name="A", campaign_name="C",
            )
            session = intent.consecutive_session
            network = intent.consecutive_network
            intents.append(intent)
            if intent.stop_account:
                break
        return intents, session, network

    def test_two_errors_then_success_resets_to_zero(self):
        intents, sess, net = self._run([{}, {}, {"success": True}])
        assert len(intents) == 3
        assert intents[0].consecutive_session == 1
        assert intents[1].consecutive_session == 2
        assert intents[2].consecutive_session == 0
        assert sess == 0

    def test_three_generic_errors_stop_without_cookie_expiry(self):
        intents, sess, net = self._run([{}, {}, {}])
        assert len(intents) == 3
        assert intents[2].account_action == AccountAction.NONE
        assert intents[2].stop_account is True

    def test_mixed_network_and_session_errors_dont_double_count(self):
        """Network errors should reset the session counter and vice versa,
        so an interleaved sequence never hits 3-strike on either."""
        results = [{}, {"network_error": True}, {}, {"network_error": True}, {}]
        intents, sess, net = self._run(results)
        assert len(intents) == 5
        # Each error resets the OTHER counter
        assert intents[0].consecutive_session == 1
        assert intents[1].consecutive_session == 0  # reset by network error
        assert intents[1].consecutive_network == 1
        assert intents[2].consecutive_session == 1  # incremented again
        assert intents[2].consecutive_network == 0
        # Neither counter ever hit 3
        for intent in intents:
            assert intent.stop_account is False

    def test_session_expired_short_circuits_after_two_generic_errors(self):
        """Two generic errors then an explicit session signal: cookie_expired
        fires on the third even though we hadn't reached 3 generic strikes."""
        results = [{}, {}, {"session_expired": True}]
        intents, _, _ = self._run(results)
        assert len(intents) == 3
        assert intents[0].account_action == AccountAction.NONE
        assert intents[1].account_action == AccountAction.NONE
        assert intents[2].account_action == AccountAction.MARK_COOKIE_EXPIRED
        assert intents[2].stop_account is True


def test_followup_partial_sequence_reschedules():
    from datetime import datetime
    from releasi.safety.dispatch_decisions import classify_followup_result, LeadAction
    when = datetime(2026, 11, 14, 10, 0)
    intent = classify_followup_result(
        {"success": True, "messages_sent": 1, "continue_at": when}, 2, 1, "acct", "camp"
    )
    assert intent.lead_action == LeadAction.RESCHEDULE
    assert intent.continue_at == when
    assert intent.consecutive_session == 0 and intent.consecutive_network == 0
