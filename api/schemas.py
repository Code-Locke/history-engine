"""history_engine API 응답에 사용하는 Pydantic schema."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TokenUsage(BaseModel):
    """LLM token 사용량 요약."""

    prompt_tokens: int
    output_tokens: int
    total_tokens: int


class RunListItem(BaseModel):
    """run 목록 화면에 필요한 요약 응답."""

    id: int
    theme: str
    narrator_style: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    event_count: int
    token_usage: TokenUsage


class RunEntityItem(BaseModel):
    """run detail에 포함되는 entity 요약."""

    name: str
    archetype: str | None
    alive: bool | None
    capital_node_name: str | None
    personality: dict[str, Any] | None
    initial_resources: dict[str, Any] | None
    final_resources: dict[str, Any] | None


class WorldNodeItem(BaseModel):
    """run detail에 포함되는 world node 요약."""

    name: str
    initial_owner: str | None
    final_owner: str | None
    is_capital: bool
    resources: dict[str, Any] | None
    features: list[Any] | None


class WorldEdgeItem(BaseModel):
    """run detail과 snapshot 응답에 포함되는 edge 요약."""

    node_a: str
    node_b: str
    traversal_cost: float | None


class RunDetail(BaseModel):
    """단일 run detail 응답."""

    id: int
    seed: int | None
    theme: str
    narrator_style: str | None
    model: str | None
    turn_count: int | None
    entity_count: int | None
    node_count: int | None
    edge_density: float | None
    started_at: datetime | None
    finished_at: datetime | None
    status: str
    early_end: bool
    output_md_path: str | None
    output_jsonl_path: str | None
    config: dict[str, Any] | None
    meta: dict[str, Any] | None
    created_at: datetime
    event_count: int
    token_usage: TokenUsage
    entities: list[RunEntityItem]
    world_nodes: list[WorldNodeItem]
    world_edges: list[WorldEdgeItem]


class EventTimelineItem(BaseModel):
    """event timeline 목록 item."""

    id: int
    run_id: int
    turn: int
    event_index: int
    event_type: str
    actor: str | None
    target_entity: str | None
    target_node: str | None
    description: str


class NarrationDetail(BaseModel):
    """event detail에 포함되는 narration 정보."""

    narrator_style: str | None
    model: str | None
    narration_text: str | None
    prompt_tokens: int
    output_tokens: int
    narration_error: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventDetail(BaseModel):
    """단일 event detail 응답."""

    id: int
    run_id: int
    turn: int
    event_index: int
    event_type: str
    actor: str | None
    target_entity: str | None
    target_node: str | None
    description: str
    metadata: dict[str, Any] | None
    raw_record: dict[str, Any]
    created_at: datetime
    narration: NarrationDetail | None


class EventWorldNodeSnapshotItem(BaseModel):
    """event 시점의 world node snapshot item."""

    node_name: str
    owner: str | None
    is_capital: bool
    resources: dict[str, Any] | None
    features: list[Any] | None


class EventEntitySnapshotItem(BaseModel):
    """event 시점의 entity snapshot item."""

    entity_name: str
    alive: bool
    resources: dict[str, Any] | None
    node_count: int


class EventRelationSnapshotItem(BaseModel):
    """event 시점의 relation snapshot item."""

    entity_a: str
    entity_b: str
    score: float


class EventWorldSnapshotResponse(BaseModel):
    """event world snapshot 응답."""

    event_id: int
    nodes: list[EventWorldNodeSnapshotItem]
    edges: list[WorldEdgeItem]


class EventEntitySnapshotResponse(BaseModel):
    """event entity snapshot 응답."""

    event_id: int
    entities: list[EventEntitySnapshotItem]


class EventRelationSnapshotResponse(BaseModel):
    """event relation snapshot 응답."""

    event_id: int
    relations: list[EventRelationSnapshotItem]
