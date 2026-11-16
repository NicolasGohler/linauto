from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from releasi.campaign.contact_info import contact_info_updates

NOW = datetime(2026, 11, 16, 12, 0, 0)


def _lead(email=None, phone=None, extra=None):
    return SimpleNamespace(email=email, phone=phone, extra_data=extra)


def test_keeps_work_email_and_stores_harvested_separately():
    u = contact_info_updates(_lead("sam@acme.com", extra={"signal_line": "x"}), {"email": "sam@gmail.com"}, NOW)
    assert "email" not in u
    assert u["extra_data"]["contact_info_email"] == "sam@gmail.com"
    assert u["extra_data"]["signal_line"] == "x"


def test_phone_only_never_blanks_email():
    u = contact_info_updates(_lead("sam@acme.com"), {"email": None, "phone": "+1 555"}, NOW)
    assert "email" not in u and u["phone"] == "+1 555"


def test_fills_empty_email_and_keeps_existing_phone():
    u = contact_info_updates(_lead(None, "+44 1"), {"email": "a@b.com", "phone": "+1 2"}, NOW)
    assert u["email"] == "a@b.com"
    assert "phone" not in u and u["extra_data"]["contact_info_phone"] == "+1 2"


def test_nothing_found_means_no_update():
    assert contact_info_updates(_lead("a@b.com"), {"email": "", "phone": None}, NOW) == {}
