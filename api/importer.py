"""시뮬레이션 JSONL 출력물을 MVP DB 스키마로 가져오는 도구."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .database import SessionLocal, init_db
from .models import (
    EventEntitySnapshot,
    EventRelationSnapshot,
    EventWorldSnapshot,
    Narration,
    RunEntity,
    SignificantEvent,
    SimulationRun,
    WorldEdge,
    WorldNode,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError as exc:
        raise ImportError("Python < 3.11 requires tomli to read TOML config files.") from exc


def utc_now() -> datetime:
    """import 처리 시점의 timezone-aware UTC 시각을 반환한다."""
    return datetime.now(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    """JSON 파일을 읽어 최상위 object로 반환한다."""
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def load_toml(path: Path | None) -> dict[str, Any] | None:
    """선택적으로 TOML 파일을 읽어 dict로 반환한다."""
    if path is None or not path.exists():
        return None
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a TOML table in {path}")
    return data


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 줄 단위 JSON object 목록으로 읽는다."""
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError(f"Line {line_no} in {path} is not a JSON object")
            records.append(data)
    if not records:
        raise ValueError(f"No JSONL records found in {path}")
    return records


def import_run_from_files(
    session: Session,
    jsonl_path: Path,
    world_path: Path | None = None,
    config_path: Path | None = None,
    output_md_path: Path | None = None,
) -> SimulationRun:
    """JSONL과 선택 입력 파일들을 읽어 하나의 simulation run으로 저장한다."""
    records = load_jsonl(jsonl_path)
    world_data = load_json(world_path) if world_path is not None else None
    config = load_toml(config_path)

    run = build_simulation_run(
        jsonl_path=jsonl_path,
        output_md_path=output_md_path,
        records=records,
        world_data=world_data,
        config=config,
    )
    session.add(run)
    session.flush()

    add_run_entities(session, run, records=records, world_data=world_data)
    add_world_nodes(session, run, records=records, world_data=world_data)
    add_world_edges(session, run, records=records, world_data=world_data)
    add_events(session, run, records=records)

    run.status = "completed"
    run.finished_at = utc_now()
    run.early_end = infer_early_end(records[-1])
    session.flush()

    return run


def build_simulation_run(
    jsonl_path: Path,
    output_md_path: Path | None,
    records: list[dict[str, Any]],
    world_data: dict[str, Any] | None,
    config: dict[str, Any] | None,
) -> SimulationRun:
    """입력 metadata와 첫/마지막 record를 기반으로 run row를 만든다."""
    meta = dict(world_data.get("meta", {})) if world_data else {}
    simulation_config = config.get("simulation", {}) if config else {}
    world_config = config.get("world", {}) if config else {}
    narrator_config = config.get("narrator", {}) if config else {}

    first_record = records[0]
    last_record = records[-1]
    fallback_theme = meta.get("theme") or "unknown"

    return SimulationRun(
        seed=coerce_int(meta.get("seed")),
        theme=str(fallback_theme),
        narrator_style=meta.get("narrator_style") or first_record.get("narrator_style"),
        model=narrator_config.get("model"),
        turn_count=coerce_int(simulation_config.get("turn_count")) or coerce_int(last_record.get("turn")),
        entity_count=coerce_int(simulation_config.get("entity_count")) or infer_entity_count(world_data, records),
        node_count=coerce_int(world_config.get("node_count")) or infer_node_count(world_data, records),
        edge_density=coerce_float(world_config.get("edge_density")),
        started_at=utc_now(),
        status="running",
        early_end=False,
        output_md_path=str(output_md_path) if output_md_path is not None else None,
        output_jsonl_path=str(jsonl_path),
        config=config,
        meta=meta or None,
    )


def add_run_entities(
    session: Session,
    run: SimulationRun,
    records: list[dict[str, Any]],
    world_data: dict[str, Any] | None,
) -> None:
    """run-level entity 요약 row를 생성한다."""
    last_entities = entities_by_name(records[-1].get("entity_snapshot", []))
    initial_entities = {
        item.get("name"): item
        for item in (world_data or {}).get("entities", [])
        if isinstance(item, dict) and item.get("name")
    }

    capital_names = initial_capitals_by_owner(world_data)
    capital_names.update(final_capitals_by_owner(records[-1]))

    names = sorted(set(initial_entities) | set(last_entities))
    for name in names:
        initial = initial_entities.get(name, {})
        final = last_entities.get(name, {})
        session.add(
            RunEntity(
                run_id=run.id,
                name=name,
                archetype=initial.get("archetype"),
                alive=final.get("alive"),
                capital_node_name=capital_names.get(name),
                personality=initial.get("personality"),
                initial_resources=initial.get("resources"),
                final_resources=final.get("resources"),
            )
        )


def add_world_nodes(
    session: Session,
    run: SimulationRun,
    records: list[dict[str, Any]],
    world_data: dict[str, Any] | None,
) -> None:
    """run-level world node 요약 row를 생성한다."""
    initial_nodes = nodes_by_name((world_data or {}).get("nodes", []))
    first_nodes = nodes_by_name(records[0].get("world_snapshot", []))
    last_nodes = nodes_by_name(records[-1].get("world_snapshot", []))

    names = sorted(set(initial_nodes) | set(first_nodes) | set(last_nodes))
    for name in names:
        initial = initial_nodes.get(name) or first_nodes.get(name, {})
        final = last_nodes.get(name, {})
        final_or_initial = final or initial
        session.add(
            WorldNode(
                run_id=run.id,
                name=name,
                initial_owner=initial.get("owner"),
                final_owner=final_or_initial.get("owner"),
                is_capital=bool(final_or_initial.get("is_capital", False)),
                resources=initial.get("resources") or final_or_initial.get("resources"),
                features=initial.get("features") or final_or_initial.get("features"),
            )
        )


def add_world_edges(
    session: Session,
    run: SimulationRun,
    records: list[dict[str, Any]],
    world_data: dict[str, Any] | None,
) -> None:
    """world JSON 또는 JSONL edge snapshot에서 run-level edge row를 생성한다."""
    raw_edges = (world_data or {}).get("edges") or records[0].get("edges_snapshot", [])
    seen: set[tuple[str, str]] = set()

    for edge in raw_edges:
        normalized = normalize_edge(edge)
        if normalized is None:
            continue
        node_a, node_b, traversal_cost = normalized
        if (node_a, node_b) in seen:
            continue
        seen.add((node_a, node_b))
        session.add(
            WorldEdge(
                run_id=run.id,
                node_a=node_a,
                node_b=node_b,
                traversal_cost=traversal_cost,
            )
        )


def add_events(session: Session, run: SimulationRun, records: list[dict[str, Any]]) -> None:
    """JSONL record를 event, narration, snapshot row들로 분해해 저장한다."""
    for event_index, record in enumerate(records):
        event = SignificantEvent(
            run_id=run.id,
            turn=coerce_int(record.get("turn")) or 0,
            event_index=event_index,
            event_type=str(record.get("event_type") or "unknown"),
            actor=record.get("actor"),
            target_entity=record.get("target_entity"),
            target_node=record.get("target_node"),
            description=str(record.get("description") or ""),
            event_metadata=record.get("metadata"),
            raw_record=record,
        )
        session.add(event)
        session.flush()

        session.add(
            Narration(
                event_id=event.id,
                narrator_style=record.get("narrator_style"),
                model=run.model,
                narration_text=record.get("narration"),
                prompt_tokens=coerce_int(record.get("prompt_tokens")) or 0,
                output_tokens=coerce_int(record.get("output_tokens")) or 0,
                narration_error=record.get("narration_error"),
            )
        )
        add_event_world_snapshots(session, event.id, record)
        add_event_entity_snapshots(session, event.id, record)
        add_event_relation_snapshots(session, event.id, record)


def add_event_world_snapshots(session: Session, event_id: int, record: dict[str, Any]) -> None:
    """한 event record의 world_snapshot을 저장한다."""
    for node in record.get("world_snapshot", []):
        if not isinstance(node, dict) or not node.get("name"):
            continue
        session.add(
            EventWorldSnapshot(
                event_id=event_id,
                node_name=node["name"],
                owner=node.get("owner"),
                is_capital=bool(node.get("is_capital", False)),
                resources=node.get("resources"),
                features=node.get("features"),
            )
        )


def add_event_entity_snapshots(session: Session, event_id: int, record: dict[str, Any]) -> None:
    """한 event record의 entity_snapshot을 저장한다."""
    for entity in record.get("entity_snapshot", []):
        if not isinstance(entity, dict) or not entity.get("name"):
            continue
        session.add(
            EventEntitySnapshot(
                event_id=event_id,
                entity_name=entity["name"],
                alive=bool(entity.get("alive", False)),
                resources=entity.get("resources"),
                node_count=coerce_int(entity.get("node_count")) or 0,
            )
        )


def add_event_relation_snapshots(session: Session, event_id: int, record: dict[str, Any]) -> None:
    """한 event record의 relations_snapshot을 pair row로 펼쳐 저장한다."""
    for entity_a, entity_b, score in flatten_relations(record.get("relations_snapshot", {})):
        session.add(
            EventRelationSnapshot(
                event_id=event_id,
                entity_a=entity_a,
                entity_b=entity_b,
                score=score,
            )
        )


def flatten_relations(raw: Any) -> list[tuple[str, str, float]]:
    """중첩 relation matrix를 중복 없는 정렬 pair 목록으로 변환한다."""
    if not isinstance(raw, dict):
        return []

    pairs: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for entity_a in sorted(raw):
        targets = raw[entity_a]
        if not isinstance(targets, dict):
            continue
        for entity_b in sorted(targets):
            if entity_a == entity_b:
                continue
            a, b = sorted_relation_pair(str(entity_a), str(entity_b))
            if (a, b) in seen:
                continue
            score = coerce_float(targets[entity_b])
            if score is None:
                continue
            seen.add((a, b))
            pairs.append((a, b, score))
    return pairs


def normalize_edge(raw: Any) -> tuple[str, str, float | None] | None:
    """edge record를 DB 제약조건에 맞는 정렬 endpoint tuple로 정규화한다."""
    if not isinstance(raw, dict):
        return None
    node_a = raw.get("node_a")
    node_b = raw.get("node_b")
    if not node_a or not node_b or node_a == node_b:
        return None
    a, b = sorted_edge_pair(str(node_a), str(node_b))
    return a, b, coerce_float(raw.get("traversal_cost"))


def sorted_edge_pair(node_a: str, node_b: str) -> tuple[str, str]:
    """무방향 edge endpoint를 안정적인 저장 순서로 정렬한다."""
    return (node_a, node_b) if node_a <= node_b else (node_b, node_a)


def sorted_relation_pair(entity_a: str, entity_b: str) -> tuple[str, str]:
    """무방향 relation pair를 자기 자신 제외 저장 순서로 정렬한다."""
    return (entity_a, entity_b) if entity_a < entity_b else (entity_b, entity_a)


def entities_by_name(raw: Any) -> dict[str, dict[str, Any]]:
    """entity dict 목록을 name 기준 lookup으로 변환한다."""
    if not isinstance(raw, list):
        return {}
    return {
        item["name"]: item
        for item in raw
        if isinstance(item, dict) and item.get("name")
    }


def nodes_by_name(raw: Any) -> dict[str, dict[str, Any]]:
    """node dict 목록을 name 기준 lookup으로 변환한다."""
    if not isinstance(raw, list):
        return {}
    return {
        item["name"]: item
        for item in raw
        if isinstance(item, dict) and item.get("name")
    }


def initial_capitals_by_owner(world_data: dict[str, Any] | None) -> dict[str, str]:
    """world JSON에서 초기 수도 node를 owner별로 추출한다."""
    result: dict[str, str] = {}
    for node in (world_data or {}).get("nodes", []):
        if not isinstance(node, dict):
            continue
        owner = node.get("owner")
        name = node.get("name")
        if owner and name and node.get("is_capital"):
            result[str(owner)] = str(name)
    return result


def final_capitals_by_owner(record: dict[str, Any]) -> dict[str, str]:
    """마지막 event snapshot에서 최종 수도 node를 owner별로 추출한다."""
    result: dict[str, str] = {}
    for node in record.get("world_snapshot", []):
        if not isinstance(node, dict):
            continue
        owner = node.get("owner")
        name = node.get("name")
        if owner and name and node.get("is_capital"):
            result[str(owner)] = str(name)
    return result


def infer_early_end(record: dict[str, Any]) -> bool:
    """마지막 entity snapshot으로 조기 종료 여부를 추론한다."""
    entity_snapshot = record.get("entity_snapshot", [])
    if not isinstance(entity_snapshot, list):
        return False
    alive_count = sum(1 for item in entity_snapshot if isinstance(item, dict) and item.get("alive"))
    return alive_count <= 1


def infer_entity_count(world_data: dict[str, Any] | None, records: list[dict[str, Any]]) -> int | None:
    """world JSON 또는 마지막 snapshot에서 entity 수를 추론한다."""
    entities = (world_data or {}).get("entities")
    if isinstance(entities, list):
        return len(entities)
    snapshot = records[-1].get("entity_snapshot")
    if isinstance(snapshot, list):
        return len(snapshot)
    return None


def infer_node_count(world_data: dict[str, Any] | None, records: list[dict[str, Any]]) -> int | None:
    """world JSON 또는 마지막 snapshot에서 node 수를 추론한다."""
    nodes = (world_data or {}).get("nodes")
    if isinstance(nodes, list):
        return len(nodes)
    snapshot = records[-1].get("world_snapshot")
    if isinstance(snapshot, list):
        return len(snapshot)
    return None


def coerce_int(value: Any) -> int | None:
    """값을 int로 변환하되 실패하면 None을 반환한다."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_float(value: Any) -> float | None:
    """값을 float로 변환하되 실패하면 None을 반환한다."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description="Import a history_engine JSONL run into the database.")
    parser.add_argument("--jsonl", type=Path, required=True, help="Path to a simulation .jsonl file.")
    parser.add_argument("--world", type=Path, default=None, help="Optional world JSON used to seed run summaries.")
    parser.add_argument("--config", type=Path, default=Path("config/config.toml"), help="Optional config TOML.")
    parser.add_argument("--md", type=Path, default=None, help="Optional generated markdown chronicle path.")
    parser.add_argument("--init-db", action="store_true", help="Create tables before importing.")
    return parser.parse_args()


def main() -> None:
    """CLI 진입점."""
    args = parse_args()
    if args.init_db:
        init_db()

    with SessionLocal() as session:
        run = import_run_from_files(
            session=session,
            jsonl_path=args.jsonl,
            world_path=args.world,
            config_path=args.config,
            output_md_path=args.md,
        )
        session.commit()
        print(run.id)


if __name__ == "__main__":
    main()
