"""Read-only profile signals for acceptance analysis.

Reads what is already rendered on a profile page we loaded for a connection
request. It never navigates, scrolls, clicks, hovers or waits for anything, so
LinkedIn sees no extra activity: the only cost is one DOM read in the page.
Anything it can't find is recorded as None (unknown), never guessed.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import unquote

import structlog
from playwright.async_api import Page

logger = structlog.get_logger()

SIGNALS_VERSION = 1

_JS = r"""
() => {
  const txt = el => ((el && (el.innerText || el.textContent)) || '').trim();
  const out = {};
  const main = document.querySelector('main') || document.body;

  // Section headings seen, so "missing" can be told apart from "not loaded".
  const sections = {};
  for (const sec of main.querySelectorAll('section')) {
    const h = sec.querySelector('h2');
    const name = txt(h).split('\n')[0].trim();
    if (name && name.length < 40 && !(name in sections)) sections[name] = sec;
  }
  out.sections = Object.keys(sections).slice(0, 25);

  // Degree: first standalone "1st" / "2nd" / "3rd" / "3rd+" label near the top.
  out.degree = null;
  for (const el of main.querySelectorAll('span, div, p')) {
    if (el.children.length > 2) continue;
    const m = txt(el).match(/^[·•\s]*(1st|2nd|3rd\+?)[·•\s]*$/i);
    if (m) { out.degree = m[1].toLowerCase(); break; }
  }

  // Connection / follower counts and the mutual-connections line.
  out.connections = null; out.connections_plus = null;
  out.followers = null; out.mutual_text = null;
  for (const el of main.querySelectorAll('a, span, p, li, div')) {
    if (el.children.length > 3) continue;
    const t = txt(el);
    if (!t || t.length > 200) continue;
    if (out.connections === null && !/mutual/i.test(t)) {
      const m = t.match(/^([\d,]+)(\+?)\s*connections?$/i);
      if (m) { out.connections = m[1]; out.connections_plus = m[2] === '+'; }
    }
    if (out.followers === null) {
      const m = t.match(/^([\d.,]+\s*[KkMm]?)\s+followers?$/i);
      if (m) out.followers = m[1];
    }
    if (out.mutual_text === null && /mutual connection/i.test(t)) out.mutual_text = t;
  }

  // Activity: newest timestamp label ("3d", "2w", "5mo", "1yr") in the Activity card.
  const act = sections['Activity'];
  out.activity_section = !!act;
  out.activity_ages = [];
  out.activity_empty_text = false;
  if (act) {
    const at = txt(act);
    out.activity_empty_text = /hasn.t posted|no recent activity|hasn.t (been active|shared)/i.test(at);
    const re = /(?:^|\s)(\d{1,3})\s?(mo|yr|w|d|h|m)\b\s*[·•]/g;
    let m; let n = 0;
    while ((m = re.exec(at)) && n < 20) { out.activity_ages.push(m[1] + m[2]); n++; }
  }

  out.about_section = 'About' in sections;
  out.featured_section = 'Featured' in sections;

  // Tenure of the first (current) experience entry, e.g. "2 yrs 3 mos".
  out.current_role_tenure = null;
  const exp = sections['Experience'];
  if (exp) {
    const first = exp.querySelector('li');
    const m = txt(first).match(/(\d+\s+yrs?(?:\s+\d+\s+mos?)?|\d+\s+mos?|less than a year)/i);
    if (m) out.current_role_tenure = m[1];
  }
  return out;
}
"""

_AGE_UNITS = {"m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 30, "yr": 365}


def _age_days(label: str) -> Optional[float]:
    m = re.match(r"^(\d+)(mo|yr|w|d|h|m)$", label)
    return int(m.group(1)) * _AGE_UNITS[m.group(2)] if m else None


def _parse_count(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    s = raw.replace(",", "").strip().lower()
    try:
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        if s.endswith("m"):
            return int(float(s[:-1]) * 1_000_000)
        return int(float(s))
    except ValueError:
        return None


def _mutual_count(text: Optional[str]) -> Optional[int]:
    """'A is a mutual connection' -> 1, 'A and B are ...' -> 2, 'A, B and 12 other ...' -> 14."""
    if not text:
        return None
    m = re.search(r"and\s+([\d,]+)\s+other\s+mutual", text, re.I)
    if m:
        head = text[: m.start()]
        names = len([p for p in head.split(",") if p.strip()])
        return int(m.group(1).replace(",", "")) + max(names, 1)
    if re.search(r"\band\b.+\bare mutual connections\b", text, re.I):
        return 2
    if re.search(r"\bis a mutual connection\b", text, re.I):
        return 1
    m = re.search(r"([\d,]+)\s+mutual connections?", text, re.I)
    return int(m.group(1).replace(",", "")) if m else None


def _tenure_months(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    if "less than" in raw.lower():
        return 0
    yrs = re.search(r"(\d+)\s+yr", raw)
    mos = re.search(r"(\d+)\s+mo", raw)
    return (int(yrs.group(1)) * 12 if yrs else 0) + (int(mos.group(1)) if mos else 0)


def summarize(raw: dict) -> dict:
    """Turn the raw DOM read into stored signals. Pure, unit-testable."""
    ages = [a for a in (_age_days(x) for x in raw.get("activity_ages") or []) if a is not None]
    return {
        "v": SIGNALS_VERSION,
        "degree": raw.get("degree"),
        "mutual_connections": _mutual_count(raw.get("mutual_text")),
        "connections": _parse_count(raw.get("connections")),
        "connections_plus": raw.get("connections_plus"),
        "followers": _parse_count(raw.get("followers")),
        "activity_section": raw.get("activity_section"),
        "activity_newest_days": round(min(ages), 2) if ages else None,
        "activity_items_seen": len(ages),
        "activity_empty_text": raw.get("activity_empty_text"),
        "about_section": raw.get("about_section"),
        "featured_section": raw.get("featured_section"),
        "current_role_months": _tenure_months(raw.get("current_role_tenure")),
        "sections": raw.get("sections") or [],
    }


def _slug(url: str) -> str:
    m = re.search(r"linkedin\.com/in/([^/?#]+)", unquote(url or ""), re.I)
    return m.group(1).lower().rstrip("/") if m else ""


async def capture(page: Page, profile_url: str, timeout: float = 3.0) -> Optional[dict]:
    """Read signals if the page is still on this lead's profile; None otherwise. Never raises."""
    try:
        want = _slug(profile_url)
        if not want or _slug(page.url) != want:
            return None
        raw = await asyncio.wait_for(page.evaluate(_JS), timeout=timeout)
        return summarize(raw or {})
    except Exception as e:  # observation only: must never affect the invite flow
        logger.debug("profile_signals.capture_failed", error=str(e)[:200])
        return None
