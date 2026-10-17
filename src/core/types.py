from datetime import datetime
from enum import Enum
from typing import Optional, Tuple, List
from pydantic import BaseModel, Field


class SystemState(str, Enum):
    IDLE = "IDLE"
    QUERYING = "QUERYING"
    TRACKING = "TRACKING"
    REPORTING = "REPORTING"
    ERROR = "ERROR"


class BoundingBox(BaseModel):
    x1: float  # Normalized coordinates [0.0, 1.0]
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def contains_point(self, pt: Tuple[float, float], margin: float = 0.0) -> bool:
        px, py = pt
        return (self.x1 + margin <= px <= self.x2 - margin) and \
               (self.y1 + margin <= py <= self.y2 - margin)


class DetectedObject(BaseModel):
    label: str
    confidence: float
    bbox: BoundingBox
    snapshot_path: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TrackedItem(BaseModel):
    id: int
    canonical_name: str
    display_name: str
    last_bbox: BoundingBox
    spatial_description: str
    snapshot_path: str
    full_frame_path: str
    is_visible: bool
    last_seen_at: datetime


class LaserPosition(BaseModel):
    detected: bool
    pixel_coord: Optional[Tuple[int, int]] = None
    normalized_coord: Optional[Tuple[float, float]] = None
    confidence: float = 0.0


class ServoAngles(BaseModel):
    pan: int = Field(..., ge=10, le=170)
    tilt: int = Field(..., ge=0, le=45)
