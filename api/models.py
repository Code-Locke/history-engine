"""history_engine MVP 데이터베이스 스키마의 SQLAlchemy 모델."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer
from sqlalchemy import String, Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """DB row 기본값에 사용할 timezone-aware UTC 시각을 반환한다."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """모든 ORM 모델이 공유하는 SQLAlchemy declarative base."""

    pass


class SimulationRun(Base):
    """시뮬레이션 실행 1회를 나타내는 최상위 run row."""

    __tablename__ = "simulation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    theme: Mapped[str] = mapped_column(String, nullable=False)
    narrator_style: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    turn_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edge_density: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    early_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    output_md_path: Mapped[str | None] = mapped_column(String, nullable=True)
    output_jsonl_path: Mapped[str | None] = mapped_column(String, nullable=True)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    entities: Mapped[list[RunEntity]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    world_nodes: Mapped[list[WorldNode]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    world_edges: Mapped[list[WorldEdge]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    events: Mapped[list[SignificantEvent]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="SignificantEvent.event_index",
    )

    __table_args__ = (
        Index("ix_simulation_runs_created_at", "created_at"),
        Index("ix_simulation_runs_status", "status"),
        Index("ix_simulation_runs_theme", "theme"),
    )


class RunEntity(Base):
    """특정 run에 등장한 entity의 시작/최종 요약 정보."""

    __tablename__ = "run_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    archetype: Mapped[str | None] = mapped_column(String, nullable=True)
    alive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    capital_node_name: Mapped[str | None] = mapped_column(String, nullable=True)
    personality: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    initial_resources: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    final_resources: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    run: Mapped[SimulationRun] = relationship(back_populates="entities")

    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_run_entities_run_id_name"),
        Index("ix_run_entities_run_id_name", "run_id", "name"),
    )


class WorldNode(Base):
    """특정 run의 world node 기본 정보와 시작/최종 소유자 요약."""

    __tablename__ = "world_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    initial_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    final_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    is_capital: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resources: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    features: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)

    run: Mapped[SimulationRun] = relationship(back_populates="world_nodes")

    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_world_nodes_run_id_name"),
        Index("ix_world_nodes_run_id_name", "run_id", "name"),
        Index("ix_world_nodes_run_id_final_owner", "run_id", "final_owner"),
    )


class WorldEdge(Base):
    """특정 run의 무방향 world graph edge."""

    __tablename__ = "world_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_a: Mapped[str] = mapped_column(String, nullable=False)
    node_b: Mapped[str] = mapped_column(String, nullable=False)
    traversal_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    run: Mapped[SimulationRun] = relationship(back_populates="world_edges")

    __table_args__ = (
        UniqueConstraint("run_id", "node_a", "node_b", name="uq_world_edges_run_id_nodes"),
        CheckConstraint("node_a <= node_b", name="ck_world_edges_node_order"),
    )


class SignificantEvent(Base):
    """JSONL 한 줄에 대응하는 significant event row."""

    __tablename__ = "significant_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    target_entity: Mapped[str | None] = mapped_column(String, nullable=True)
    target_node: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )
    raw_record: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    run: Mapped[SimulationRun] = relationship(back_populates="events")
    narration: Mapped[Narration | None] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        uselist=False,
    )
    world_snapshots: Mapped[list[EventWorldSnapshot]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )
    entity_snapshots: Mapped[list[EventEntitySnapshot]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )
    relation_snapshots: Mapped[list[EventRelationSnapshot]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_significant_events_run_id_turn", "run_id", "turn"),
        Index("ix_significant_events_run_id_event_index", "run_id", "event_index"),
        Index("ix_significant_events_run_id_event_type", "run_id", "event_type"),
        Index("ix_significant_events_run_id_actor", "run_id", "actor"),
        Index("ix_significant_events_run_id_target_entity", "run_id", "target_entity"),
        Index("ix_significant_events_run_id_target_node", "run_id", "target_node"),
    )


class Narration(Base):
    """significant event에 1:1로 연결되는 LLM narration과 token 사용량."""

    __tablename__ = "narrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("significant_events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    narrator_style: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    narration_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    narration_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    event: Mapped[SignificantEvent] = relationship(back_populates="narration")

    __table_args__ = (
        Index("ix_narrations_event_id", "event_id"),
    )


class EventWorldSnapshot(Base):
    """특정 event 시점의 world node 상태 snapshot."""

    __tablename__ = "event_world_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("significant_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_name: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    is_capital: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resources: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    features: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)

    event: Mapped[SignificantEvent] = relationship(back_populates="world_snapshots")

    __table_args__ = (
        UniqueConstraint("event_id", "node_name", name="uq_event_world_snapshots_event_node"),
        Index("ix_event_world_snapshots_event_id_owner", "event_id", "owner"),
    )


class EventEntitySnapshot(Base):
    """특정 event 시점의 entity 상태 snapshot."""

    __tablename__ = "event_entity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("significant_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_name: Mapped[str] = mapped_column(String, nullable=False)
    alive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    resources: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    event: Mapped[SignificantEvent] = relationship(back_populates="entity_snapshots")

    __table_args__ = (
        UniqueConstraint("event_id", "entity_name", name="uq_event_entity_snapshots_event_entity"),
        Index("ix_event_entity_snapshots_entity_name", "entity_name"),
    )


class EventRelationSnapshot(Base):
    """특정 event 시점의 entity 관계 점수 snapshot."""

    __tablename__ = "event_relation_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("significant_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_a: Mapped[str] = mapped_column(String, nullable=False)
    entity_b: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)

    event: Mapped[SignificantEvent] = relationship(back_populates="relation_snapshots")

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "entity_a",
            "entity_b",
            name="uq_event_relation_snapshots_event_pair",
        ),
        CheckConstraint("entity_a < entity_b", name="ck_event_relation_snapshots_entity_order"),
        Index("ix_event_relation_snapshots_entity_pair", "entity_a", "entity_b"),
    )
