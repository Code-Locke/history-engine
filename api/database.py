"""DB engine과 SQLAlchemy session 생성 헬퍼."""

from __future__ import annotations

import os
from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


load_dotenv()

DEFAULT_DATABASE_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/history_engine"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """MVP 스키마의 모든 테이블을 생성한다."""
    Base.metadata.create_all(bind=engine)


def get_session() -> Generator[Session]:
    """요청 단위 DB session을 제공하는 FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_db() -> Generator[Session]:
    """기존 API 코드와의 호환을 위한 DB session dependency alias."""
    yield from get_session()
