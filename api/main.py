from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
import subprocess
import os
import json 
import glob

from api.database import get_db
import api.models as models
import api.schemas as schemas

app = FastAPI(title="History Engine API")

@app.get("/")
def read_root():
    return {"message": "History Engine API에 오신 것을 환영합니다!"}

@app.get("/api/runs")
def get_runs(db: Session = Depends(get_db)):
    # 나중에 프론트엔드에 과거 시뮬레이션 목록을 보내줄 엔드포인트
    return {"status": "success", "message": "DB 연결 및 API 준비 완료!"}

# 새로 추가된 POST 엔드포인트: 시뮬레이션 트리거
@app.post("/api/simulate", response_model=schemas.SimulationResponse)
def trigger_simulation(request: schemas.SimulationRequest, db: Session = Depends(get_db)):
    try:
        # 1. DB에 새로운 시뮬레이션 Run(기록) 생성
        new_run = models.SimulationRun(
            theme=request.theme, 
            narrator_style=request.narrator_style
        )
        db.add(new_run)
        db.commit()
        db.refresh(new_run)

        # 2. 코어 엔진 실행 (과금 방지를 위해 --prompt-only 플래그 사용)
        print(f"\n[{new_run.id}번 Run] 시뮬레이션 엔진을 백그라운드에서 실행합니다...")
        
        venv_python = os.path.join(os.getcwd(), "venv", "Scripts", "python.exe")
        subprocess.run([venv_python, "-m", "engine.main", "--prompt-only"], check=True)
        
        # 3. 최신 JSONL 파일을 읽어 DB 'events' 테이블에 저장
        print("시뮬레이션 완료. 결과를 DB에 저장합니다...")
        
        # runs 폴더 안의 모든 jsonl 파일을 찾아서 가장 최근에 생성된 파일을 선택
        list_of_files = glob.glob(os.path.join(os.getcwd(), "runs", "*.jsonl"))
        if not list_of_files:
            raise HTTPException(status_code=500, detail="결과 파일(.jsonl)을 찾을 수 없습니다.")
            
        latest_file = max(list_of_files, key=os.path.getctime)
        
        events_to_insert = []
        with open(latest_file, "r", encoding="utf-8") as f:
            for line in f:
                data = json.loads(line) # 한 줄씩 JSON으로 파싱
                
                # DB Event 모델 형식에 맞게 데이터 가공
                event = models.Event(
                    run_id=new_run.id,
                    turn_number=data.get("turn", 0),
                    event_type=data.get("event", "unknown"),
                    narration=data.get("narration", ""),
                    raw_data=data # 전체 원본 데이터 보존
                )
                events_to_insert.append(event)
                
        # 한 번에 DB에 밀어 넣기 (성능 최적화)
        db.bulk_save_objects(events_to_insert)
        db.commit()
        
        print(f"{len(events_to_insert)}개의 이벤트가 DB에 성공적으로 저장되었습니다!")

        return {
            "status": "success", 
            "message": "시뮬레이션이 성공적으로 완료되었습니다.", 
            "run_id": new_run.id
        }
        

    except subprocess.CalledProcessError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"엔진 실행 중 오류 발생: {str(e)}")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"알 수 없는 오류 발생: {str(e)}")