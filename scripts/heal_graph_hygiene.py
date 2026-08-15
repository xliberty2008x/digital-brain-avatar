#!/usr/bin/env python3
"""Typed operator heal for same-id clones and graph hygiene.

Interactive host script. Uses the Neo4j driver (admin/operator creds),
never generic write_neo4j_cypher. No --yes path.

Reports counts and element ids only. Never reads or emits journal content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REL_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROP_COPY_FIELDS = ("role", "relation")
CONFIRM_TOKENS = {
    "person-clones": "HEAL person-clones",
    "journal-same-id": "HEAL journal-same-id",
    "topic-ci": "HEAL topic-ci",
    "orphan-states": "HEAL orphan-states",
    "backfill-ids": "HEAL backfill-ids",
}

HEAD_EID = "4:c15718c3-6091-454f-bf13-c49443078b10:2834"
CHAIN_PREV_EID = "4:c15718c3-6091-454f-bf13-c49443078b10:2818"
TWIN_TIP_EID = "4:c15718c3-6091-454f-bf13-c49443078b10:2822"
PROTECTED_JOURNAL_EIDS = frozenset({HEAD_EID, CHAIN_PREV_EID, TWIN_TIP_EID})

PARKED_PERSON_IDS = frozenset({"olivia_daughter"})
FORBIDDEN_PERSON_MERGE_IDS = frozenset(
    {
        "user_node",
        "21e1a32e-ebc1-46b0-aeb4-5c5b3ce392cc",
    }
)

# Authorized keep-map only. Parked ids must not appear here.
PERSON_KEEP_MAP: tuple[dict[str, Any], ...] = (
    {
        "id": "3d1cd817-dfef-4e07-b01d-a7304c5174e5",
        "keep": "4:c15718c3-6091-454f-bf13-c49443078b10:439",
        "drop": ("4:c15718c3-6091-454f-bf13-c49443078b10:554",),
    },
    {
        "id": "Ruslan_Koptev",
        "keep": "4:c15718c3-6091-454f-bf13-c49443078b10:184",
        "drop": ("4:c15718c3-6091-454f-bf13-c49443078b10:316",),
    },
    {
        "id": "d2f430eb-90f3-4530-8082-7c3d680e30f6",
        "keep": "4:c15718c3-6091-454f-bf13-c49443078b10:225",
        "drop": (
            "4:c15718c3-6091-454f-bf13-c49443078b10:285",
            "4:c15718c3-6091-454f-bf13-c49443078b10:497",
        ),
    },
)

PARKED_JOURNAL_IDS = frozenset({"E05E1243-CD7B-4724-8C93-5D15B02D2FA6"})


def load_dotenv_if_present() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'").strip('"')


def sanitize_rel_type(rel_type: str) -> str:
    if not isinstance(rel_type, str) or not REL_TYPE_RE.fullmatch(rel_type):
        raise ValueError(f"unsafe_rel_type:{rel_type!r}")
    return rel_type


def validate_person_keep_map(
    rows: tuple[dict[str, Any], ...] = PERSON_KEEP_MAP,
) -> None:
    seen_eids: set[str] = set()
    for row in rows:
        pid = row["id"]
        if pid in PARKED_PERSON_IDS:
            raise ValueError(f"parked_person_id_in_keep_map:{pid}")
        if pid in FORBIDDEN_PERSON_MERGE_IDS:
            raise ValueError(f"forbidden_person_merge:{pid}")
        keep = row["keep"]
        drops = tuple(row["drop"])
        if keep in drops:
            raise ValueError(f"keep_in_drop:{keep}")
        for eid in (keep, *drops):
            if eid in seen_eids:
                raise ValueError(f"duplicate_element_id:{eid}")
            seen_eids.add(eid)


def confirm(prompt: str, *, expected: str) -> None:
    """Interactive confirm — no unattended --yes bypass exists by design."""
    print(prompt)
    print(f"Type exactly: {expected}")
    try:
        got = input("> ").strip()
    except EOFError as exc:
        raise SystemExit(
            "interactive confirmation required (no unattended path)"
        ) from exc
    if got != expected:
        raise SystemExit("confirmation mismatch; aborted")


def refuse_unattended_flags(argv: list[str]) -> None:
    if any(a in {"--yes", "-y", "--force", "--non-interactive"} for a in argv):
        raise SystemExit("unattended flags are not allowed")


def auth() -> tuple[str, str, str, str]:
    load_dotenv_if_present()
    uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL") or "bolt://localhost:7687"
    user = (
        os.getenv("NEO4J_ADMIN_USERNAME")
        or os.getenv("NEO4J_USERNAME")
        or "neo4j"
    )
    password = os.getenv("NEO4J_ADMIN_PASSWORD") or os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE") or "neo4j"
    if not password:
        raise SystemExit("NEO4J_ADMIN_PASSWORD or NEO4J_PASSWORD is required")
    return uri, user, password, database


def _driver():
    from neo4j import GraphDatabase

    uri, user, password, database = auth()
    return GraphDatabase.driver(uri, auth=(user, password)), database


def _node_snapshot(session, eid: str) -> dict[str, Any] | None:
    rec = session.run(
        """
        MATCH (n) WHERE elementId(n) = $eid
        RETURN elementId(n) AS element_id,
               labels(n) AS labels,
               n.id AS id,
               n.role IS NOT NULL AS has_role,
               n.relation IS NOT NULL AS has_relation,
               COUNT { (n)--() } AS deg
        """,
        eid=eid,
    ).single()
    return dict(rec) if rec else None


def _shared_neighbors(session, a: str, b: str) -> int:
    rec = session.run(
        """
        MATCH (x) WHERE elementId(x) = $a
        MATCH (y) WHERE elementId(y) = $b
        MATCH (x)--(n)--(y)
        WHERE elementId(n) <> $a AND elementId(n) <> $b
        RETURN count(DISTINCT n) AS n
        """,
        a=a,
        b=b,
    ).single()
    return int(rec["n"]) if rec else 0


def plan_person_clones(session) -> dict[str, Any]:
    validate_person_keep_map()
    groups: list[dict[str, Any]] = []
    for row in PERSON_KEEP_MAP:
        keep = _node_snapshot(session, row["keep"])
        drops = [_node_snapshot(session, eid) for eid in row["drop"]]
        missing = [
            eid
            for eid, snap in zip(row["drop"], drops, strict=True)
            if snap is None
        ]
        if keep is None:
            missing = [row["keep"], *missing]
        ok = True
        reasons: list[str] = []
        if keep is None or any(d is None for d in drops):
            ok = False
            reasons.append("missing_node")
        else:
            if "Person" not in keep["labels"]:
                ok = False
                reasons.append("keep_not_person")
            if keep["id"] != row["id"]:
                ok = False
                reasons.append("keep_id_mismatch")
            for drop in drops:
                assert drop is not None
                if "Person" not in drop["labels"]:
                    ok = False
                    reasons.append("drop_not_person")
                if drop["id"] != row["id"]:
                    ok = False
                    reasons.append("drop_id_mismatch")
        shared = []
        if keep is not None:
            for drop_eid, drop in zip(row["drop"], drops, strict=True):
                if drop is None:
                    continue
                shared.append(
                    {"drop": drop_eid, "shared_neighbors": _shared_neighbors(session, row["keep"], drop_eid)}
                )
        groups.append(
            {
                "id": row["id"],
                "keep": row["keep"],
                "drop": list(row["drop"]),
                "keep_node": keep,
                "drop_nodes": drops,
                "shared": shared,
                "authorized": ok,
                "reasons": reasons,
            }
        )
    parked = session.run(
        """
        MATCH (p:Person)
        WHERE p.id IN $ids
        RETURN p.id AS id, elementId(p) AS element_id, COUNT { (p)--() } AS deg
        ORDER BY id, element_id
        """,
        ids=list(PARKED_PERSON_IDS),
    )
    return {
        "phase": "person-clones",
        "groups": groups,
        "parked": [dict(r) for r in parked],
        "apply_ready": all(g["authorized"] for g in groups),
    }


def _copy_field_if_null(session, keep: str, drop: str, field: str) -> bool:
    if field not in PROP_COPY_FIELDS:
        raise ValueError(f"forbidden_prop_copy:{field}")
    rec = session.run(
        f"""
        MATCH (keep) WHERE elementId(keep) = $keep
        MATCH (drop) WHERE elementId(drop) = $drop
        WITH keep, drop
        WHERE keep.{field} IS NULL AND drop.{field} IS NOT NULL
        SET keep.{field} = drop.{field}
        RETURN keep.{field} IS NOT NULL AS copied
        """,
        keep=keep,
        drop=drop,
    ).single()
    return bool(rec and rec["copied"])


def _existing_rel(session, start: str, end: str, rel_type: str) -> bool:
    t = sanitize_rel_type(rel_type)
    rec = session.run(
        f"""
        MATCH (a)-[r:{t}]->(b)
        WHERE elementId(a) = $start AND elementId(b) = $end
        RETURN count(r) AS n
        """,
        start=start,
        end=end,
    ).single()
    return bool(rec and int(rec["n"]) > 0)


def rewire_and_remove(session, keep: str, drop: str) -> dict[str, int]:
    stats = {"rewired": 0, "skipped_existing": 0, "deleted_to_keep": 0, "copied_fields": 0}
    for field in PROP_COPY_FIELDS:
        if _copy_field_if_null(session, keep, drop, field):
            stats["copied_fields"] += 1
    rels = list(
        session.run(
            """
            MATCH (drop)-[r]-(other)
            WHERE elementId(drop) = $drop
            RETURN elementId(r) AS rid,
                   type(r) AS t,
                   elementId(startNode(r)) AS start,
                   elementId(endNode(r)) AS end,
                   properties(r) AS props
            """,
            drop=drop,
        )
    )
    for rec in rels:
        t = sanitize_rel_type(rec["t"])
        start, end = rec["start"], rec["end"]
        if start == keep or end == keep:
            session.run(
                "MATCH ()-[r]-() WHERE elementId(r) = $rid DELETE r",
                rid=rec["rid"],
            )
            stats["deleted_to_keep"] += 1
            continue
        new_start = keep if start == drop else start
        new_end = keep if end == drop else end
        if _existing_rel(session, new_start, new_end, t):
            stats["skipped_existing"] += 1
        else:
            props = dict(rec["props"] or {})
            props.pop("embedding", None)
            session.run(
                f"""
                MATCH (a) WHERE elementId(a) = $start
                MATCH (b) WHERE elementId(b) = $end
                CREATE (a)-[r:{t}]->(b)
                SET r = $props
                """,
                start=new_start,
                end=new_end,
                props=props,
            )
            stats["rewired"] += 1
        session.run(
            "MATCH ()-[r]-() WHERE elementId(r) = $rid DELETE r",
            rid=rec["rid"],
        )
    session.run(
        "MATCH (d) WHERE elementId(d) = $drop DETACH DELETE d",
        drop=drop,
    )
    return stats


def apply_person_clones(session, plan: dict[str, Any]) -> dict[str, Any]:
    if not plan.get("apply_ready"):
        raise SystemExit("person-clones plan is not apply_ready")

    def _work(tx):
        applied: list[dict[str, Any]] = []
        for group in plan["groups"]:
            keep = group["keep"]
            for drop in group["drop"]:
                stats = rewire_and_remove(tx, keep, drop)
                applied.append({"id": group["id"], "keep": keep, "drop": drop, **stats})
        return applied

    applied = session.execute_write(_work)
    return {"phase": "person-clones", "applied": applied}



def _id_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _public_journal_id(journal_id: str) -> str:
    if journal_id in PARKED_JOURNAL_IDS:
        return journal_id
    return f"hash:{_id_hash(journal_id)}"


def _primary_chain_eids(session) -> set[str]:
    rows = session.run(
        """
        MATCH (head) WHERE elementId(head) = $head
        OPTIONAL MATCH (head)-[:FOLLOWS*0..]->(down)
        OPTIONAL MATCH (up)-[:FOLLOWS*0..]->(head)
        WITH collect(DISTINCT elementId(down)) + collect(DISTINCT elementId(up)) AS eids
        UNWIND eids AS eid
        WITH eid WHERE eid IS NOT NULL
        RETURN collect(DISTINCT eid) AS eids
        """,
        head=HEAD_EID,
    ).single()
    return set(rows["eids"] if rows else [])


def _fork_parent_eids(session) -> set[str]:
    rows = session.run(
        """
        MATCH (child:JournalEntry)-[:FOLLOWS]->(parent:JournalEntry)
        WITH parent, count(child) AS n
        WHERE n > 1
        RETURN collect(elementId(parent)) AS eids
        """
    ).single()
    return set(rows["eids"] if rows else [])


def _journal_node_stats(session, eids: list[str]) -> list[dict[str, Any]]:
    rows = session.run(
        """
        UNWIND $eids AS eid
        MATCH (j) WHERE elementId(j) = eid
        RETURN elementId(j) AS element_id,
               COUNT { (j)--() } AS deg,
               coalesce(j.timestamp, j.entry_date, j.created_at) IS NOT NULL AS has_ts,
               EXISTS { MATCH (j)-[:FOLLOWS]-() } AS has_follows
        """,
        eids=eids,
    )
    return [dict(r) for r in rows]


def plan_journal_same_id(session) -> dict[str, Any]:
    chain = _primary_chain_eids(session)
    fork_parents = _fork_parent_eids(session)
    groups_raw = list(
        session.run(
            """
            MATCH (j:JournalEntry)
            WHERE j.id IS :: STRING AND trim(j.id) <> ''
            WITH j.id AS id, collect(elementId(j)) AS element_ids, count(*) AS n
            WHERE n > 1
            RETURN id, n, element_ids
            ORDER BY n DESC, id
            """
        )
    )
    apply_groups: list[dict[str, Any]] = []
    parked: list[dict[str, Any]] = []
    for raw in groups_raw:
        jid = raw["id"]
        eids = list(raw["element_ids"])
        stats = {s["element_id"]: s for s in _journal_node_stats(session, eids)}
        reasons: list[str] = []
        if jid in PARKED_JOURNAL_IDS:
            reasons.append("parked_twin_tip")
        if any(eid in fork_parents for eid in eids):
            reasons.append("follows_fork_parent")
        if any(stats.get(eid, {}).get("has_follows") for eid in eids):
            reasons.append("has_follows")
        if any(eid in PROTECTED_JOURNAL_EIDS for eid in eids):
            reasons.append("protected_chain_node")
        public = {
            "id": _public_journal_id(jid),
            "n": int(raw["n"]),
            "element_ids": eids,
        }
        if reasons:
            parked.append({**public, "reason": reasons})
            continue
        ranked = []
        for eid in eids:
            s = stats[eid]
            ranked.append(
                (
                    1 if eid in chain else 0,
                    int(s["deg"]),
                    1 if s["has_ts"] else 0,
                    eid,
                )
            )
        ranked.sort(reverse=True)
        keep = ranked[0][3]
        keep_reason = (
            "primary_head_chain"
            if ranked[0][0]
            else ("higher_deg" if ranked[0][1] > ranked[1][1] else "has_timestamp")
        )
        drops = [eid for eid in eids if eid != keep]
        if keep in PROTECTED_JOURNAL_EIDS or any(d in PROTECTED_JOURNAL_EIDS for d in drops):
            parked.append({**public, "reason": ["protected_chain_node"]})
            continue
        apply_groups.append(
            {
                "id": _public_journal_id(jid),
                "keep": keep,
                "drop": drops,
                "keep_reason": keep_reason,
                "n": int(raw["n"]),
            }
        )
    return {
        "phase": "journal-same-id",
        "groups": len(groups_raw),
        "nodes": sum(int(r["n"]) for r in groups_raw),
        "apply_groups": apply_groups,
        "parked_groups": parked,
        "apply_n": len(apply_groups),
        "parked_n": len(parked),
        "apply_ready": True,
        "keep_rule": "primary_head_chain else higher_deg else has_timestamp",
    }


def apply_journal_same_id(session, plan: dict[str, Any]) -> dict[str, Any]:
    def _work(tx):
        applied = []
        for group in plan["apply_groups"]:
            for drop in group["drop"]:
                if drop in PROTECTED_JOURNAL_EIDS or group["keep"] in PROTECTED_JOURNAL_EIDS:
                    raise SystemExit("refusing to mutate protected journal eids")
                stats = rewire_and_remove(tx, group["keep"], drop)
                applied.append({"id": group["id"], "keep": group["keep"], "drop": drop, **stats})
        return applied

    applied = session.execute_write(_work)
    return {"phase": "journal-same-id", "applied": applied, "parked_n": plan["parked_n"]}


def plan_topic_ci(session) -> dict[str, Any]:
    rows = list(
        session.run(
            """
            MATCH (t:Topic)
            WHERE t.name IS :: STRING
              AND NOT t.name IS :: LIST<ANY>
              AND trim(t.name) <> ''
            WITH toLower(t.name) AS key,
                 collect({
                   elementId: elementId(t),
                   deg: COUNT { (t)--() },
                   has_id: t.id IS :: STRING AND trim(t.id) <> ''
                 }) AS nodes,
                 count(*) AS n
            WHERE n > 1
            RETURN key, n, nodes
            ORDER BY n DESC
            """
        )
    )
    apply_groups = []
    for raw in rows:
        nodes = list(raw["nodes"])
        nodes.sort(key=lambda n: (int(n["deg"]), 1 if n["has_id"] else 0, n["elementId"]), reverse=True)
        keep = nodes[0]
        drops = [n["elementId"] for n in nodes[1:]]
        apply_groups.append(
            {
                "key_hash": _id_hash(raw["key"]),
                "n": int(raw["n"]),
                "keep": keep["elementId"],
                "drop": drops,
                "copy_id_from": next((n["elementId"] for n in nodes[1:] if n["has_id"] and not keep["has_id"]), None),
            }
        )
    return {
        "phase": "topic-ci",
        "groups": len(apply_groups),
        "nodes": sum(g["n"] for g in apply_groups),
        "apply_groups": apply_groups,
        "apply_ready": True,
    }


def _copy_id_if_null(tx, keep: str, drop: str) -> bool:
    rec = tx.run(
        """
        MATCH (keep) WHERE elementId(keep) = $keep
        MATCH (drop) WHERE elementId(drop) = $drop
        WITH keep, drop
        WHERE (keep.id IS NULL OR (keep.id IS :: STRING AND trim(keep.id) = ''))
          AND drop.id IS :: STRING AND trim(drop.id) <> ''
        SET keep.id = drop.id
        RETURN keep.id IS NOT NULL AS copied
        """,
        keep=keep,
        drop=drop,
    ).single()
    return bool(rec and rec["copied"])


def apply_topic_ci(session, plan: dict[str, Any]) -> dict[str, Any]:
    def _work(tx):
        applied = []
        for group in plan["apply_groups"]:
            copied = False
            for drop in group["drop"]:
                if _copy_id_if_null(tx, group["keep"], drop):
                    copied = True
                stats = rewire_and_remove(tx, group["keep"], drop)
                applied.append({"key_hash": group["key_hash"], "keep": group["keep"], "drop": drop, "copied_id": copied, **stats})
        return applied

    return {"phase": "topic-ci", "applied": session.execute_write(_work)}


def plan_orphan_states(session) -> dict[str, Any]:
    rec = session.run(
        """
        MATCH (s:State)
        WITH count(s) AS total,
             sum(CASE WHEN NOT EXISTS { MATCH (s)--() } THEN 1 ELSE 0 END) AS orphans
        RETURN total, orphans
        """
    ).single()
    return {
        "phase": "orphan-states",
        "total": int(rec["total"]),
        "orphans": int(rec["orphans"]),
        "apply_ready": int(rec["orphans"]) >= 0,
    }


def apply_orphan_states(session, plan: dict[str, Any]) -> dict[str, Any]:
    def _work(tx):
        rec = tx.run(
            """
            MATCH (s:State)
            WHERE NOT EXISTS { MATCH (s)--() }
            WITH collect(elementId(s)) AS eids
            MATCH (s:State)
            WHERE elementId(s) IN eids
            DETACH DELETE s
            RETURN size(eids) AS deleted
            """
        ).single()
        return int(rec["deleted"]) if rec else 0

    deleted = session.execute_write(_work)
    post = plan_orphan_states(session)
    return {"phase": "orphan-states", "deleted": deleted, "post": post}


def plan_backfill_ids(session) -> dict[str, Any]:
    rows = list(
        session.run(
            """
            MATCH (n)
            WHERE (n:Person OR n:Topic)
              AND (n.id IS NULL OR (n.id IS :: STRING AND trim(n.id) = ''))
              AND n.name IS :: STRING
              AND NOT n.name IS :: LIST<ANY>
              AND trim(n.name) <> ''
            WITH labels(n) AS labels, n.name AS name, collect(elementId(n)) AS missing
            MATCH (donor)
            WHERE (donor:Person OR donor:Topic)
              AND donor.name IS :: STRING
              AND NOT donor.name IS :: LIST<ANY>
              AND donor.name = name
              AND donor.id IS :: STRING AND trim(donor.id) <> ''
              AND any(l IN labels WHERE l IN labels(donor))
            WITH labels, name, missing, collect(DISTINCT donor.id) AS donor_ids, collect(elementId(donor)) AS donors
            WHERE size(donor_ids) = 1
            RETURN labels, size(name) AS name_len, missing, donor_ids[0] AS donor_id, donors
            """
        )
    )
    copies = []
    for raw in rows:
        labels = [l for l in raw["labels"] if l in {"Person", "Topic"}]
        if "Person" in labels and raw["donor_id"] in FORBIDDEN_PERSON_MERGE_IDS:
            continue
        if "Person" in labels and raw["donor_id"] in PARKED_PERSON_IDS:
            continue
        for eid in raw["missing"]:
            copies.append(
                {
                    "labels": labels,
                    "target": eid,
                    "donor_id": raw["donor_id"],
                    "name_len": int(raw["name_len"]),
                }
            )
    return {
        "phase": "backfill-ids",
        "copies": copies,
        "n": len(copies),
        "apply_ready": True,
        "rule": "copy_existing_stable_id_only",
    }


def apply_backfill_ids(session, plan: dict[str, Any]) -> dict[str, Any]:
    def _work(tx):
        done = 0
        for row in plan["copies"]:
            rec = tx.run(
                """
                MATCH (n) WHERE elementId(n) = $eid
                WITH n
                WHERE n.id IS NULL OR (n.id IS :: STRING AND trim(n.id) = '')
                SET n.id = $donor_id
                RETURN n.id AS id
                """,
                eid=row["target"],
                donor_id=row["donor_id"],
            ).single()
            if rec and rec["id"] == row["donor_id"]:
                done += 1
        return done

    return {"phase": "backfill-ids", "copied": session.execute_write(_work), "planned": plan["n"]}


def plan_other_phase(session, phase: str) -> dict[str, Any]:
    if phase == "journal-same-id":
        return plan_journal_same_id(session)
    if phase == "topic-ci":
        return plan_topic_ci(session)
    if phase == "orphan-states":
        return plan_orphan_states(session)
    if phase == "backfill-ids":
        return plan_backfill_ids(session)
    raise SystemExit(f"unknown_phase:{phase}")


def run_phase(phase: str, *, do_apply: bool) -> dict[str, Any]:
    driver, database = _driver()
    planners = {
        "person-clones": plan_person_clones,
        "journal-same-id": plan_journal_same_id,
        "topic-ci": plan_topic_ci,
        "orphan-states": plan_orphan_states,
        "backfill-ids": plan_backfill_ids,
    }
    appliers = {
        "person-clones": apply_person_clones,
        "journal-same-id": apply_journal_same_id,
        "topic-ci": apply_topic_ci,
        "orphan-states": apply_orphan_states,
        "backfill-ids": apply_backfill_ids,
    }
    try:
        with driver.session(database=database) as session:
            plan = planners[phase](session)
            if not do_apply:
                return plan
            confirm(f"Apply typed heal phase {phase}.", expected=CONFIRM_TOKENS[phase])
            applied = appliers[phase](session, plan)
            if phase != "person-clones":
                applied["post_plan"] = planners[phase](session)
            else:
                applied["post_plan"] = plan_person_clones(session)
            return applied
    finally:
        driver.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    for name in ("plan", "apply"):
        p = sub.add_parser(name)
        p.add_argument(
            "--phase",
            default="person-clones",
            choices=(
                "person-clones",
                "journal-same-id",
                "topic-ci",
                "orphan-states",
                "backfill-ids",
            ),
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    refuse_unattended_flags(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "plan"
    phase = getattr(args, "phase", "person-clones")
    report = run_phase(phase, do_apply=(command == "apply"))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
