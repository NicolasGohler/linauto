"""Merge LinkedIn Contact Info (harvested after acceptance) into a lead without data loss.

The imported email (usually the Apollo work email) is never overwritten: the
email a person shows to their connections is often personal, so it is kept in
extra_data["contact_info_email"] and only fills `email` when that was empty.
Missing values never blank existing ones.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def contact_info_updates(lead: Any, info: dict, now: Optional[datetime] = None) -> dict:
    """Return the update_lead kwargs for harvested contact info ({} if nothing to save)."""
    email = (info.get("email") or "").strip() or None
    phone = (info.get("phone") or "").strip() or None
    if not email and not phone:
        return {}

    extra = dict(lead.extra_data or {})  # new dict so SQLAlchemy sees the JSON change
    if email:
        extra["contact_info_email"] = email
    if phone:
        extra["contact_info_phone"] = phone
    extra["contact_info_harvested_at"] = (now or datetime.utcnow()).isoformat(timespec="seconds")

    updates: dict = {"extra_data": extra}
    if email and not (lead.email or "").strip():
        updates["email"] = email
    if phone and not (getattr(lead, "phone", None) or "").strip():
        updates["phone"] = phone
    return updates
