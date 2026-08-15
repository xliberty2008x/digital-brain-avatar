from __future__ import annotations

import pytest

from scripts.heal_graph_hygiene import (
    CONFIRM_TOKENS,
    FORBIDDEN_PERSON_MERGE_IDS,
    PARKED_PERSON_IDS,
    PERSON_KEEP_MAP,
    main,
    refuse_unattended_flags,
    sanitize_rel_type,
    validate_person_keep_map,
)


def test_keep_map_valid_and_parks_olivia() -> None:
    validate_person_keep_map()
    assert "olivia_daughter" in PARKED_PERSON_IDS
    assert all(row["id"] != "olivia_daughter" for row in PERSON_KEEP_MAP)
    assert "user_node" in FORBIDDEN_PERSON_MERGE_IDS
    assert "21e1a32e-ebc1-46b0-aeb4-5c5b3ce392cc" in FORBIDDEN_PERSON_MERGE_IDS


def test_validate_rejects_parked_in_keep_map() -> None:
    bad = (
        {
            "id": "olivia_daughter",
            "keep": "4:c15718c3-6091-454f-bf13-c49443078b10:486",
            "drop": ("4:c15718c3-6091-454f-bf13-c49443078b10:185",),
        },
    )
    with pytest.raises(ValueError, match="parked_person_id_in_keep_map"):
        validate_person_keep_map(bad)


def test_validate_rejects_self_merge() -> None:
    bad = (
        {
            "id": "user_node",
            "keep": "4:c15718c3-6091-454f-bf13-c49443078b10:813",
            "drop": ("4:c15718c3-6091-454f-bf13-c49443078b10:555",),
        },
    )
    with pytest.raises(ValueError, match="forbidden_person_merge"):
        validate_person_keep_map(bad)


def test_rel_type_sanitizer() -> None:
    assert sanitize_rel_type("KNOWS") == "KNOWS"
    with pytest.raises(ValueError, match="unsafe_rel_type"):
        sanitize_rel_type("knows")
    with pytest.raises(ValueError, match="unsafe_rel_type"):
        sanitize_rel_type("KNOWS} DELETE (n)")


def test_refuse_yes_flags() -> None:
    with pytest.raises(SystemExit, match="unattended"):
        refuse_unattended_flags(["apply", "--yes"])
    with pytest.raises(SystemExit, match="unattended"):
        refuse_unattended_flags(["--force"])


def test_confirm_token_is_exact() -> None:
    assert CONFIRM_TOKENS["person-clones"] == "HEAL person-clones"


def test_main_rejects_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="unattended"):
        main(["apply", "--phase", "person-clones", "--yes"])
