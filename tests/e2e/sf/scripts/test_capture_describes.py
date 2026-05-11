"""
Unit tests for capture_describes.py trim + sort logic.

These tests use mock REST responses only — no live Salesforce org is required.
Run with: pytest tests/e2e/sf/scripts/test_capture_describes.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the script module is importable from any working directory.
_SCRIPTS_DIR = Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from capture_describes import (  # noqa: E402
    _trim_child_relationship,
    _trim_describe,
    _trim_field,
    _trim_object_list,
)


# ---------------------------------------------------------------------------
# _trim_object_list
# ---------------------------------------------------------------------------


def test_trim_object_list_basic():
    """Objects with createable|updateable|deletable=True are kept; others dropped."""
    raw = [
        {"name": "Account", "createable": True, "updateable": True, "deletable": True},
        {"name": "Note", "createable": True, "updateable": False, "deletable": False},
        {"name": "SomeSysObject", "createable": False, "updateable": False, "deletable": False},
        {"name": "ZapierObject__c", "createable": False, "updateable": True, "deletable": False},
    ]
    result = _trim_object_list(raw)
    assert result == ["Account", "Note", "ZapierObject__c"]


def test_trim_object_list_sorted():
    """Output is sorted alphabetically."""
    raw = [
        {"name": "Contact", "createable": True, "updateable": True, "deletable": True},
        {"name": "Account", "createable": True, "updateable": True, "deletable": True},
    ]
    assert _trim_object_list(raw) == ["Account", "Contact"]


def test_trim_object_list_empty():
    assert _trim_object_list([]) == []


def test_trim_object_list_none_flags_treated_as_false():
    """Missing flag keys are treated as falsy."""
    raw = [{"name": "Unknown"}]
    assert _trim_object_list(raw) == []


# ---------------------------------------------------------------------------
# _trim_field
# ---------------------------------------------------------------------------

_FLAT_FIELD_RAW = {
    "name": "Name",
    "label": "Account Name",
    "type": "string",
    "createable": True,
    "updateable": True,
    "nillable": False,
    "defaultedOnCreate": False,
    "externalId": False,
    "idLookup": False,
    "referenceTo": [],
    "relationshipName": None,
}

_REF_FIELD_RAW = {
    "name": "OwnerId",
    "label": "Owner ID",
    "type": "reference",
    "createable": True,
    "updateable": True,
    "nillable": False,
    "defaultedOnCreate": True,
    "externalId": False,
    "idLookup": False,
    "referenceTo": ["User", "Group"],
    "relationshipName": "Owner",
}


def test_trim_field_flat():
    result = _trim_field(_FLAT_FIELD_RAW)
    assert result == {
        "api_name": "Name",
        "createable": True,
        "defaulted_on_create": False,
        "external_id": False,
        "id_lookup": False,
        "label": "Account Name",
        "nillable": False,
        "reference_to": [],
        "relationship_name": None,
        "type": "string",
        "updateable": True,
    }


def test_trim_field_reference():
    result = _trim_field(_REF_FIELD_RAW)
    assert result["type"] == "reference"
    assert result["reference_to"] == ["Group", "User"]  # sorted
    assert result["relationship_name"] == "Owner"
    assert result["defaulted_on_create"] is True


def test_trim_field_id_lookup():
    """idLookup flag (e.g. User.Username) must be captured as id_lookup."""
    raw = {**_FLAT_FIELD_RAW, "name": "Username", "idLookup": True, "externalId": False}
    result = _trim_field(raw)
    assert result["id_lookup"] is True
    assert result["external_id"] is False


def test_trim_field_external_id():
    """externalId flag captured as external_id."""
    raw = {**_FLAT_FIELD_RAW, "name": "External_Id__c", "externalId": True}
    result = _trim_field(raw)
    assert result["external_id"] is True


def test_trim_field_reference_to_sorted():
    """reference_to list is sorted for determinism."""
    raw = {**_REF_FIELD_RAW, "referenceTo": ["User", "Contact", "Account"]}
    result = _trim_field(raw)
    assert result["reference_to"] == ["Account", "Contact", "User"]


def test_trim_field_missing_keys_default_safely():
    """Missing keys produce safe defaults without KeyError."""
    result = _trim_field({})
    assert result["api_name"] == ""
    assert result["type"] == ""
    assert result["reference_to"] == []
    assert result["relationship_name"] is None
    assert result["createable"] is False


# ---------------------------------------------------------------------------
# _trim_child_relationship
# ---------------------------------------------------------------------------


def test_trim_child_relationship_basic():
    raw = {"childSObject": "Contact", "field": "AccountId", "relationshipName": "Contacts"}
    result = _trim_child_relationship(raw)
    assert result == {"child_sobject": "Contact", "field": "AccountId"}


def test_trim_child_relationship_drops_extra_keys():
    raw = {
        "childSObject": "Case",
        "field": "AccountId",
        "relationshipName": "Cases",
        "cascadeDelete": False,
        "deprecatedAndHidden": False,
    }
    result = _trim_child_relationship(raw)
    assert set(result.keys()) == {"child_sobject", "field"}


# ---------------------------------------------------------------------------
# _trim_describe — integration across all helpers
# ---------------------------------------------------------------------------

_MOCK_DESCRIBE = {
    "name": "Account",
    "fields": [
        {**_REF_FIELD_RAW},
        {**_FLAT_FIELD_RAW, "name": "AnnualRevenue", "label": "Annual Revenue", "type": "currency",
         "nillable": True, "referenceTo": [], "relationshipName": None},
        {**_FLAT_FIELD_RAW},  # Name field
    ],
    "childRelationships": [
        {"childSObject": "Opportunity", "field": "AccountId", "relationshipName": "Opportunities"},
        {"childSObject": "Contact", "field": "AccountId", "relationshipName": "Contacts"},
        # No childSObject — should be excluded
        {"childSObject": None, "field": "SomeField", "relationshipName": "Foo"},
        # No field — should be excluded
        {"childSObject": "Case", "field": None, "relationshipName": "Cases"},
    ],
}


def test_trim_describe_shape():
    result = _trim_describe(_MOCK_DESCRIBE, "2026-05-11T12:00:00Z")
    assert result["name"] == "Account"
    assert result["fetched_at"] == "2026-05-11T12:00:00Z"
    assert set(result.keys()) == {"name", "fields", "child_relationships", "fetched_at"}


def test_trim_describe_fields_sorted_by_api_name():
    result = _trim_describe(_MOCK_DESCRIBE, "2026-05-11T12:00:00Z")
    api_names = [f["api_name"] for f in result["fields"]]
    assert api_names == sorted(api_names, key=str.lower)


def test_trim_describe_fields_count():
    result = _trim_describe(_MOCK_DESCRIBE, "2026-05-11T12:00:00Z")
    # All three raw fields should be present
    assert len(result["fields"]) == 3


def test_trim_describe_child_rels_filtered():
    """child_relationships with missing childSObject or field are excluded."""
    result = _trim_describe(_MOCK_DESCRIBE, "2026-05-11T12:00:00Z")
    # Only Contact + Opportunity survive; Case (null field) and Foo (null sobject) are dropped
    child_sobjects = {cr["child_sobject"] for cr in result["child_relationships"]}
    assert child_sobjects == {"Contact", "Opportunity"}


def test_trim_describe_child_rels_sorted():
    """child_relationships sorted by (child_sobject, field)."""
    result = _trim_describe(_MOCK_DESCRIBE, "2026-05-11T12:00:00Z")
    pairs = [(cr["child_sobject"], cr["field"]) for cr in result["child_relationships"]]
    assert pairs == sorted(pairs)


def test_trim_describe_empty_describe():
    """Empty describe payload produces valid (empty) output without error."""
    result = _trim_describe({}, "2026-05-11T12:00:00Z")
    assert result["name"] == ""
    assert result["fields"] == []
    assert result["child_relationships"] == []
    assert result["fetched_at"] == "2026-05-11T12:00:00Z"


def test_trim_field_keys_exact():
    """_trim_field output has exactly the D2 keys — no extras, no missing."""
    expected_keys = {
        "api_name",
        "createable",
        "defaulted_on_create",
        "external_id",
        "id_lookup",
        "label",
        "nillable",
        "reference_to",
        "relationship_name",
        "type",
        "updateable",
    }
    result = _trim_field(_FLAT_FIELD_RAW)
    assert set(result.keys()) == expected_keys
