"""Company-level do-not-contact list.

People who already replied (handled by hand) and their colleagues must never get
automated LinkedIn messages. Rules live in the ``do_not_contact`` table, which is
created on first use so no image rebuild or alembic step is needed.

Rule kinds:
  company       normalised company name (lowercase, alphanumerics only)
  domain        email domain; subdomains match too
  linkedin_url  normalised profile URL, for individuals

A lead is suppressed if ANY rule matches. Matching fails open on missing data
(a lead with no company and no email can only be matched by URL).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Set

import structlog
from sqlalchemy import text

logger = structlog.get_logger()

_CREATE = """
CREATE TABLE IF NOT EXISTS do_not_contact (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    label TEXT,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (kind, value)
)
"""


def norm_company(name: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def norm_domain(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    return v.split("/")[0]


def norm_url(url: Optional[str]) -> str:
    u = (url or "").strip().lower().split("?")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u)


@dataclass
class Rules:
    companies: Set[str] = field(default_factory=set)
    domains: Set[str] = field(default_factory=set)
    urls: Set[str] = field(default_factory=set)

    def match(self, lead) -> Optional[str]:
        """Return a short description of the matching rule, or None."""
        company = norm_company(getattr(lead, "company", None))
        if company and company in self.companies:
            return f"company:{company}"
        domain = norm_domain(getattr(lead, "email", None))
        if domain:
            for d in self.domains:
                if domain == d or domain.endswith("." + d):
                    return f"domain:{d}"
        url = norm_url(getattr(lead, "linkedin_url", None))
        if url and url in self.urls:
            return f"url:{url}"
        return None


async def load_rules(session) -> Rules:
    await session.execute(text(_CREATE))
    rows = (await session.execute(text("SELECT kind, value FROM do_not_contact"))).all()
    rules = Rules()
    for kind, value in rows:
        if kind == "company":
            rules.companies.add(norm_company(value))
        elif kind == "domain":
            rules.domains.add(norm_domain(value))
        elif kind == "linkedin_url":
            rules.urls.add(norm_url(value))
    return rules


async def check_lead(session, lead) -> Optional[str]:
    """Return the matching rule for ``lead`` or None. Never raises: on any error
    the lead is treated as not suppressed so a DB hiccup cannot stall dispatch."""
    try:
        return (await load_rules(session)).match(lead)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("suppression.check_failed", error=str(e))
        return None


async def add_rule(session, kind: str, value: str, label: str = "", reason: str = "") -> None:
    await session.execute(text(_CREATE))
    norm = {"company": norm_company, "domain": norm_domain, "linkedin_url": norm_url}[kind](value)
    await session.execute(
        text("INSERT OR IGNORE INTO do_not_contact (kind, value, label, reason) VALUES (:k, :v, :l, :r)"),
        {"k": kind, "v": norm, "l": label or value, "r": reason},
    )
    await session.commit()
