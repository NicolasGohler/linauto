"""Conservative withdrawal limits and parsing, independent of browser actions."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

MAX_WITHDRAWALS = 100
WITHDRAWAL_JOB_TIMEOUT = 30 * 60
WITHDRAWAL_STEP_TIMEOUT = 30


class WithdrawalStopped(RuntimeError):
    """Safe user-facing reason; never includes browser traces or credentials."""

    def __init__(self, reason, *, account_action=None):
        super().__init__(reason)
        self.account_action = account_action


def profile_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname != "www.linkedin.com":
        return None
    match = re.fullmatch(r"/in/([\w-]+)/?", parsed.path)
    if not match:
        return None
    return "https://www.linkedin.com/in/" + match.group(1).lower()


def response_records(body):
    """Decode JSON or individual React Flight JSON records, never eval code."""
    if not isinstance(body, str):
        return []
    records = []
    for line in body.splitlines():
        candidates = [line]
        if ":" in line:
            candidates.append(line.split(":", 1)[1])
        for candidate in candidates:
            try:
                records.append(json.loads(candidate))
                break
            except (ValueError, TypeError):
                pass
    return records


def _objects(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value)


def _identities(node):
    ids, urls = set(), set()
    if isinstance(node, str):
        ids.update(re.findall(r"InvitationUrn\(invitationId=(\d+)\)", node))
        url = profile_url(node)
        if url:
            urls.add(url)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "invitationId" and str(value).isdigit():
                ids.add(str(value))
            child_ids, child_urls = _identities(value)
            ids.update(child_ids)
            urls.update(child_urls)
    elif isinstance(node, list):
        for value in node:
            child_ids, child_urls = _identities(value)
            ids.update(child_ids)
            urls.update(child_urls)
    return ids, urls


def invitation_pairs(body):
    """Only accept IDs and URLs co-located in an unambiguous card object."""
    if not isinstance(body, str) or len(body) > 512_000:
        raise WithdrawalStopped("LinkedIn returned an unexpected invitation response; withdrawal stopped.")
    records = response_records(body)
    ids = list(dict.fromkeys(re.findall(r"InvitationUrn\(invitationId=(\d+)\)", body)))
    pairs = {}
    for record in records:
        for obj in _objects(record):
            if obj.get("error") or obj.get("success") is False:
                raise WithdrawalStopped("LinkedIn reported a pagination error; withdrawal stopped.")
            obj_ids, urls = _identities(obj)
            if len(obj_ids) == len(urls) == 1:
                inv_id, url = next(iter(obj_ids)), next(iter(urls))
                if inv_id in pairs and pairs[inv_id] != url:
                    raise WithdrawalStopped("Ambiguous invitation identity; nothing further was withdrawn.")
                pairs[inv_id] = url
    if not ids or any(inv_id not in pairs for inv_id in ids):
        raise WithdrawalStopped("LinkedIn invitation format could not be verified; no positional matching or scrolling was attempted.")
    if len(set(pairs[inv_id] for inv_id in ids)) != len(ids):
        raise WithdrawalStopped("Duplicate invitation identities; withdrawal stopped.")
    return [(inv_id, pairs[inv_id]) for inv_id in ids]


def withdrawal_receipt(body, invitation_id):
    """Require an explicit, identified acknowledgement, not merely HTTP 200."""
    if not isinstance(body, str) or len(body) > 512_000:
        return False
    records = response_records(body)
    acknowledged = False
    for record in records:
        for obj in _objects(record):
            if obj.get("error") or obj.get("success") is False:
                return False
            ids, _ = _identities(obj)
            if ids == {str(invitation_id)} and (
                obj.get("success") is True
                or str(obj.get("status", "")).lower() == "withdrawn"
            ):
                acknowledged = True
    return acknowledged
