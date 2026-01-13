from sqlalchemy import Column, DateTime, Integer, String, Text, func
from core.platform.db.models.base import Base


class ScriptOutput(Base):
    __tablename__ = "outputs"
    __table_args__ = {"schema": "scripts"}

    id = Column(Integer, primary_key=True)
    feed_id = Column(Integer, nullable=False, index=True)
    variant = Column(String, nullable=False, server_default="default")
    content = Column(Text, nullable=False)
    status = Column(String, nullable=False, server_default="built")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)