from sqlalchemy import Column, Integer, String, JSON, ForeignKey
from sqlalchemy.orm import relationship
from api.database import Base

class SimulationRun(Base):
    __tablename__ = "runs"
    
    id = Column(Integer, primary_key=True, index=True)
    theme = Column(String, default="fantasy")
    narrator_style = Column(String)
    
    events = relationship("Event", back_populates="run")

class Event(Base):
    __tablename__ = "events"
    
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("runs.id"))
    turn_number = Column(Integer)
    event_type = Column(String) # 예: 'war_declared', 'elimination'
    narration = Column(String)  # LLM이 생성한 서술
    raw_data = Column(JSON)     # 안전을 위해 JSONL 데이터를 통째로 저장
    
    run = relationship("SimulationRun", back_populates="events")