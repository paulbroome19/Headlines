from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func
from core.platform.db.models.base import Base


class StorySummary(Base):
    __tablename__ = "story_summaries"
    __table_args__ = {"schema": "data"}

    id = Column(Integer, primary_key=True)
    story_id = Column(Text, nullable=False, index=True)
    ranking_run_id = Column(Integer, nullable=False, index=True)
    content_hash = Column(Text, nullable=False, server_default="")
    headline = Column(Text, nullable=False)
    summary_text = Column(Text, nullable=False)
    why_it_matters = Column(Text)
    audio_script = Column(Text)
    model = Column(String(100), nullable=False)
    summary_version = Column(Integer, nullable=False, server_default="1")
    confidence = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
