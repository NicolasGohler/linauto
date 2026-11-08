"""Campaign execution orchestrator — ties browser actions, state machine, and logging together."""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import List, Optional, Sequence

import structlog

from playwright.async_api import BrowserContext

from releasi.db.models import (
    Lead, LeadStatus, Account, Campaign, ActionType, ActionLogStatus,
    Broadcast, BroadcastLead,
)
from releasi.db.repository import Repository
from releasi.linkedin.browser import LinkedInBrowser
from releasi.linkedin.actions import LinkedInActions, ActionStatus
from releasi.campaign.template import render_template
from releasi.campaign.state_machine import validate_transition
from releasi.linkedin.profile_filter import ProfileFilters
from releasi.safety.delays import DelayGenerator
from releasi.safety import suppression

logger = structlog.get_logger()

# Re-export classification helpers from the no-deps module so existing
# callers (and tests that mock these names) keep working.
from releasi.safety.error_signals import (
    is_network_error as _is_network_error,
    is_session_expired_signal as _is_session_expired_signal,
)


class CampaignExecutor:
    """Executes campaign actions for a batch of leads."""

    def __init__(self, repo: Repository, browser_context: Optional[BrowserContext] = None):
        self.repo = repo
        self.delay = DelayGenerator()
        self._browser: Optional[LinkedInBrowser] = None
        self._shared_context = browser_context

    async def execute_single_lead(
        self,
        account: Account,
        campaign: Campaign,
        lead: Lead,
    ) -> dict:
        """
        Execute a single connection request (used by the scheduler dispatcher).

        When ``self._shared_context`` is set (pool mode), opens/closes only
        pages on the shared browser.  Otherwise creates an ephemeral browser.
        """
        # `session_expired` is the explicit cookie-expiry signal (redirect
        # loop, /login redirect, authwall, checkpoint). When set, the
        # dispatcher should mark the account cookie_expired immediately
        # rather than waiting for 3 consecutive errors.
        result = {
            "success": False,
            "limit_reached": False,
            "fatal": False,
            "skipped": False,
            "session_expired": False,
        }

        if not lead.linkedin_url:
            result["skipped"] = True
            result["reason"] = "no_linkedin_url"
            return result

        # Do-not-contact list: never send to suppressed companies/people.
        _dnc = await suppression.check_lead(self.repo.session, lead)
        if _dnc:
            logger.warning("executor.do_not_contact", url=lead.linkedin_url, rule=_dnc)
            await self.repo.update_lead(
                lead, campaign_id_override=campaign.id,
                status=LeadStatus.SKIPPED, error_message=f"do_not_contact:{_dnc}",
            )
            await self.repo.log_action(
                account_id=account.id, campaign_id=campaign.id, lead_id=lead.id,
                action_type=ActionType.CONNECTION_REQUEST,
                status=ActionLogStatus.SKIPPED,
                details={"reason": f"do_not_contact:{_dnc}"},
            )
            result["skipped"] = True
            result["reason"] = f"do_not_contact:{_dnc}"
            return result

        # Pool mode: use shared context, only manage pages
        browser: Optional[LinkedInBrowser] = None
        if self._shared_context:
            page = await self._shared_context.new_page()
        else:
            browser = LinkedInBrowser()
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            if not valid:
                logger.error("executor.session_invalid", account=account.name)
                result["fatal"] = True
                result["session_expired"] = True
                await browser.close()
                return result
            page = await browser.new_page()

        try:
            actions = LinkedInActions(page)

            # Optional: browsing noise before request
            try:
                from releasi.linkedin.noise import BrowsingNoise
                noise = BrowsingNoise(page)
                import random
                if random.random() < 0.4:  # 40% chance of noise before action
                    await noise.view_random_profile()
            except Exception:
                pass  # Noise is best-effort

            # Render message
            message = None
            if campaign.connection_message_template:
                message = render_template(campaign.connection_message_template, lead)

            # Build profile filters from campaign config
            filters = ProfileFilters(
                no_photo=campaign.filter_no_photo,
                min_connections=campaign.filter_min_connections,
                exclude_open_to_work=campaign.filter_exclude_open_to_work,
            )

            # Execute
            action_result = await actions.send_connection_request(lead.linkedin_url, message, filters=filters)
            result["error"] = action_result.reason

            if action_result.status == ActionStatus.SUCCESS:
                try:
                    validate_transition(lead.status, LeadStatus.CONNECTION_REQUESTED)
                except Exception:
                    logger.warning("executor.transition_stale_status", lead_id=lead.id,
                                   lead_status=lead.status, target="CONNECTION_REQUESTED")
                await self.repo.update_lead(
                    lead,
                    campaign_id_override=campaign.id,
                    status=LeadStatus.CONNECTION_REQUESTED,
                    connection_requested_at=datetime.utcnow(),
                )
                await self.repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    action_type=ActionType.CONNECTION_REQUEST,
                    status=ActionLogStatus.SUCCESS,
                )
                await self.repo.increment_daily_stat(account.id, "connection_requests_sent")
                result["success"] = True

            elif action_result.status == ActionStatus.LIMIT_REACHED:
                # Differentiate weekly quota (pause until Monday) from soft rate-limit modal
                # (2–4 hour backoff only — leads stay SCHEDULED for later today).
                if action_result.reason == "rate_limit_modal":
                    result["soft_limit_reached"] = True
                else:
                    result["limit_reached"] = True
                await self.repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    action_type=ActionType.LIMIT_DETECTED,
                    status=ActionLogStatus.FAILED,
                    details=action_result.details,
                )

            elif action_result.status in (ActionStatus.CAPTCHA, ActionStatus.SESSION_EXPIRED):
                result["fatal"] = True
                result["session_expired"] = action_result.status == ActionStatus.SESSION_EXPIRED
                result["captcha"] = action_result.status == ActionStatus.CAPTCHA
                await self.repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    action_type=ActionType.ERROR,
                    status=ActionLogStatus.FAILED,
                    details={"reason": action_result.status.value},
                )

            elif action_result.status == ActionStatus.INVALID:
                # Lead data is permanently bad (404, deleted profile) — never retry.
                try:
                    validate_transition(lead.status, LeadStatus.INVALID)
                except Exception:
                    logger.warning("executor.transition_stale_status", lead_id=lead.id,
                                   lead_status=lead.status, target="INVALID")
                await self.repo.update_lead(
                    lead,
                    campaign_id_override=campaign.id, status=LeadStatus.INVALID, error_message=action_result.reason
                )
                await self.repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    action_type=ActionType.CONNECTION_REQUEST,
                    status=ActionLogStatus.SKIPPED,
                    details={"reason": action_result.reason},
                )
                result["skipped"] = True  # counts as skipped for dispatcher backfill

            elif action_result.status == ActionStatus.SKIPPED:
                # actions.py tags the specific detection path in the reason
                # (already_connected:1st_degree_badge / :remove_in_dropdown /
                # :removal_dialog) so we can tell them apart in action_log.
                # Match on the shared prefix here.
                reason_str = action_result.reason or ""
                if reason_str == "already_connected" or reason_str.startswith("already_connected:"):
                    # Already a 1st-degree connection — mark CONNECTED, not SKIPPED.
                    # Don't count as a new send; just update the DB to reflect reality.
                    try:
                        validate_transition(lead.status, LeadStatus.CONNECTED)
                    except Exception:
                        logger.warning("executor.transition_stale_status", lead_id=lead.id,
                                       lead_status=lead.status, target="CONNECTED")
                    await self.repo.update_lead(
                        lead,
                        campaign_id_override=campaign.id,
                        status=LeadStatus.CONNECTED,
                        connection_accepted_at=datetime.utcnow(),
                    )
                    # Log as SKIPPED, not SUCCESS: no invitation was sent — the
                    # lead was already a 1st-degree connection. Logging it as
                    # SUCCESS inflated the activity feed and campaign total_sent
                    # (which count SUCCESS CONNECTION_REQUEST rows) by treating a
                    # pre-existing connection as a freshly sent request.
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST,
                        status=ActionLogStatus.SKIPPED,
                        details={"reason": reason_str or "already_connected"},
                    )
                else:
                    # Phase 3b: Lead.status is a legacy column that can be stale relative
                    # to CLA.status. A lead dispatched by the scheduler always has a valid
                    # CLA state (scheduled/pending), so this transition is safe. If the
                    # legacy column disagrees, skip the guard rather than looping forever.
                    try:
                        validate_transition(lead.status, LeadStatus.SKIPPED)
                    except Exception:
                        logger.warning(
                            "executor.skip_transition_stale_status",
                            lead_id=lead.id,
                            lead_status=lead.status,
                        )
                    await self.repo.update_lead(
                        lead, campaign_id_override=campaign.id,
                        status=LeadStatus.SKIPPED, error_message=action_result.reason
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST,
                        status=ActionLogStatus.SKIPPED,
                        details={"reason": action_result.reason},
                    )
                result["skipped"] = True

            else:  # ERROR
                reason = action_result.reason or ""
                # Only SESSION_EXPIRED above confirms auth loss. Error text
                # (including redirect loops) is an inconclusive navigation hint.
                if reason in ("no_connect_button", "profile_identity_unavailable", "profile_render_incomplete", "profile_error_page"):
                    result["failure_kind"] = "profile_dom"
                elif action_result.details.get("pre_send") and (
                    _is_network_error(reason) or _is_session_expired_signal(reason)
                ):
                    # Action navigation failed before Connect/Send was reached.
                    result["network_error"] = True
                else:
                    try:
                        validate_transition(lead.status, LeadStatus.ERROR)
                    except Exception:
                        # The scheduler selected an eligible assignment; the
                        # canonical lead can carry another campaign's status.
                        logger.warning("executor.error_transition_stale_status", lead_id=lead.id,
                                       lead_status=lead.status, target="ERROR")
                    await self.repo.update_lead(
                        lead,
                        campaign_id_override=campaign.id,
                        status=LeadStatus.ERROR,
                        retry_count=lead.retry_count + 1,
                        error_message=reason,
                    )
                    await self.repo.increment_daily_stat(account.id, "errors")
                    await self.repo.log_action(
                        account_id=account.id, campaign_id=campaign.id, lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST, status=ActionLogStatus.FAILED,
                        details={"reason": reason, "failure_kind": "action_error", "retryable": False},
                    )

        except Exception as e:
            err_str = str(e)
            result["error"] = err_str[:500]
            logger.error("executor.single_lead_failed", error=err_str)
            if "Cannot transition" in err_str:
                # Lead/assignment desync: the assignment row said pending but the
                # canonical lead row is already in a terminal state (e.g. error).
                # This is a data integrity issue, not a session or network problem.
                # Mark as skipped so the dispatcher moves on without pausing the
                # account or sending a false-positive CAPTCHA Slack alert.
                logger.warning(
                    "executor.status_desync_skipped",
                    lead_url=getattr(lead, "linkedin_url", "unknown"),
                    error=err_str,
                )
                result["skipped"] = True
            else:
                try:
                    await self.repo.update_lead(
                        lead,
                        campaign_id_override=campaign.id,
                        status=LeadStatus.ERROR,
                        retry_count=lead.retry_count + 1,
                        error_message=err_str[:500],
                    )
                except Exception:
                    pass  # Best-effort — don't mask the original error
                await self.repo.increment_daily_stat(account.id, "errors")
                await self.repo.log_action(
                    account_id=account.id, campaign_id=campaign.id, lead_id=lead.id,
                    action_type=ActionType.CONNECTION_REQUEST, status=ActionLogStatus.FAILED,
                    details={"reason": err_str[:500], "failure_kind": "uncertain_outcome", "retryable": False},
                )
                # An exception can occur after Send. Never automatically retry
                # an uncertain outcome, even when the exception is a timeout.
                result["fatal"] = True
        finally:
            try:
                await page.goto("about:blank", wait_until="domcontentloaded", timeout=2000)
            except Exception:
                pass
            await page.close()
            if browser:
                await browser.close()

        return result

    async def execute_followup_sequence(
        self,
        account: Account,
        campaign: Campaign,
        lead: Lead,
    ) -> dict:
        """
        Send 1-3 follow-up messages immediately after connection acceptance.

        Messages are sent with 30-60s delays between each.
        Returns result dict with messages_sent count and success flag.
        """
        # `session_expired` and `network_error` mirror the connection-request
        # path so dispatch_followups can apply the same consecutive-error
        # heuristics (3+ session errors → mark cookie_expired, network errors
        # don't penalise the session, etc.).
        result = {
            "success": False,
            "messages_sent": 0,
            "fatal": False,
            "skipped": False,
            "session_expired": False,
            "network_error": False,
            "reason": None,
        }

        # ── Guard 0: do-not-contact list ───────────────────────────────────────
        _dnc = await suppression.check_lead(self.repo.session, lead)
        if _dnc:
            logger.warning("followup.do_not_contact", url=lead.linkedin_url, rule=_dnc)
            await self.repo.update_lead(
                lead, campaign_id_override=campaign.id,
                status=LeadStatus.REMOVED, error_message=f"do_not_contact:{_dnc}",
            )
            result["skipped"] = True
            result["reason"] = f"do_not_contact:{_dnc}"
            return result

        # ── Guard 1: action-log resume ─────────────────────────────────────────
        # Check the highest message_index already successfully sent for this lead.
        # If all messages were sent in a prior run, skip entirely (dedup).
        # If some were sent (e.g. msg 1 ok, msg 2 crashed), resume from the next one.
        # Collect configured messages first so we know the total.
        messages: List[str] = []
        for attr in ("followup_message_1", "followup_message_2", "followup_message_3"):
            msg = getattr(campaign, attr, None)
            if msg:
                messages.append(msg)

        if not messages:
            logger.warning("followup.no_messages_configured", campaign=campaign.name)
            return result

        start_from = await self.repo.get_last_successful_followup_index(lead.id)
        # start_from is 1-based (message_index); loop i is 0-based.
        # start_from == len(messages) means all messages already sent → full skip.
        if start_from >= len(messages):
            logger.warning(
                "followup.action_log_dedup_skipped",
                url=lead.linkedin_url,
                messages_already_sent=start_from,
            )
            result["skipped"] = True
            return result
        if start_from > 0:
            logger.info(
                "followup.resuming_partial_sequence",
                url=lead.linkedin_url,
                resuming_from_index=start_from + 1,
            )

        # Pool mode: use shared context, only manage pages
        browser: Optional[LinkedInBrowser] = None
        if self._shared_context:
            page = await self._shared_context.new_page()
        else:
            browser = LinkedInBrowser()
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            if not valid:
                logger.error("followup.session_invalid", account=account.name)
                result["fatal"] = True
                result["session_expired"] = True
                result["reason"] = "session_validate_failed"
                await browser.close()
                return result
            page = await browser.new_page()

        try:
            actions = LinkedInActions(page)

            for i, msg_template in enumerate(messages):
                if i < start_from:
                    continue  # already sent in a prior run

                msg = render_template(msg_template, lead)

                action_result = await actions.send_message(
                    lead.linkedin_url,
                    msg,
                    skip_prior_conversation_check=(i > 0),
                )

                if action_result.status == ActionStatus.SKIPPED:
                    # Prior conversation detected — treat the whole sequence as skipped.
                    result["skipped"] = True
                    logger.info(
                        "followup.skipped_existing_conversation",
                        url=lead.linkedin_url,
                        reason=action_result.reason,
                    )
                    break

                elif action_result.status == ActionStatus.SUCCESS:
                    result["messages_sent"] += 1
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.FOLLOWUP_MESSAGE,
                        status=ActionLogStatus.SUCCESS,
                        details={"message_index": i + 1, "total": len(messages)},
                    )
                    await self.repo.increment_daily_stat(account.id, "followup_messages_sent")
                    logger.info(
                        "followup.message_sent",
                        url=lead.linkedin_url,
                        index=i + 1,
                        total=len(messages),
                    )

                    # Wait 10-20s between messages (skip after last)
                    if i < len(messages) - 1:
                        delay = random.uniform(10, 20)
                        logger.info("followup.waiting", seconds=round(delay))
                        await asyncio.sleep(delay)
                else:
                    # Stop on first failure
                    reason_str = action_result.reason or ""
                    logger.error(
                        "followup.message_failed",
                        url=lead.linkedin_url,
                        index=i + 1,
                        reason=reason_str,
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.FOLLOWUP_MESSAGE,
                        status=ActionLogStatus.FAILED,
                        details={
                            "message_index": i + 1,
                            "reason": reason_str,
                        },
                    )
                    result["reason"] = reason_str
                    # Classify the failure so dispatch_followups can apply the
                    # same consecutive-error heuristics as the connection
                    # dispatcher. Priority: explicit status → session signal
                    # in reason string → network signal → generic fatal.
                    if action_result.status == ActionStatus.SESSION_EXPIRED:
                        result["fatal"] = True
                        result["session_expired"] = True
                    elif action_result.status == ActionStatus.CAPTCHA:
                        result["fatal"] = True
                    elif _is_session_expired_signal(reason_str):
                        # ERR_TOO_MANY_REDIRECTS, /login redirect, authwall, etc.
                        result["fatal"] = True
                        result["session_expired"] = True
                    elif _is_network_error(reason_str):
                        result["network_error"] = True
                    break

            if result["messages_sent"] > 0:
                result["success"] = True

        except Exception as e:
            err_str = str(e)
            logger.error("followup.sequence_failed", error=err_str)
            result["reason"] = err_str
            # Same priority chain as the action-level error path.
            if _is_session_expired_signal(err_str):
                result["fatal"] = True
                result["session_expired"] = True
            elif _is_network_error(err_str):
                result["network_error"] = True
            else:
                result["fatal"] = True
        finally:
            try:
                await page.goto("about:blank", wait_until="domcontentloaded", timeout=2000)
            except Exception:
                pass
            await page.close()
            if browser:
                await browser.close()

        return result

    async def execute_broadcast_lead(
        self,
        account: Account,
        broadcast: Broadcast,
        broadcast_lead: BroadcastLead,
    ) -> dict:
        """Send the next message in a broadcast's sequence to a single lead.

        Called by dispatch_broadcasts. Uses the shared pool context if set,
        otherwise creates an ephemeral browser. Returns a result dict shaped
        like execute_single_lead / execute_followup_sequence so the dispatcher's
        session-health classifier can consume it uniformly.

        Result dict:
          - success: bool          — a message was successfully sent
          - message_index: int     — 1-based index of the message sent (0 if none)
          - skipped: bool
          - skipped_reason: str | None
          - fatal: bool            — stop dispatching to this account this cycle
          - session_expired: bool  — mark cookie_expired
          - network_error: bool    — skip cycle without penalising session
          - reason: str | None

        Broadcast-lead state transitions (persisted here):
          - success + more messages left  → status='sent', next_message_at=now+delay
          - success + last message        → status='sequence_complete', next_message_at=NULL
          - skipped (non-recoverable)     → status='skipped', skipped_reason=<reason>
          - error (recoverable)           → retry_count++; status='error' after 3 retries
        """
        result = {
            "success": False,
            "message_index": 0,
            "skipped": False,
            "skipped_reason": None,
            "fatal": False,
            "session_expired": False,
            "network_error": False,
            "reason": None,
        }

        # Determine which message to send (1-based). last_message_index is
        # the highest successfully sent so far; 0 means none.
        last_index = broadcast_lead.last_message_index or 0
        next_index = last_index + 1

        # Select the right message pool.
        # Prior-branch: message_prior_only replaces message_1 (different opener for
        # warm leads). message_prior_only_2/_3 override messages 2/3 if set; otherwise
        # fall back to message_2/3 so the sequence always continues to completion.
        if broadcast_lead.is_prior_branch:
            messages = [
                broadcast.message_prior_only,
                broadcast.message_prior_only_2 or broadcast.message_2,
                broadcast.message_prior_only_3 or broadcast.message_3,
            ]
        else:
            messages = [broadcast.message_1, broadcast.message_2, broadcast.message_3]
        available_messages = [m for m in messages if m]

        if next_index > len(available_messages):
            # Nothing left to send — mark complete and return. This is defensive:
            # the dispatcher's due-lead query should already filter these out.
            await self.repo.update_broadcast_lead(
                broadcast_lead,
                status="sequence_complete",
                next_message_at=None,
            )
            result["skipped"] = True
            result["skipped_reason"] = "sequence_already_complete"
            return result

        # Fetch the target Lead
        lead = await self.repo.get_lead_by_id(broadcast_lead.lead_id)
        if lead is None:
            # Lead was hard-deleted between snapshot and dispatch — mark skipped.
            await self.repo.update_broadcast_lead(
                broadcast_lead,
                status="skipped",
                skipped_reason="lead_deleted",
            )
            result["skipped"] = True
            result["skipped_reason"] = "lead_deleted"
            return result

        _dnc = await suppression.check_lead(self.repo.session, lead)
        if _dnc:
            await self.repo.update_broadcast_lead(
                broadcast_lead,
                status="skipped",
                skipped_reason=f"do_not_contact:{_dnc}",
            )
            result["skipped"] = True
            result["skipped_reason"] = f"do_not_contact:{_dnc}"
            return result

        if not lead.linkedin_url:
            await self.repo.update_broadcast_lead(
                broadcast_lead,
                status="skipped",
                skipped_reason="no_linkedin_url",
            )
            result["skipped"] = True
            result["skipped_reason"] = "no_linkedin_url"
            return result

        msg_template = available_messages[next_index - 1]
        rendered = render_template(msg_template, lead)

        # ── Pool mode vs ephemeral (same pattern as execute_followup_sequence) ──
        browser: Optional[LinkedInBrowser] = None
        if self._shared_context:
            page = await self._shared_context.new_page()
        else:
            browser = LinkedInBrowser()
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )
            if not await browser.validate_session():
                result["fatal"] = True
                result["session_expired"] = True
                result["reason"] = "session_validate_failed"
                await browser.close()
                return result
            page = await browser.new_page()

        try:
            actions = LinkedInActions(page)
            # Message 1: full conversation routing — classifies fresh/sent_only/has_reply.
            # Messages 2+: use "reply_only" routing — blocks only on has_reply (inbound
            # messages), allows sent_only (our own bubbles) to proceed. skip_prior_conversation_check
            # stays False so we wait for the thread to render and can detect replies.
            if next_index == 1:
                routing = broadcast.conversation_routing
                skip_check = False
                prior_only_msg = broadcast.message_prior_only
            else:
                routing = "reply_only"
                skip_check = False
                prior_only_msg = None
            action_result = await actions.send_message(
                lead.linkedin_url,
                rendered,
                skip_prior_conversation_check=skip_check,
                conversation_routing=routing,
                message_prior_only=prior_only_msg,
            )

            if action_result.status == ActionStatus.SUCCESS:
                now = datetime.utcnow()
                # Determine if this was the last message in the sequence.
                is_last = next_index >= len(available_messages)
                next_at = None
                new_status = "sequence_complete" if is_last else "sent"
                if not is_last:
                    from datetime import timedelta as _td
                    delay_h = broadcast.delay_between_hours
                    if delay_h == 0:
                        delay_secs = 5
                    else:
                        delay_secs = delay_h * 3600
                    next_at = now + _td(seconds=delay_secs)

                # Mark is_prior_branch on the lead row when branch routing swapped
                # in message_prior_only — subsequent messages must also follow that pool.
                is_prior = broadcast_lead.is_prior_branch or action_result.prior_branch_used

                await self.repo.update_broadcast_lead(
                    broadcast_lead,
                    status=new_status,
                    last_message_sent_at=now,
                    last_message_index=next_index,
                    next_message_at=next_at,
                    error_message=None,
                    is_prior_branch=is_prior,
                )
                await self.repo.log_action(
                    account_id=account.id,
                    action_type=ActionType.DIRECT_MESSAGE,
                    status=ActionLogStatus.SUCCESS,
                    lead_id=lead.id,
                    details={
                        "broadcast_id": broadcast.id,
                        "message_index": next_index,
                        "total_messages": len(available_messages),
                    },
                )
                await self.repo.increment_daily_stat(account.id, "direct_messages_sent")
                result["success"] = True
                result["message_index"] = next_index
                logger.info(
                    "broadcast.message_sent",
                    broadcast=broadcast.name,
                    url=lead.linkedin_url,
                    index=next_index,
                    total=len(available_messages),
                )

            elif action_result.status == ActionStatus.SKIPPED:
                reason = action_result.reason or "skipped"
                # "existing_conversation_replied" means branch-mode detected a
                # reply from the lead — route to manual_outreach, not skipped.
                bl_status = (
                    "manual_outreach"
                    if reason == "existing_conversation_replied"
                    else "skipped"
                )
                await self.repo.update_broadcast_lead(
                    broadcast_lead,
                    status=bl_status,
                    skipped_reason=reason,
                )
                await self.repo.log_action(
                    account_id=account.id,
                    action_type=ActionType.DIRECT_MESSAGE,
                    status=ActionLogStatus.SKIPPED,
                    lead_id=lead.id,
                    details={"broadcast_id": broadcast.id, "reason": reason, "message_index": next_index},
                )
                result["skipped"] = True
                result["skipped_reason"] = reason
                logger.info("broadcast.skipped", broadcast=broadcast.name, url=lead.linkedin_url, reason=reason, bl_status=bl_status)

            elif action_result.status == ActionStatus.SESSION_EXPIRED:
                # Do NOT update broadcast_lead — this lead will be retried.
                await self.repo.log_action(
                    account_id=account.id,
                    action_type=ActionType.DIRECT_MESSAGE,
                    status=ActionLogStatus.FAILED,
                    lead_id=lead.id,
                    details={"broadcast_id": broadcast.id, "reason": "session_expired", "message_index": next_index},
                )
                result["fatal"] = True
                result["session_expired"] = True
                result["reason"] = action_result.reason or "session_expired"

            else:
                # ActionStatus.ERROR (or unclassified). Common cases:
                #   - recipient_urn_not_found → not a 1st-degree connection.
                #     Treat as a permanent skip, not a retry.
                #   - profile_not_found       → same.
                #   - message_input_not_found → transient DOM issue, retry.
                #   - navigation timeout      → network_error.
                reason_str = (action_result.reason or "").lower()
                is_not_first_degree = "recipient_urn_not_found" in reason_str
                is_profile_missing = "profile_not_found" in reason_str

                if is_not_first_degree or is_profile_missing:
                    permanent_reason = (
                        "not_first_degree" if is_not_first_degree else "profile_not_found"
                    )
                    await self.repo.update_broadcast_lead(
                        broadcast_lead,
                        status="skipped",
                        skipped_reason=permanent_reason,
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        action_type=ActionType.DIRECT_MESSAGE,
                        status=ActionLogStatus.SKIPPED,
                        lead_id=lead.id,
                        details={"broadcast_id": broadcast.id, "reason": permanent_reason, "message_index": next_index},
                    )
                    result["skipped"] = True
                    result["skipped_reason"] = permanent_reason
                    logger.info(
                        "broadcast.permanent_skip",
                        broadcast=broadcast.name, url=lead.linkedin_url, reason=permanent_reason,
                    )
                else:
                    # Recoverable error — bump retry_count, mark ERROR after 3 attempts.
                    new_retry = (broadcast_lead.retry_count or 0) + 1
                    fields = {
                        "retry_count": new_retry,
                        "error_message": action_result.reason,
                    }
                    if new_retry >= 3:
                        fields["status"] = "error"
                    await self.repo.update_broadcast_lead(broadcast_lead, **fields)
                    await self.repo.log_action(
                        account_id=account.id,
                        action_type=ActionType.DIRECT_MESSAGE,
                        status=ActionLogStatus.FAILED,
                        lead_id=lead.id,
                        details={
                            "broadcast_id": broadcast.id,
                            "reason": action_result.reason,
                            "message_index": next_index,
                            "retry_count": new_retry,
                        },
                    )
                    result["reason"] = action_result.reason
                    if _is_network_error(action_result.reason or ""):
                        result["network_error"] = True
                    logger.warning(
                        "broadcast.error",
                        broadcast=broadcast.name,
                        url=lead.linkedin_url,
                        reason=action_result.reason,
                        retry_count=new_retry,
                    )

        except Exception as e:
            err_str = str(e)
            logger.error("broadcast.execute_failed", broadcast=broadcast.name, url=lead.linkedin_url, error=err_str)
            result["reason"] = err_str
            if _is_session_expired_signal(err_str):
                result["fatal"] = True
                result["session_expired"] = True
            elif _is_network_error(err_str):
                result["network_error"] = True
            else:
                # Log an error to action_log so the lead detail page shows it.
                try:
                    await self.repo.log_action(
                        account_id=account.id,
                        action_type=ActionType.DIRECT_MESSAGE,
                        status=ActionLogStatus.FAILED,
                        lead_id=broadcast_lead.lead_id,
                        details={"broadcast_id": broadcast.id, "reason": err_str, "exception": True},
                    )
                except Exception:
                    pass
                result["fatal"] = True
        finally:
            try:
                await page.goto("about:blank", wait_until="domcontentloaded", timeout=2000)
            except Exception:
                pass
            try:
                await page.close()
            except Exception:
                pass
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

        return result

    async def execute_batch(
        self,
        account: Account,
        campaign: Campaign,
        leads: Sequence[Lead],
    ) -> dict:
        """
        Execute connection requests for a batch of leads.
        Returns summary stats.
        """
        stats = {"success": 0, "skipped": 0, "error": 0, "limit_reached": False}

        browser = LinkedInBrowser()
        try:
            context = await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )

            # Validate session first
            is_valid = await browser.validate_session()
            if not is_valid:
                logger.error("executor.session_invalid", account=account.name)
                await self.repo.update_account(account, status="cookie_expired")
                return stats

            page = await browser.new_page()
            actions = LinkedInActions(page)

            # Build profile filters from campaign config
            filters = ProfileFilters(
                no_photo=campaign.filter_no_photo,
                min_connections=campaign.filter_min_connections,
                exclude_open_to_work=campaign.filter_exclude_open_to_work,
            )

            for i, lead in enumerate(leads):
                logger.info(
                    "executor.processing_lead",
                    lead=lead.linkedin_url,
                    index=i + 1,
                    total=len(leads),
                )

                # Render personalized message
                message = None
                if campaign.connection_message_template:
                    message = render_template(campaign.connection_message_template, lead)

                # Execute the action
                result = await actions.send_connection_request(lead.linkedin_url, message, filters=filters)

                # Handle result
                if result.status == ActionStatus.SUCCESS:
                    validate_transition(lead.status, LeadStatus.CONNECTION_REQUESTED)
                    await self.repo.update_lead(
                        lead,
                        campaign_id_override=campaign.id,
                        status=LeadStatus.CONNECTION_REQUESTED,
                        connection_requested_at=datetime.utcnow(),
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST,
                        status=ActionLogStatus.SUCCESS,
                    )
                    await self.repo.increment_daily_stat(
                        account.id, "connection_requests_sent"
                    )
                    stats["success"] += 1
                    logger.info("executor.request_sent", url=lead.linkedin_url)

                elif result.status == ActionStatus.SKIPPED:
                    validate_transition(lead.status, LeadStatus.SKIPPED)
                    await self.repo.update_lead(
                        lead,
                        campaign_id_override=campaign.id,
                        status=LeadStatus.SKIPPED,
                        error_message=result.reason,
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST,
                        status=ActionLogStatus.SKIPPED,
                        details={"reason": result.reason},
                    )
                    stats["skipped"] += 1
                    logger.info("executor.skipped", url=lead.linkedin_url, reason=result.reason)

                elif result.status == ActionStatus.LIMIT_REACHED:
                    # Stop processing — weekly limit hit
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.LIMIT_DETECTED,
                        status=ActionLogStatus.FAILED,
                        details=result.details,
                    )
                    stats["limit_reached"] = True
                    logger.warning("executor.limit_reached", url=lead.linkedin_url)
                    break

                elif result.status in (ActionStatus.CAPTCHA, ActionStatus.SESSION_EXPIRED):
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": result.status.value},
                    )
                    logger.error("executor.critical_error", status=result.status.value)
                    break

                else:  # ERROR
                    new_retry = lead.retry_count + 1
                    new_status = LeadStatus.ERROR
                    validate_transition(lead.status, new_status)
                    await self.repo.update_lead(
                        lead,
                        campaign_id_override=campaign.id,
                        status=new_status,
                        retry_count=new_retry,
                        error_message=result.reason,
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": result.reason, **result.details},
                    )
                    await self.repo.increment_daily_stat(account.id, "errors")
                    stats["error"] += 1
                    logger.error("executor.error", url=lead.linkedin_url, reason=result.reason)

                # Wait between actions (skip delay after last lead)
                if i < len(leads) - 1 and not stats["limit_reached"]:
                    await self.delay.action_delay()

            await page.close()

        finally:
            await browser.close()

        return stats
