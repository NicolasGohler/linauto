"""Profile signal parsing (pure) and the capture guard."""
from __future__ import annotations

import asyncio

from releasi.linkedin import profile_signals as ps


def test_mutual_counts():
    assert ps._mutual_count("Sam Lee is a mutual connection") == 1
    assert ps._mutual_count("Sam Lee and Ana Ruiz are mutual connections") == 2
    assert ps._mutual_count("Sam Lee, Ana Ruiz and 12 other mutual connections") == 14
    assert ps._mutual_count("5 mutual connections") == 5
    assert ps._mutual_count(None) is None


def test_summarize_ages_counts_tenure():
    out = ps.summarize({
        "degree": "2nd", "mutual_text": "Sam and 3 other mutual connections",
        "connections": "500", "connections_plus": True, "followers": "1.2K",
        "activity_section": True, "activity_ages": ["3w", "5d", "2mo"],
        "current_role_tenure": "2 yrs 3 mos", "sections": ["About", "Activity"],
    })
    assert out["degree"] == "2nd"
    assert out["mutual_connections"] == 4
    assert out["connections"] == 500 and out["connections_plus"] is True
    assert out["followers"] == 1200
    assert out["activity_newest_days"] == 5 and out["activity_items_seen"] == 3
    assert out["current_role_months"] == 27


def test_summarize_unknowns_stay_none():
    out = ps.summarize({})
    assert out["degree"] is None and out["activity_newest_days"] is None
    assert out["connections"] is None and out["current_role_months"] is None


class _Page:
    def __init__(self, url, raw=None, boom=False):
        self.url, self.raw, self.boom, self.calls = url, raw, boom, 0

    async def evaluate(self, js):
        self.calls += 1
        if self.boom:
            raise RuntimeError("detached")
        return self.raw


def test_capture_skips_when_not_on_lead_profile():
    page = _Page("https://www.linkedin.com/in/someone-else/")
    assert asyncio.run(ps.capture(page, "https://www.linkedin.com/in/sam")) is None
    assert page.calls == 0


def test_capture_matches_encoded_slug_and_never_raises():
    page = _Page("https://www.linkedin.com/in/j%C3%B6rg/", raw={"degree": "3rd"})
    assert asyncio.run(ps.capture(page, "https://linkedin.com/in/jörg"))["degree"] == "3rd"
    assert asyncio.run(ps.capture(_Page("https://www.linkedin.com/in/sam", boom=True),
                                  "https://linkedin.com/in/sam")) is None
