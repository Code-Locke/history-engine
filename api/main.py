"""저장된 simulation run을 조회하는 FastAPI 앱."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from .database import get_session, init_db
from .queries import (
    get_entity_snapshot,
    get_event_detail,
    get_relation_snapshot,
    get_run_detail,
    get_world_snapshot,
    list_run_events,
    list_runs,
)
from .schemas import (
    EventDetail,
    EventEntitySnapshotResponse,
    EventRelationSnapshotResponse,
    EventTimelineItem,
    EventWorldSnapshotResponse,
    RunDetail,
    RunListItem,
)


SessionDep = Annotated[Session, Depends(get_session)]

app = FastAPI(title="history_engine API")


@app.get("/health")
def health() -> dict[str, str]:
    """API process 상태를 확인한다."""
    return {"status": "ok"}


@app.post("/api/db/init")
def initialize_database() -> dict[str, str]:
    """MVP DB 스키마를 생성한다."""
    init_db()
    return {"status": "created"}


@app.get("/api/runs", response_model=list[RunListItem])
def api_list_runs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunListItem]:
    """simulation run 목록을 조회한다."""
    return list_runs(session=session, limit=limit, offset=offset)


@app.get("/api/runs/{run_id}", response_model=RunDetail)
def api_get_run(run_id: int, session: SessionDep) -> RunDetail:
    """단일 simulation run 상세 정보를 조회한다."""
    detail = get_run_detail(session=session, run_id=run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return detail


@app.get("/api/runs/{run_id}/events", response_model=list[EventTimelineItem])
def api_list_run_events(
    run_id: int,
    session: SessionDep,
    event_type: str | None = None,
    actor: str | None = None,
    target_entity: str | None = None,
    target_node: str | None = None,
    turn_from: Annotated[int | None, Query(ge=0)] = None,
    turn_to: Annotated[int | None, Query(ge=0)] = None,
) -> list[EventTimelineItem]:
    """단일 run의 event timeline을 필터링해서 조회한다."""
    detail = get_run_detail(session=session, run_id=run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")

    return list_run_events(
        session=session,
        run_id=run_id,
        event_type=event_type,
        actor=actor,
        target_entity=target_entity,
        target_node=target_node,
        turn_from=turn_from,
        turn_to=turn_to,
    )


@app.get("/api/events/{event_id}", response_model=EventDetail)
def api_get_event(event_id: int, session: SessionDep) -> EventDetail:
    """단일 event 상세 정보를 조회한다."""
    detail = get_event_detail(session=session, event_id=event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return detail


@app.get("/api/events/{event_id}/snapshot/world", response_model=EventWorldSnapshotResponse)
def api_get_world_snapshot(event_id: int, session: SessionDep) -> EventWorldSnapshotResponse:
    """event 시점의 world snapshot을 조회한다."""
    snapshot = get_world_snapshot(session=session, event_id=event_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return snapshot


@app.get("/api/events/{event_id}/snapshot/entities", response_model=EventEntitySnapshotResponse)
def api_get_entity_snapshot(event_id: int, session: SessionDep) -> EventEntitySnapshotResponse:
    """event 시점의 entity snapshot을 조회한다."""
    snapshot = get_entity_snapshot(session=session, event_id=event_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return snapshot


@app.get("/api/events/{event_id}/snapshot/relations", response_model=EventRelationSnapshotResponse)
def api_get_relation_snapshot(event_id: int, session: SessionDep) -> EventRelationSnapshotResponse:
    """event 시점의 relation snapshot을 조회한다."""
    snapshot = get_relation_snapshot(session=session, event_id=event_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return snapshot
