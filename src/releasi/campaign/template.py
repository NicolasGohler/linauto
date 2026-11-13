"""Message template engine with {{variable}} substitution."""
from __future__ import annotations

import re
from typing import Optional

import structlog

from releasi.db.models import Lead

logger = structlog.get_logger()

# Capture an optional single horizontal-space before the token so that
# "Hi {{first_name}}," renders "Hi John," when filled and "Hi," (not "Hi ,")
# when the variable is absent.
_PATTERN = re.compile(r'([ \t]?)\{\{(\w+)\}\}')

# Map template variable names to Lead model attributes
_FIELD_MAP = {
    "firstname": "first_name",
    "first_name": "first_name",
    "lastname": "last_name",
    "last_name": "last_name",
    "company": "company",
    "title": "title",
}


def render_template(template: str, lead: Lead) -> str:
    """
    Replace {{variable}} placeholders with lead data.

    Supports:
      - {{firstname}}, {{first_name}} -> lead.first_name
      - {{lastname}}, {{last_name}} -> lead.last_name
      - {{company}} -> lead.company
      - {{title}} -> lead.title
      - Any other variable -> looked up in lead.extra_data JSON

    Missing values are replaced with empty string.
    """
    def _replace(match):
        leading_space = match.group(1)   # space already in template (may be "")
        var_name = match.group(2).lower()

        def _with_space(val: str) -> str:
            """Return the resolved value, adding a word-boundary space if needed."""
            if not val:
                return ""
            if leading_space:
                # A space was in the template — include it with the value.
                return leading_space + val
            # No space in the template. Add one if the token is flush against
            # a non-whitespace character (e.g. "Hi{{name}}" → "Hi Name").
            pos = match.start()
            char_before = match.string[pos - 1] if pos > 0 else ""
            if char_before and not char_before.isspace():
                return " " + val
            return val

        # Check standard field map
        attr_name = _FIELD_MAP.get(var_name)
        if attr_name:
            value = getattr(lead, attr_name, None)
            if value:
                return _with_space(value)
            logger.debug("template.missing_field", variable=var_name, lead_url=lead.linkedin_url)
            return ""

        # Check extra_data
        if lead.extra_data and var_name in lead.extra_data:
            return _with_space(str(lead.extra_data[var_name]))

        # Also try original case in extra_data
        if lead.extra_data:
            for key, val in lead.extra_data.items():
                if key.lower() == var_name:
                    return _with_space(str(val))

        logger.debug("template.unknown_variable", variable=var_name, lead_url=lead.linkedin_url)
        return ""

    return _PATTERN.sub(_replace, template)


def _resolve_variable(var_name: str, lead: Lead) -> str:
    var_name = var_name.lower()
    attr_name = _FIELD_MAP.get(var_name)
    if attr_name:
        return str(getattr(lead, attr_name, None) or "")
    for key, val in (lead.extra_data or {}).items():
        if key.lower() == var_name:
            return "" if val is None else str(val)
    return ""


def missing_variables(template: str, lead: Lead) -> list:
    """Variables in the template that would render empty for this lead."""
    return sorted({
        m.group(2) for m in _PATTERN.finditer(template)
        if not _resolve_variable(m.group(2), lead).strip()
    })


def extract_variables(template: str) -> list:
    """Extract all {{variable}} names from a template."""
    return [m.group(2) for m in _PATTERN.finditer(template)]


def validate_template(template: str, lead: Optional[Lead] = None) -> list:
    """
    Validate a template. Returns list of warnings.
    If a sample lead is provided, checks that all variables resolve.
    """
    warnings = []
    variables = extract_variables(template)

    if not variables:
        warnings.append("Template contains no {{variables}} — message will be identical for all leads")

    if lead:
        for var in variables:
            var_lower = var.lower()
            attr_name = _FIELD_MAP.get(var_lower)
            if attr_name:
                if not getattr(lead, attr_name, None):
                    warnings.append(f"{{{{{var}}}}} will be empty for this lead")
            elif not lead.extra_data or var_lower not in {k.lower() for k in lead.extra_data}:
                warnings.append(f"{{{{{var}}}}} not found in lead data")

    # LinkedIn connection note limit: 300 characters
    if len(template) > 250:
        warnings.append(f"Template is {len(template)} chars — LinkedIn limit is 300 (after variable substitution)")

    return warnings
