"""저장된 simulation run을 조회하는 FastAPI 앱 및 React SPA 서비스 서빙."""

from __future__ import annotations

import os
import glob
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware  # CORS 대응 필수
from fastapi.staticfiles import StaticFiles          
from fastapi.responses import FileResponse           
from sqlalchemy.orm import Session

from .database import get_session
from api.routers import simulation  # 라우터 임포트

SessionDep = Annotated[Session, Depends(get_session)]

# 1. FastAPI 앱 인스턴스를 가장 먼저 생성
app = FastAPI(title="history_engine API")

# 2. 🔥 [중요] CORS 미들웨어 설정을 라우터 등록보다 무조건 '먼저' 수행합니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", 
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "*"  # 개발 환경에서 모든 도메인의 크로스 오리진 허용
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. 🔥 [중요] CORS 설정이 완료된 '후'에 라우터를 등록해야 정상적으로 헤더가 붙습니다.
app.include_router(simulation.router, prefix="/api")


# ═══════════════════════════════════════════════════════════════════════════
#  기존 엔드포인트 및 정적 서빙 유지
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/data/latest-chronicle")
def get_latest_chronicle():
    runs_dir = os.path.join(os.getcwd(), "runs")
    if not os.path.exists(runs_dir):
        raise HTTPException(status_code=404, detail="runs/ 디렉토리가 존재하지 않습니다.")
    
    md_files = glob.glob(os.path.join(runs_dir, "*.md"))
    if not md_files:
        raise HTTPException(status_code=404, detail="runs/ 폴더 내에 마크다운 파일이 없습니다.")
    
    latest_md_path = max(md_files, key=os.path.getmtime)
    return FileResponse(latest_md_path, media_type="text/markdown")

# 정적 자원 연결
ROOT_DIR = os.getcwd()
app.mount("/data", StaticFiles(directory=ROOT_DIR), name="root_assets")

RUNS_DIR = os.path.join(ROOT_DIR, "runs")
if os.path.exists(RUNS_DIR):
    app.mount("/data/runs", StaticFiles(directory=RUNS_DIR), name="runs_history")

FRONTEND_DIST_DIR = os.path.join(ROOT_DIR, "frontend", "dist")
if os.path.exists(FRONTEND_DIST_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST_DIR, "assets")), name="assets")

    @app.get("/{catchall:path}")
    def serve_frontend(catchall: str):
        if catchall.startswith("api") or catchall.startswith("data"):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(os.path.join(FRONTEND_DIST_DIR, "index.html"))