from pydantic import BaseModel
from typing import Optional

class SimulationRequest(BaseModel):
    theme: str = "fantasy"
    narrator_style: str = "chronicle"

class SimulationResponse(BaseModel):
    status: str
    message: str
    run_id: Optional[int] = None