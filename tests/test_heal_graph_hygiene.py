from __future__ import annotations

import pytest

from scripts.heal_graph_hygiene import (
    BACKFILL_ID_SET_QUERY,
    CONFIRM_TOKENS,
    FORBIDDEN_PERSON_MERGE_IDS,
    PARKED_PERSON_IDS,
    PERSON_KEEP_MAP,
    classify_journal_group,
    is_true_follows_fork,
    main,
    pick_journal_keep,
    pick_topic_keep,
    refuse_unattended_flags,
    rewire_and_remove,
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


def test_confirm_tokens_for_remaining_phases() -> None:
    assert CONFIRM_TOKENS["journal-same-id"] == "HEAL journal-same-id"
    assert CONFIRM_TOKENS["topic-ci"] == "HEAL topic-ci"
    assert CONFIRM_TOKENS["orphan-states"] == "HEAL orphan-states"
    assert CONFIRM_TOKENS["backfill-ids"] == "HEAL backfill-ids"


def test_park_true_follows_fork_and_twin_tip() -> None:
    members = [
        {"element_id": "4:c15718c3-6091-454f-bf13-c49443078b10:2818", "deg": 12, "has_follows": True, "has_head": False, "on_primary": True, "has_timestamp": True},
        {"element_id": "4:c15718c3-6091-454f-bf13-c49443078b10:2822", "deg": 11, "has_follows": True, "has_head": False, "on_primary": False, "has_timestamp": True},
    ]
    assert is_true_follows_fork(members)
    classified = classify_journal_group("E05E1243-CD7B-4724-8C93-5D15B02D2FA6", members)
    assert classified["decision"] == "park"
    assert "parked_twin_tip" in classified["reasons"]
    assert "follows_fork" in classified["reasons"]


def test_merge_keeps_primary_chain_not_higher_degree() -> None:
    members = [
        {"element_id": "keep-chain", "deg": 1, "has_follows": True, "has_head": False, "on_primary": True, "has_timestamp": False},
        {"element_id": "drop-clone", "deg": 9, "has_follows": False, "has_head": False, "on_primary": False, "has_timestamp": True},
    ]
    classified = classify_journal_group("safe-clone", members)
    assert classified["decision"] == "merge"
    assert classified["keep"] == "keep-chain"
    assert classified["drop"] == ["drop-clone"]


def test_keep_rule_degree_then_timestamp() -> None:
    members = [
        {"element_id": "a", "deg": 1, "has_follows": False, "on_primary": False, "has_timestamp": True},
        {"element_id": "b", "deg": 3, "has_follows": False, "on_primary": False, "has_timestamp": False},
    ]
    assert pick_journal_keep(members) == "b"


def test_topic_keep_degree_then_has_id_then_eid() -> None:
    nodes = [
        {"elementId": "eid-z", "deg": 2, "has_id": True},
        {"elementId": "eid-a", "deg": 2, "has_id": False},
        {"elementId": "eid-m", "deg": 5, "has_id": False},
    ]
    assert pick_topic_keep(nodes) == "eid-m"
    tied = [
        {"elementId": "eid-b", "deg": 2, "has_id": False},
        {"elementId": "eid-a", "deg": 2, "has_id": True},
    ]
    assert pick_topic_keep(tied) == "eid-a"


def test_backfill_query_sets_only_id() -> None:
    assert "SET n.id = $donor_id" in BACKFILL_ID_SET_QUERY
    assert BACKFILL_ID_SET_QUERY.count("SET ") == 1
    assert "embedding" not in BACKFILL_ID_SET_QUERY
    assert "name" not in BACKFILL_ID_SET_QUERY


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeTx:
    def __init__(self, rels):
        self.rels = rels
        self.calls: list[str] = []

    def run(self, query, **params):
        self.calls.append(query)
        if "MATCH (drop)-[r]-(other)" in query:
            return _FakeResult(self.rels)
        return _FakeResult([{"n": 0, "copied": False}])


def test_rewire_forbids_follows() -> None:
    tx = _FakeTx(
        [
            {
                "rid": "r1",
                "t": "FOLLOWS",
                "start": "drop",
                "end": "parent",
                "props": {},
            }
        ]
    )
    with pytest.raises(ValueError, match="forbidden_rel_on_drop:FOLLOWS"):
        rewire_and_remove(tx, "keep", "drop", copy_fields=(), forbid_rel_types=frozenset({"FOLLOWS", "HEAD"}))
    assert not any("CREATE (a)-[r:FOLLOWS]" in c for c in tx.calls)
