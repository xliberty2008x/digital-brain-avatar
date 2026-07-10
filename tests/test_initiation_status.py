"""Unit tests for initiate-protocol status derivation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "digital-brain-buddy"
    / "scripts"
    / "initiation_status.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("initiation_status", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def status_mod():
    return _load_module()


def test_empty_graph_missing_language(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": False,
            "has_self": False,
            "has_anchor_person": False,
            "has_focus": False,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 0,
            "topic_count": 0,
        }
    )
    assert result["status"] == "missing_language"
    assert result["complete"] is False
    assert result["mode"] == "INITIATE"
    assert result["graph_thin"] is True


def test_language_only_missing_self(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": False,
            "has_anchor_person": False,
            "has_focus": False,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 0,
            "topic_count": 0,
        }
    )
    assert result["status"] == "missing_self"
    assert result["next_step"] == "self"


def test_self_only_missing_anchor(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": False,
            "has_focus": False,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 0,
            "topic_count": 0,
        }
    )
    assert result["status"] == "missing_anchor_person"
    assert result["next_step"] == "anchor_person"


def test_self_and_anchor_missing_focus(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": False,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 1,
            "topic_count": 0,
        }
    )
    assert result["status"] == "missing_focus"
    assert result["next_step"] == "focus"


def test_seeds_missing_overlay(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": True,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 1,
            "topic_count": 1,
        }
    )
    assert result["status"] == "missing_soul_overlay"
    assert result["next_step"] == "soul_overlay"


def test_seeds_and_overlay_missing_receipt(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": True,
            "has_soul_overlay_beyond_language": True,
            "has_receipt": False,
            "non_self_person_count": 1,
            "topic_count": 1,
        }
    )
    assert result["status"] == "missing_receipt"
    assert result["next_step"] == "receipt"
    assert result["mode"] == "INITIATE"


def test_complete_thin_graph(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": True,
            "has_soul_overlay_beyond_language": True,
            "has_receipt": True,
            "non_self_person_count": 1,
            "topic_count": 1,
        }
    )
    assert result["status"] == "complete"
    assert result["complete"] is True
    assert result["mode"] == "NORMAL"
    assert result["graph_thin"] is True
    assert result["soft_hooks_allowed"] is True


def test_complete_not_thin(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": True,
            "has_soul_overlay_beyond_language": True,
            "has_receipt": True,
            "non_self_person_count": 3,
            "topic_count": 2,
        }
    )
    assert result["status"] == "complete"
    assert result["graph_thin"] is False
    assert result["soft_hooks_allowed"] is False


def test_receipt_without_self_is_not_complete(status_mod):
    """Corrupt/partial graphs: receipt alone must not skip seeds."""
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": False,
            "has_anchor_person": False,
            "has_focus": False,
            "has_soul_overlay_beyond_language": True,
            "has_receipt": True,
            "non_self_person_count": 0,
            "topic_count": 0,
        }
    )
    assert result["complete"] is False
    assert result["status"] == "missing_self"
