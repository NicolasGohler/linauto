"""
Async X (Twitter) handle resolver, built on twitterapi.io (read-only).

Mirrors telegram/resolver.py, with a different engine underneath. A handle
merely existing on X proves nothing, so every candidate is scored against the
person's profile evidence: display name, bio, profile website and handle.

Passes, cheapest first (stops early on a confident match). Order and patterns
come from 134 known handles in the DB (Nov 2026): LinkedIn slug 30%,
firstlast/first_last 27%, flast 7%, lastfirst 3%, company in handle only 4%,
and 54% not guessable at all (only a display-name search finds those).
  Pass 1 — LinkedIn vanity slug as a handle                     (~15-18 credits)
  Pass 2 — name handle patterns: firstlast, first_last, flast,
           lastfirst (case-insensitive, no company patterns)    (~15-18 each)
  Pass 3 — X user search on "First Last"; full profile lookup only
           for results matching first AND last name (max 3)     (~360 + 18 each)
  Pass 4 — X user search on "First Company", only when Pass 3
           returned no full-name match ("Ana | Acme" display names)

Scoring:
  name      first + last in display name = 3, first only = 1, neither = reject
            (unless the profile website matches the company domain)
  website   profile URL on the company domain                 +3
  bio       company domain in bio                             +3
            company name / shorthand in bio                   +2
  handle    company shorthand in handle                       +1
  role      founder/CEO in bio and in the lead's title        +0.5

A match is "confident" at score >= CONFIDENT_SCORE (name plus company evidence).
Only confident matches become best_match; everything else that matched the
name is returned in alternatives with its score so it can be reviewed in the UI.

Never writes to X: no follows, likes, posts or DMs.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import structlog

from releasi.telegram.resolver import (
    extract_linkedin_slug,
    get_company_shorthands,
)

log = structlog.get_logger(__name__)

API_BASE = "https://api.twitterapi.io/twitter"
CONFIDENT_SCORE = 5.0
MIN_ALT_SCORE = 3.0          # keep alternatives only when the full name matches
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

_FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.de", "web.de", "mail.com", "yandex.com", "hey.com",
}
_ROLE_WORDS = ("founder", "co-founder", "cofounder", "ceo", "cto", "coo", "owner")
_LINKEDIN_RESERVED = {"in", "pub", "company", "school", "feed", "jobs"}


@dataclass
class XFindResult:
    best_match: Optional[str] = None                       # bare handle, no @
    best_score: Optional[float] = None
    alternatives: list[dict] = field(default_factory=list)  # [{username, score, name, bio}]
    logs: list[str] = field(default_factory=list)
    api_calls: int = 0
    rate_limited: bool = False
    best_profile: dict = field(default_factory=dict)        # {name, bio, can_dm} of best_match


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_x_username(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"(?:twitter\.com|x\.com)/(@?\w+)", url.strip(), re.IGNORECASE)
    if not m:
        return None
    handle = m.group(1).lstrip("@")
    if handle.lower() in ("home", "explore", "search", "settings", "i", "intent", "share"):
        return None
    return handle


def _fold(text: Optional[str]) -> str:
    """Lowercase and strip accents so 'Müller' matches 'muller'."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(text))


def company_domain_from(email: Optional[str], extra: Optional[dict]) -> Optional[str]:
    """Best-effort company domain: work email domain, else a website-like extra field."""
    if email and "@" in email:
        dom = email.split("@", 1)[1].strip().lower()
        if dom and dom not in _FREE_EMAIL_DOMAINS:
            return dom
    for key, val in (extra or {}).items():
        if not isinstance(val, str) or not val:
            continue
        if any(k in key.lower() for k in ("website", "domain", "company_url", "site")):
            m = re.search(r"(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", val.lower())
            if m and "linkedin.com" not in m.group(1) and "x.com" not in m.group(1):
                return m.group(1)
    return None


def linkedin_slug_x_candidate(linkedin_url: Optional[str]) -> Optional[str]:
    """Custom LinkedIn vanity slugs are often reused as X handles."""
    slug = extract_linkedin_slug(linkedin_url)
    if not slug or slug.lower() in _LINKEDIN_RESERVED:
        return None
    segments = slug.lower().split("-")
    if len(segments) > 1 and any(ch.isdigit() for ch in segments[-1]):
        return None  # auto-generated first-last-<id>
    core = re.sub(r"[^a-z0-9_]", "", slug.lower().replace("-", ""))
    return core if _HANDLE_RE.match(core or "") else None


def x_name_candidates(name: str) -> list[str]:
    """Name-only handle patterns, in order of observed frequency on X.

    No company patterns: only 4% of known handles contain the company and
    those people are found by the name search anyway.
    """
    parts = (name or "").strip().split()
    if len(parts) < 2:
        return []
    f, l = _alnum(parts[0]), _alnum(parts[-1])
    if not f or not l:
        return []
    out: list[str] = []
    for c in (f + l, f"{f}_{l}", f[0] + l, l + f):
        if _HANDLE_RE.match(c) and c not in out:
            out.append(c)
    return out


def _can_dm(d: dict) -> Optional[bool]:
    """X's open-DM flag as reported by twitterapi.io; None when the payload lacks it."""
    for k in ("canDm", "can_dm"):
        if isinstance(d.get(k), bool):
            return d[k]
    return None


def _profile_from_info(data: dict) -> dict:
    urls = []
    ent = data.get("entities") or {}
    for part in ("url", "description"):
        for u in (ent.get(part) or {}).get("urls") or []:
            if u.get("expanded_url"):
                urls.append(u["expanded_url"])
    return {
        "username": data.get("userName") or data.get("screen_name"),
        "name": data.get("name") or "",
        "bio": data.get("description") or "",
        "urls": urls,
        "location": data.get("location") or "",
        "followers": data.get("followers") or data.get("followers_count") or 0,
        "can_dm": _can_dm(data),
    }


def _profile_from_search(u: dict) -> dict:
    return {
        "username": u.get("screen_name") or u.get("username"),
        "name": u.get("name") or "",
        "bio": u.get("description") or "",
        "urls": [u["url"]] if u.get("url") else [],
        "location": u.get("location") or "",
        "followers": u.get("followers_count") or 0,
        "can_dm": _can_dm(u),
    }


def score_profile(
    profile: dict,
    first: str,
    last: str,
    company: Optional[str],
    shorthands: list[str],
    domain: Optional[str],
    title: Optional[str],
) -> Optional[float]:
    """Score how well an X profile matches the lead. None = reject."""
    disp = _fold(profile.get("name"))
    bio = _fold(profile.get("bio"))
    handle = _fold(profile.get("username"))
    urls = " ".join(_fold(u) for u in profile.get("urls") or [])

    f, l = _fold(first), _fold(last)
    first_hit = bool(f) and (f in disp or f in handle)
    last_hit = bool(l) and len(l) > 1 and (l in disp or l in handle)

    site_hit = bool(domain) and domain in urls
    if not first_hit and not site_hit:
        return None

    score = 3.0 if (first_hit and last_hit) else (1.0 if first_hit else 2.0)
    if site_hit:
        score += 3.0
    if domain and domain in bio:
        score += 3.0
    company_tokens = [t for t in ([_alnum(company)] if company else []) + shorthands if len(t) >= 3]
    bio_alnum = _alnum(profile.get("bio") or "")
    if any(t in bio_alnum for t in company_tokens):
        score += 2.0
    if any(t in handle for t in shorthands if len(t) >= 3):
        score += 1.0
    t = _fold(title)
    if any(w in bio for w in _ROLE_WORDS) and any(w in t for w in _ROLE_WORDS):
        score += 0.5
    return score


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class _Api:
    def __init__(self, client: httpx.AsyncClient, api_key: str, result: XFindResult):
        self.client = client
        self.key = api_key
        self.result = result

    async def _get(self, path: str, params: dict) -> Optional[dict]:
        self.result.api_calls += 1
        r = await self.client.get(f"{API_BASE}/{path}", params=params, headers={"X-API-Key": self.key})
        if r.status_code == 429:
            self.result.rate_limited = True
            return None
        if r.status_code >= 400:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def user_info(self, handle: str) -> Optional[dict]:
        d = await self._get("user/info", {"userName": handle})
        if not d or d.get("status") != "success" or not d.get("data"):
            return None
        return _profile_from_info(d["data"])

    async def user_search(self, query: str) -> list[dict]:
        d = await self._get("user/search", {"query": query})
        return [_profile_from_search(u) for u in (d or {}).get("users") or []]


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

async def find_x(
    name: str,
    api_key: str,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    email: Optional[str] = None,
    title: Optional[str] = None,
    extra_data: Optional[dict] = None,
    exclude_usernames: Optional[list[str]] = None,
    max_enrich: int = 3,
    sleep_between: float = 0.2,
) -> XFindResult:
    """Resolve the most likely personal X handle for a person."""
    result = XFindResult()
    parts = (name or "").strip().split()
    if len(parts) < 2:
        result.logs.append("Need first and last name to search X")
        return result
    first, last = parts[0], parts[-1]
    shorthands = get_company_shorthands(company)
    domain = company_domain_from(email, extra_data)
    exclude = {u.lower().lstrip("@") for u in (exclude_usernames or [])}
    result.logs.append(
        f"Searching X for {name}"
        + (f" · company={company}" if company else "")
        + (f" · domain={domain}" if domain else "")
    )

    scored: dict[str, tuple[float, dict]] = {}
    full_name_hit = False

    def consider(profile: Optional[dict], source: str) -> Optional[float]:
        if not profile or not profile.get("username"):
            return None
        key = profile["username"].lower()
        if key in exclude:
            return None
        s = score_profile(profile, first, last, company, shorthands, domain, title)
        if s is None:
            return None
        prev = scored.get(key)
        if not prev or s > prev[0]:
            scored[key] = (s, profile)
        result.logs.append(f"  ✓ @{profile['username']} ({profile.get('name')}) score={s:.1f} via {source}")
        return s

    async with httpx.AsyncClient(timeout=20) as client:
        api = _Api(client, api_key, result)

        tried: set[str] = set(exclude)

        def confident() -> bool:
            return bool(scored) and max(v[0] for v in scored.values()) >= CONFIDENT_SCORE

        async def lookup(handle: str, source: str) -> None:
            key = handle.lower()
            if key in tried or result.rate_limited:
                return
            tried.add(key)
            if result.api_calls:
                await asyncio.sleep(sleep_between)
            consider(await api.user_info(handle), source)

        # Pass 1 — LinkedIn vanity slug
        slug = linkedin_slug_x_candidate(linkedin_url)
        if slug:
            result.logs.append(f"[Pass 1] LinkedIn slug → @{slug}")
            await lookup(slug, "linkedin slug")
            if confident():
                return _finish(result, scored)

        # Pass 2 — name handle patterns
        patterns = [c for c in x_name_candidates(name) if c.lower() not in tried]
        if patterns:
            result.logs.append(f"[Pass 2] Handle patterns: {', '.join(patterns)}")
        for c in patterns:
            await lookup(c, f"pattern '{c}'")
            if confident():
                return _finish(result, scored)

        # Pass 3 — name search; Pass 4 — company search only if no full-name hit
        queries = [("Pass 3", f"{first} {last}")]
        if shorthands:
            queries.append(("Pass 4", f"{first} {shorthands[0]}"))
        for label, query in queries:
            if result.rate_limited:
                break
            if label == "Pass 4" and full_name_hit:
                break
            result.logs.append(f"[{label}] X user search: \"{query}\"")
            await asyncio.sleep(sleep_between)
            matches = []
            for p in await api.user_search(query):
                sc = consider(p, "search")
                if sc is not None and sc >= MIN_ALT_SCORE:  # first AND last name matched
                    matches.append((sc, p))
                    full_name_hit = True
            matches.sort(key=lambda x: -x[0])
            for _, p in matches[:max_enrich]:
                await lookup(p["username"], "profile")
            if confident():
                return _finish(result, scored)

    return _finish(result, scored)


def _finish(result: XFindResult, scored: dict[str, tuple[float, dict]]) -> XFindResult:
    ranked = sorted(scored.values(), key=lambda x: -x[0])
    if ranked and ranked[0][0] >= CONFIDENT_SCORE:
        best_s, best_p = ranked[0]
        result.best_match = best_p["username"]
        result.best_score = best_s
        result.best_profile = {
            "name": best_p.get("name") or "",
            "bio": (best_p.get("bio") or "")[:160],
            "can_dm": best_p.get("can_dm"),
            "followers": best_p.get("followers"),
        }
        ranked = ranked[1:]
        result.logs.append(f"Best match: @{result.best_match} (score={best_s:.1f})")
    else:
        result.logs.append("No confident match" + (f", {len(ranked)} possible" if ranked else ""))
    result.alternatives = [
        {
            "username": p["username"],
            "score": round(s, 1),
            "name": p.get("name") or "",
            "bio": (p.get("bio") or "")[:160],
            "can_dm": p.get("can_dm"),
            "followers": p.get("followers"),
        }
        for s, p in ranked
        if s >= MIN_ALT_SCORE
    ][:8]
    result.logs.append(f"{result.api_calls} twitterapi.io calls")
    return result
