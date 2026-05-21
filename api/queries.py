"""API 조회 계약을 구현하는 read-side query 헬퍼."""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    EventEntitySnapshot,
    EventRelationSnapshot,
    EventWorldSnapshot,
    Narration,
    SignificantEvent,
    SimulationRun,
)
from .schemas import (
    EventDetail,
    EventEntitySnapshotItem,
    EventEntitySnapshotResponse,
    EventRelationSnapshotItem,
    EventRelationSnapshotResponse,
    EventTimelineItem,
    EventWorldNodeSnapshotItem,
    EventWorldSnapshotResponse,
    NarrationDetail,
    RunDetail,
    RunEntityItem,
    RunListItem,
    TokenUsage,
    WorldEdgeItem,
    WorldNodeItem,
)


def make_token_usage(prompt_tokens: int | None, output_tokens: int | None) -> TokenUsage:
    """prompt/output token 값을 응답용 token usage 객체로 묶는다."""
    prompt = int(prompt_tokens or 0)
    output = int(output_tokens or 0)
    return TokenUsage(
        prompt_tokens=prompt,
        output_tokens=output,
        total_tokens=prompt + output,
    )


def list_runs(session: Session, limit: int = 50, offset: int = 0) -> list[RunListItem]:
    """run 목록과 event/token 집계 정보를 조회한다."""
    stats = (
        select(
            SignificantEvent.run_id.label("run_id"),
            func.count(SignificantEvent.id).label("event_count"),
            func.coalesce(func.sum(Narration.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(Narration.output_tokens), 0).label("output_tokens"),
        )
        .select_from(SignificantEvent)
        .join(Narration, Narration.event_id == SignificantEvent.id, isouter=True)
        .group_by(SignificantEvent.run_id)
        .subquery()
    )

    stmt = (
        select(
            SimulationRun,
            func.coalesce(stats.c.event_count, 0),
            func.coalesce(stats.c.prompt_tokens, 0),
            func.coalesce(stats.c.output_tokens, 0),
        )
        .outerjoin(stats, stats.c.run_id == SimulationRun.id)
        .order_by(SimulationRun.created_at.desc(), SimulationRun.id.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = session.execute(stmt).all()
    return [
        RunListItem(
            id=run.id,
            theme=run.theme,
            narrator_style=run.narrator_style,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            event_count=int(event_count or 0),
            token_usage=make_token_usage(prompt_tokens, output_tokens),
        )
        for run, event_count, prompt_tokens, output_tokens in rows
    ]


def get_run_detail(session: Session, run_id: int) -> RunDetail | None:
    """단일 run의 기본 정보, world/entity 요약, token 집계를 조회한다."""
    run = session.scalars(
        select(SimulationRun)
        .options(
            selectinload(SimulationRun.entities),
            selectinload(SimulationRun.world_nodes),
            selectinload(SimulationRun.world_edges),
        )
        .where(SimulationRun.id == run_id)
    ).one_or_none()
    if run is None:
        return None

    event_count, prompt_tokens, output_tokens = session.execute(
        select(
            func.count(SignificantEvent.id),
            func.coalesce(func.sum(Narration.prompt_tokens), 0),
            func.coalesce(func.sum(Narration.output_tokens), 0),
        )
        .select_from(SignificantEvent)
        .join(Narration, Narration.event_id == SignificantEvent.id, isouter=True)
        .where(SignificantEvent.run_id == run_id)
    ).one()

    return RunDetail(
        id=run.id,
        seed=run.seed,
        theme=run.theme,
        narrator_style=run.narrator_style,
        model=run.model,
        turn_count=run.turn_count,
        entity_count=run.entity_count,
        node_count=run.node_count,
        edge_density=run.edge_density,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        early_end=run.early_end,
        output_md_path=run.output_md_path,
        output_jsonl_path=run.output_jsonl_path,
        config=run.config,
        meta=run.meta,
        created_at=run.created_at,
        event_count=int(event_count or 0),
        token_usage=make_token_usage(prompt_tokens, output_tokens),
        entities=[
            RunEntityItem(
                name=entity.name,
                archetype=entity.archetype,
                alive=entity.alive,
                capital_node_name=entity.capital_node_name,
                personality=entity.personality,
                initial_resources=entity.initial_resources,
                final_resources=entity.final_resources,
            )
            for entity in sorted(run.entities, key=lambda item: item.name)
        ],
        world_nodes=[
            WorldNodeItem(
                name=node.name,
                initial_owner=node.initial_owner,
                final_owner=node.final_owner,
                is_capital=node.is_capital,
                resources=node.resources,
                features=node.features,
            )
            for node in sorted(run.world_nodes, key=lambda item: item.name)
        ],
        world_edges=[
            WorldEdgeItem(
                node_a=edge.node_a,
                node_b=edge.node_b,
                traversal_cost=edge.traversal_cost,
            )
            for edge in sorted(run.world_edges, key=lambda item: (item.node_a, item.node_b))
        ],
    )


def list_run_events(
    session: Session,
    run_id: int,
    event_type: str | None = None,
    actor: str | None = None,
    target_entity: str | None = None,
    target_node: str | None = None,
    turn_from: int | None = None,
    turn_to: int | None = None,
) -> list[EventTimelineItem]:
    """필터 조건에 맞는 run event timeline을 turn/event_index 순서로 조회한다."""
    stmt: Select[tuple[SignificantEvent]] = select(SignificantEvent).where(
        SignificantEvent.run_id == run_id
    )

    if event_type is not None:
        stmt = stmt.where(SignificantEvent.event_type == event_type)
    if actor is not None:
        stmt = stmt.where(SignificantEvent.actor == actor)
    if target_entity is not None:
        stmt = stmt.where(SignificantEvent.target_entity == target_entity)
    if target_node is not None:
        stmt = stmt.where(SignificantEvent.target_node == target_node)
    if turn_from is not None:
        stmt = stmt.where(SignificantEvent.turn >= turn_from)
    if turn_to is not None:
        stmt = stmt.where(SignificantEvent.turn <= turn_to)

    stmt = stmt.order_by(SignificantEvent.turn.asc(), SignificantEvent.event_index.asc())
    events = session.scalars(stmt).all()

    return [
        EventTimelineItem(
            id=event.id,
            run_id=event.run_id,
            turn=event.turn,
            event_index=event.event_index,
            event_type=event.event_type,
            actor=event.actor,
            target_entity=event.target_entity,
            target_node=event.target_node,
            description=event.description,
        )
        for event in events
    ]


def get_event_detail(session: Session, event_id: int) -> EventDetail | None:
    """event 기본 정보와 narration, raw_record를 조회한다."""
    event = session.scalars(
        select(SignificantEvent)
        .options(selectinload(SignificantEvent.narration))
        .where(SignificantEvent.id == event_id)
    ).one_or_none()
    if event is None:
        return None

    narration = None
    if event.narration is not None:
        narration = NarrationDetail.model_validate(event.narration)

    return EventDetail(
        id=event.id,
        run_id=event.run_id,
        turn=event.turn,
        event_index=event.event_index,
        event_type=event.event_type,
        actor=event.actor,
        target_entity=event.target_entity,
        target_node=event.target_node,
        description=event.description,
        metadata=event.event_metadata,
        raw_record=event.raw_record,
        created_at=event.created_at,
        narration=narration,
    )


def get_world_snapshot(session: Session, event_id: int) -> EventWorldSnapshotResponse | None:
    """event 시점의 world node snapshot과 run-level edge 목록을 조회한다."""
    run_id = session.scalar(select(SignificantEvent.run_id).where(SignificantEvent.id == event_id))
    if run_id is None:
        return None

    run = session.get(SimulationRun, run_id)
    if run is None:
        return None

    nodes = session.scalars(
        select(EventWorldSnapshot)
        .where(EventWorldSnapshot.event_id == event_id)
        .order_by(EventWorldSnapshot.node_name.asc())
    ).all()
    edges = run.world_edges

    return EventWorldSnapshotResponse(
        event_id=event_id,
        nodes=[
            EventWorldNodeSnapshotItem(
                node_name=node.node_name,
                owner=node.owner,
                is_capital=node.is_capital,
                resources=node.resources,
                features=node.features,
            )
            for node in nodes
        ],
        edges=[
            WorldEdgeItem(
                node_a=edge.node_a,
                node_b=edge.node_b,
                traversal_cost=edge.traversal_cost,
            )
            for edge in sorted(edges, key=lambda item: (item.node_a, item.node_b))
        ],
    )


def get_entity_snapshot(session: Session, event_id: int) -> EventEntitySnapshotResponse | None:
    """event 시점의 entity snapshot을 조회한다."""
    exists = session.scalar(select(SignificantEvent.id).where(SignificantEvent.id == event_id))
    if exists is None:
        return None

    entities = session.scalars(
        select(EventEntitySnapshot)
        .where(EventEntitySnapshot.event_id == event_id)
        .order_by(EventEntitySnapshot.entity_name.asc())
    ).all()

    return EventEntitySnapshotResponse(
        event_id=event_id,
        entities=[
            EventEntitySnapshotItem(
                entity_name=entity.entity_name,
                alive=entity.alive,
                resources=entity.resources,
                node_count=entity.node_count,
            )
            for entity in entities
        ],
    )


def get_relation_snapshot(session: Session, event_id: int) -> EventRelationSnapshotResponse | None:
    """event 시점의 relation snapshot을 조회한다."""
    exists = session.scalar(select(SignificantEvent.id).where(SignificantEvent.id == event_id))
    if exists is None:
        return None

    relations = session.scalars(
        select(EventRelationSnapshot)
        .where(EventRelationSnapshot.event_id == event_id)
        .order_by(EventRelationSnapshot.entity_a.asc(), EventRelationSnapshot.entity_b.asc())
    ).all()

    return EventRelationSnapshotResponse(
        event_id=event_id,
        relations=[
            EventRelationSnapshotItem(
                entity_a=relation.entity_a,
                entity_b=relation.entity_b,
                score=relation.score,
            )
            for relation in relations
        ],
    )
