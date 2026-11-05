"""Stateless connection-request pacing.

Everything here is a pure function of (now, account timezone, requests sent
today, time of last send). Nothing is cached between ticks, so a container
restart or a timezone change cannot lose or mis-place the day's progress.

Model: a linear target curve over the work window. Each dispatcher tick asks
"how many requests should be out by now, and how many are?". If the account is
behind by at least one minimum batch, a session runs. If the window is
running out, a session runs regardless (urgent), so the daily limit is still
reachable. Restart mid-day simply shows up as a bigger deficit and is caught up
with back-to-back sessions separated by a minimum gap.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as _tz
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

# Minimum idle time between the last send of one session and the start of the next.
MIN_SESSION_GAP_SEC = 20 * 60
# Rough wall-clock cost of one session (browser warm-up, 6-10 sends, noise).
EST_SESSION_SEC = 8 * 60
# Dispatcher tick interval, used when asking "can I still finish if I wait one tick?".
TICK_SEC = 5 * 60


@dataclass(frozen=True)
class PacingDecision:
    run: bool
    batch_size: int
    reason: str
    target_now: float
    deficit: float
    urgent: bool = False


def work_window(
    account_id: str,
    local_date: date,
    tz_name: Optional[str],
    start_hour: int,
    end_hour: int,
    start_variation: Tuple[int, int],
    end_variation: Tuple[int, int],
) -> Tuple[datetime, datetime]:
    """Return (work_start, work_end) as naive UTC for the account-local date.

    The daily jitter is seeded from account id + local date, so every call on
    the same day returns the same window without storing it anywhere.
    """
    seed = int(hashlib.sha256(f"{account_id}-{local_date.isoformat()}-window".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    start_off = rng.randint(start_variation[0], start_variation[1])
    end_off = rng.randint(end_variation[0], end_variation[1])
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    base_start = datetime(local_date.year, local_date.month, local_date.day, start_hour, 0, tzinfo=tz)
    base_end = datetime(local_date.year, local_date.month, local_date.day, end_hour, 0, tzinfo=tz)
    to_utc = lambda d: d.astimezone(_tz.utc).replace(tzinfo=None)  # noqa: E731
    return (
        to_utc(base_start) + timedelta(minutes=start_off),
        to_utc(base_end) + timedelta(minutes=end_off),
    )


def decide(
    now: datetime,
    window: Tuple[datetime, datetime],
    sent_today: int,
    daily_limit: int,
    last_send_at: Optional[datetime],
    batch_range: Tuple[int, int],
    rng: Optional[random.Random] = None,
    min_gap_sec: float = MIN_SESSION_GAP_SEC,
) -> PacingDecision:
    """Decide whether to run a session now and how large it should be."""
    rng = rng or random
    lo, hi = max(1, batch_range[0]), max(batch_range[0], batch_range[1], 1)
    work_start, work_end = window
    window_sec = max(1.0, (work_end - work_start).total_seconds())

    if now < work_start or now >= work_end:
        return PacingDecision(False, 0, "outside_window", 0.0, 0.0)

    remaining = daily_limit - sent_today
    if remaining <= 0:
        return PacingDecision(False, 0, "limit_reached", float(daily_limit), 0.0)

    if last_send_at is not None and (now - last_send_at).total_seconds() < min_gap_sec:
        return PacingDecision(False, 0, "min_gap", 0.0, 0.0)

    elapsed = (now - work_start).total_seconds()
    avg_batch = (lo + hi) / 2
    # Lead of one average session so the first session fires when the window opens.
    lead_sec = window_sec * avg_batch / max(1, daily_limit)
    target_now = daily_limit * min(1.0, (elapsed + lead_sec) / window_sec)
    deficit = target_now - sent_today

    # Urgent: if we skip this tick, can the remaining sessions still finish the quota?
    time_left = (work_end - now).total_seconds() - TICK_SEC
    cycle = min_gap_sec + EST_SESSION_SEC
    capacity_if_wait = max(0, math.floor(time_left / cycle)) * hi
    urgent = capacity_if_wait < remaining

    needed = min(lo, remaining)
    if deficit < needed and not urgent:
        return PacingDecision(False, 0, "on_pace", target_now, deficit)

    batch = rng.randint(lo, hi)
    if deficit > batch:
        batch = min(hi, math.ceil(deficit))
    batch = max(1, min(batch, remaining))
    return PacingDecision(True, batch, "urgent" if urgent and deficit < needed else "behind", target_now, deficit, urgent)


def expected_by_now(
    now: datetime, window: Tuple[datetime, datetime], daily_limit: int
) -> float:
    """Linear expectation with no lead, used by the watchdog."""
    work_start, work_end = window
    span = max(1.0, (work_end - work_start).total_seconds())
    frac = min(1.0, max(0.0, (now - work_start).total_seconds() / span))
    return daily_limit * frac
