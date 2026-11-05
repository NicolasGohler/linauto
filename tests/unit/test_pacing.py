"""Stateless pacing: window determinism, catch-up, urgency, limits."""
import random
from datetime import date, datetime, timedelta

from releasi.scheduler import pacing

WIN = (datetime(2026, 11, 5, 5, 0), datetime(2026, 11, 5, 13, 0))  # 8h window
RNG = lambda: random.Random(1)  # noqa: E731
BATCH = (6, 10)


def d(now, sent, last=None, limit=35):
    return pacing.decide(now, WIN, sent, limit, last, BATCH, RNG())


def test_window_is_deterministic_and_follows_timezone():
    a = pacing.work_window("acc", date(2026, 11, 5), "Asia/Dubai", 9, 17, (-30, 45), (-45, 30))
    b = pacing.work_window("acc", date(2026, 11, 5), "Asia/Dubai", 9, 17, (-30, 45), (-45, 30))
    assert a == b
    # Dubai 09:00 = 05:00 UTC; start jitter is within [-30, +45] min
    assert datetime(2026, 11, 5, 4, 30) <= a[0] <= datetime(2026, 11, 5, 5, 45)
    c = pacing.work_window("acc", date(2026, 11, 5), "America/Toronto", 9, 17, (-30, 45), (-45, 30))
    assert c[0] > a[0] + timedelta(hours=7)  # tz change moves the window with no restart/cache


def test_outside_window_and_limit():
    assert d(WIN[0] - timedelta(minutes=1), 0).reason == "outside_window"
    assert d(WIN[1], 0).reason == "outside_window"
    assert d(WIN[0] + timedelta(hours=1), 35).reason == "limit_reached"


def test_first_session_fires_at_window_open():
    r = d(WIN[0], 0)
    assert r.run and 6 <= r.batch_size <= 10


def test_on_pace_waits():
    r = d(WIN[0] + timedelta(minutes=30), 8, last=WIN[0] + timedelta(minutes=10))
    assert not r.run


def test_min_gap_blocks_even_when_far_behind():
    now = WIN[0] + timedelta(hours=4)
    r = d(now, 0, last=now - timedelta(minutes=5))
    assert not r.run and r.reason == "min_gap"


def test_catch_up_after_restart_uses_big_batch():
    now = WIN[0] + timedelta(hours=4)  # halfway, nothing sent (e.g. lost state)
    r = d(now, 0, last=None)
    assert r.run and r.batch_size == 10 and r.deficit > 10


def test_urgent_when_window_closing():
    now = WIN[1] - timedelta(minutes=45)
    r = d(now, 20, last=now - timedelta(minutes=30))
    assert r.run and r.urgent


def test_batch_never_exceeds_remaining():
    now = WIN[1] - timedelta(minutes=40)
    r = d(now, 33, last=now - timedelta(minutes=30))
    assert r.run and r.batch_size <= 2


def test_full_day_simulation_hits_limit():
    """Tick every 5 min, each session takes 8 min and sends its batch."""
    now, sent, last, rng = WIN[0], 0, None, random.Random(7)
    while now < WIN[1]:
        r = pacing.decide(now, WIN, sent, 35, last, BATCH, rng)
        if r.run:
            sent += r.batch_size
            now += timedelta(minutes=8)
            last = now
        now += timedelta(minutes=5)
    assert sent == 35


def test_simulation_with_mid_day_restart_still_hits_limit():
    """In-memory state is gone at 13:00-equivalent; DB progress alone drives the rest."""
    now, sent, last, rng = WIN[0], 0, None, random.Random(3)
    restarted = False
    while now < WIN[1]:
        if not restarted and now >= WIN[0] + timedelta(hours=3):
            restarted = True  # nothing to reset: decide() holds no state
        r = pacing.decide(now, WIN, sent, 35, last, BATCH, rng)
        if r.run:
            sent += r.batch_size
            now += timedelta(minutes=8)
            last = now
        now += timedelta(minutes=5)
    assert sent == 35


def test_late_start_two_hours_in_still_hits_limit():
    now, sent, last, rng = WIN[0] + timedelta(hours=2), 0, None, random.Random(11)
    while now < WIN[1]:
        r = pacing.decide(now, WIN, sent, 35, last, BATCH, rng)
        if r.run:
            sent += r.batch_size
            now += timedelta(minutes=8)
            last = now
        now += timedelta(minutes=5)
    assert sent == 35


def test_expected_by_now_is_linear():
    mid = WIN[0] + timedelta(hours=4)
    assert abs(pacing.expected_by_now(mid, WIN, 40) - 20) < 1e-6
