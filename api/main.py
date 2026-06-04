"""저장된 simulation run을 조회하는 FastAPI 앱 및 React SPA 서비스 서빙."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware  # CORS 대응
from fastapi.staticfiles import StaticFiles          # 정적 파일 마운트
from fastapi.responses import FileResponse           # SPA 및 단일 파일 서빙 지원
from sqlalchemy.orm import Session

from .database import get_session, init_db
from .importer import import_run_from_files
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
    SimulationRequest,
    SimulationResponse,
)

SessionDep = Annotated[Session, Depends(get_session)]

# 1. FastAPI 앱 객체 생성
app = FastAPI(title="history_engine API")

# 2. CORS 미들웨어 설정 (FastAPI 객체 바로 다음에 위치)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", 
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],  
)

# =====================================================================
# CORS 설정: 프론트엔드 개발 서버(Vite: 5173 포트) 호출 허용
# =====================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite 기본 개발 포트
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """API process 상태를 확인한다."""
    return {"status": "ok"}


@app.post("/api/db/init")
def initialize_database() -> dict[str, str]:
    """MVP DB 스키마를 생성한다."""
    init_db()
    return {"status": "created"}


@app.post("/api/simulate", response_model=SimulationResponse)
def trigger_simulation(request: SimulationRequest, session: SessionDep) -> SimulationResponse:
    """시뮬레이션을 실행한 뒤 최신 JSONL 결과를 최종 스키마로 import한다."""
    try:
        venv_python = Path.cwd() / "venv" / "Scripts" / "python.exe"
        python_executable = str(venv_python) if venv_python.exists() else sys.executable

        subprocess.run(
            [python_executable, "-m", "engine.main", "--prompt-only"],
            check=True,
            cwd=Path.cwd(),
        )

        run_files = list((Path.cwd() / "runs").glob("*.jsonl"))
        if not run_files:
            raise HTTPException(status_code=500, detail="결과 파일(.jsonl)을 찾을 수 없습니다.")

        latest_jsonl = max(run_files, key=lambda path: path.stat().st_mtime)
        world_path = Path("world.json") if Path("world.json").exists() else None
        config_path = Path("config/config.toml") if Path("config/config.toml").exists() else None
        md_path = latest_jsonl.with_suffix(".md")

        run = import_run_from_files(
            session=session,
            jsonl_path=latest_jsonl,
            world_path=world_path,
            config_path=config_path,
            output_md_path=md_path if md_path.exists() else None,
        )
        session.commit()

        return SimulationResponse(
            status="success",
            message="시뮬레이션이 성공적으로 완료되었습니다.",
            run_id=run.id,
        )
    except HTTPException:
        session.rollback()
        raise
    except subprocess.CalledProcessError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"엔진 실행 중 오류 발생: {exc}") from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"알 수 없는 오류 발생: {exc}") from exc


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


# =====================================================================
# [추가] 파일 스트리밍 전용 라우트 구역 (물리 디렉토리 바인딩)
# =====================================================================

@app.get("/data/world.json")
def serve_world_json():
    """백엔드 프로젝트 루트 디렉토리(history-engine-feat-db)에 위치한 world.json 반환."""
    world_path = os.path.join(os.getcwd(), "world.json")
    if os.path.exists(world_path):
        return FileResponse(world_path, media_type="application/json")
    raise HTTPException(status_code=404, detail="world.json 파일을 루트 경로에서 찾을 수 없습니다.")


RUNS_DIR = os.path.join(os.getcwd(), "runs")
if os.path.exists(RUNS_DIR):
    # /data/runs/파일명.md 경로로 요청하면 물리 디렉토리 안의 마크다운 파일을 정적으로 서빙
    app.mount("/data/runs", StaticFiles(directory=RUNS_DIR), name="runs_history")


# =====================================================================
# 프로덕션용 빌드 자산 서빙 및 SPA 폴백 라우팅
# 중요: API 및 정적 자산 라우터들보다 '항상 가장 마지막'에 위치해야 캐치올 규칙이 방해되지 않습니다!
# =====================================================================
# api/main.py 하단부 파일 스트리밍 구역 수정

# =====================================================================
# [추가/수정] 파일 스트리밍 전용 라우트 구역 (동적 최신 마크다운 자동 매핑)
# =====================================================================

@app.get("/data/world.json")
def serve_world_json():
    """백엔드 프로젝트 루트 디렉토리에 위치한 world.json 반환."""
    world_path = os.path.join(os.getcwd(), "world.json")
    if os.path.exists(world_path):
        return FileResponse(world_path, media_type="application/json")
    raise HTTPException(status_code=404, detail="world.json 파일을 루트 경로에서 찾을 수 없습니다.")


@app.get("/data/latest-chronicle")
def serve_latest_markdown():
    """
    [핵심 추가] runs/ 폴더 내에서 수정 시간(mtime) 기준 
    가장 최근에 생성된 .md 파일을 찾아 실시간으로 프론트엔드에 스트리밍합니다.
    """
    import glob
    runs_dir = os.path.join(os.getcwd(), "runs")
    if not os.path.exists(runs_dir):
        raise HTTPException(status_code=404, detail="runs/ 디렉토리가 존재하지 않습니다.")
    
    # runs/ 폴더 안의 모든 .md 파일 검색
    md_files = glob.glob(os.path.join(runs_dir, "*.md"))
    if not md_files:
        raise HTTPException(status_code=404, detail="runs/ 폴더 내에 마크다운(.md) 파일이 없습니다. 먼저 시뮬레이션을 실행하세요.")
    
    # 가장 최근에 갱신/생성된 마크다운 파일 추출
    latest_md_path = max(md_files, key=os.path.getmtime)
    
    return FileResponse(latest_md_path, media_type="text/markdown")


RUNS_DIR = os.path.join(os.getcwd(), "runs")
if os.path.exists(RUNS_DIR):
    # 정적 디렉토리 파일 백업 서빙 활성화 유지
    app.mount("/data/runs", StaticFiles(directory=RUNS_DIR), name="runs_history")

FRONTEND_DIST_DIR = os.path.join(os.getcwd(), "frontend", "dist")

if os.path.exists(FRONTEND_DIST_DIR):
    # React 빌드의 /assets 폴더를 마운트
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST_DIR, "assets")), name="assets")

    # API 호출이 아닌 모든 브라우저 요청은 React의 index.html로 Fallback 리다이렉트
    @app.get("/{catchall:path}")
    def serve_react_app(catchall: str):
        """React SPA의 클라이언트 사이드 라우팅을 보호하기 위해 index.html을 반환한다."""
        return FileResponse(os.path.join(FRONTEND_DIST_DIR, "index.html"))
else:
    # 아직 빌드가 안 되었을 때 가이드용 기본 루트 라우트 보존
    @app.get("/")
    def read_root() -> dict[str, str]:
        """API 기본 진입점을 확인한다."""
        return {"message": "History Engine API에 오신 것을 환영합니다! (프론트엔드 dist 빌드 파일 없음)"}