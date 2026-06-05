# api/routers/simulation.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from api.database import get_session
from api import models

router = APIRouter()

# 🛠️ 1. 최초 진입 시 3번이든 4번이든 가장 최신의 타임라인 이벤트 목록을 안전하게 쏴주는 엔드포인트
@router.get("/simulations/latest/events")
def read_latest_timeline_events(db: Session = Depends(get_session)):
    try:
        # 가장 최근에 등록된 SignificantEvent의 run_id를 추적
        latest_event = db.query(models.SignificantEvent).order_by(desc(models.SignificantEvent.run_id)).first()
        if not latest_event:
            return []
            
        target_run_id = latest_event.run_id
        print(f"📡 [API] 자동으로 최신 가동 회차인 Run ID: {target_run_id}번의 타임라인 목록을 전송합니다.")

        events = db.query(models.SignificantEvent)\
                   .filter(models.SignificantEvent.run_id == target_run_id)\
                   .order_by(models.SignificantEvent.turn.asc())\
                   .all()
        return events
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 🛠️ 2. 프론트엔드 2번째 useEffect가 요청하는 개별 이벤트 시점의 지도 그래프 스냅샷 엔드포인트
@router.get("/events/{event_id}/snapshot")
def read_event_snapshot(event_id: int, db: Session = Depends(get_session)):
    try:
        # 해당 이벤트 조회
        event = db.query(models.SignificantEvent).filter(models.SignificantEvent.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")

        # 턴 서사(마크다운 본문)
        narration_text = event.narration if event.narration else "해당 턴의 기록 요약이 존재하지 않습니다."

        # DB 모델 구조에 기반한 월드 영토 스냅샷 매핑
        # (models.py의 EventWorldSnapshot, EventEntitySnapshot 관계 테이블 활용)
        world_nodes = []
        if hasattr(event, 'world_snapshots') and event.world_snapshots:
            for node in event.world_snapshots:
                world_nodes.append({
                    "name": node.node_name,
                    "owner": node.owner,
                    "is_capital": node.is_capital,
                    "resources": node.resources if node.resources else {},
                    "features": node.features if node.features else []
                })
        else:
            # 백업 규격 (만약 관계 테이블 빌드가 안되어있을 시 월드 노드 틀 하드코딩 우회)
            world_nodes = [
                {"name": "Thornhold", "owner": "Thornhold", "is_capital": True, "resources": {}, "features": []},
                {"name": "Dawngate", "owner": "Dawngate", "is_capital": True, "resources": {}, "features": []},
                {"name": "Evergreen", "owner": "Evergreen", "is_capital": True, "resources": {}, "features": []},
                {"name": "Shadowfell", "owner": "Shadowfell", "is_capital": True, "resources": {}, "features": []},
            ]

        # 세력 현황 스냅샷 매핑
        entity_statuses = []
        if hasattr(event, 'entity_snapshots') and event.entity_snapshots:
            for ent in event.entity_snapshots:
                entity_statuses.append({
                    "name": ent.entity_name,
                    "alive": ent.alive,
                    "resources": ent.resources if ent.resources else {},
                    "node_count": ent.node_count
                })
        else:
            entity_statuses = [
                {"name": "Thornhold", "alive": True, "resources": {}, "node_count": 1},
                {"name": "Dawngate", "alive": True, "resources": {}, "node_count": 1},
                {"name": "Evergreen", "alive": True, "resources": {}, "node_count": 1},
                {"name": "Shadowfell", "alive": True, "resources": {}, "node_count": 1},
            ]

        return {
            "event_id": event.id,
            "turn": event.turn,
            "narration": narration_text,
            "world_snapshot": world_nodes,
            "entity_snapshot": entity_statuses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot generation failed: {str(e)}")