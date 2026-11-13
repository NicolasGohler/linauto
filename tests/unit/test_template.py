"""Tests for message template rendering."""
import pytest
from unittest.mock import MagicMock
from releasi.campaign.template import render_template, extract_variables, validate_template


def _make_lead(**kwargs):
    """Create a mock Lead object."""
    lead = MagicMock()
    lead.first_name = kwargs.get("first_name")
    lead.last_name = kwargs.get("last_name")
    lead.company = kwargs.get("company")
    lead.title = kwargs.get("title")
    lead.linkedin_url = kwargs.get("linkedin_url", "https://linkedin.com/in/test")
    lead.extra_data = kwargs.get("extra_data")
    return lead


def test_render_simple():
    lead = _make_lead(first_name="Nicolas")
    result = render_template("Hi {{firstname}}, happy to connect!", lead)
    assert result == "Hi Nicolas, happy to connect!"


def test_render_multiple_variables():
    lead = _make_lead(first_name="Nicolas", company="Threedom")
    result = render_template("Hi {{firstname}} from {{company}}!", lead)
    assert result == "Hi Nicolas from Threedom!"


def test_render_missing_variable_returns_empty():
    lead = _make_lead(first_name="Nicolas", company=None)
    result = render_template("Hi {{firstname}} at {{company}}!", lead)
    assert result == "Hi Nicolas at !"


def test_render_extra_data():
    lead = _make_lead(first_name="Nicolas", extra_data={"industry": "Tech", "location": "Berlin"})
    result = render_template("Hi {{firstname}} in {{industry}}!", lead)
    assert result == "Hi Nicolas in Tech!"


def test_render_case_insensitive_extra_data():
    lead = _make_lead(extra_data={"Industry": "SaaS"})
    result = render_template("In {{industry}}", lead)
    assert result == "In SaaS"


def test_render_first_name_alias():
    lead = _make_lead(first_name="Nicolas")
    assert render_template("{{first_name}}", lead) == "Nicolas"
    assert render_template("{{firstname}}", lead) == "Nicolas"


def test_render_no_variables():
    lead = _make_lead()
    result = render_template("Hello, let's connect!", lead)
    assert result == "Hello, let's connect!"


def test_extract_variables():
    variables = extract_variables("Hi {{firstname}} from {{company}}, title: {{title}}")
    assert set(variables) == {"firstname", "company", "title"}


def test_extract_no_variables():
    assert extract_variables("Hello world") == []


def test_validate_warns_no_variables():
    warnings = validate_template("Hello, let's connect!")
    assert any("no {{variables}}" in w for w in warnings)


def test_validate_warns_long_template():
    template = "x" * 260
    warnings = validate_template(template)
    assert any("300" in w for w in warnings)


def test_missing_variables_flags_empty_and_absent():
    from types import SimpleNamespace
    from releasi.campaign.template import missing_variables
    lead = SimpleNamespace(
        first_name="Sam", last_name=None, company="Acme", title=None,
        linkedin_url="x", extra_data={"Signal_Line": "hiring SDRs", "example_1": "  "},
    )
    tpl = "Hi {{first_name}}, they're all {{signal_line}}. {{example_1}} and {{example_2}}"
    assert missing_variables(tpl, lead) == ["example_1", "example_2"]
    assert missing_variables("Hi {{first_name}}", lead) == []
