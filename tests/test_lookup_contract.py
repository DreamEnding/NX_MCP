"""Lookup tests use iterable NX collection seams, never unsupported ToArray()."""

from types import SimpleNamespace

import pytest

from nx_mcp.runtime import NXToolError
from nx_mcp.utils.geometry import resolve_object_by_name


def test_lookup_accepts_journal_identifier_and_casefolded_name():
    body = SimpleNamespace(Name="", JournalIdentifier="BODY(1)")
    named = SimpleNamespace(Name="Profile", JournalIdentifier="SKETCH(2)")
    assert resolve_object_by_name(None, "body(1)", iter([body])) is body
    assert resolve_object_by_name(None, "PROFILE", iter([named])) is named
    assert resolve_object_by_name(None, "missing", [body, named]) is None


def test_lookup_rejects_ambiguous_names_but_deduplicates_same_object():
    first = SimpleNamespace(Name="Part", JournalIdentifier="BODY(1)")
    second = SimpleNamespace(Name="PART", JournalIdentifier="BODY(2)")
    assert resolve_object_by_name(None, "part", [first], [first]) is first
    with pytest.raises(NXToolError) as error:
        resolve_object_by_name(None, "part", [first, second])
    assert error.value.code == "NX_AMBIGUOUS_REFERENCE"
