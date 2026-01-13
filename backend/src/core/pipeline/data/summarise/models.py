from sqlalchemy import Column, DateTime, Integer, String, Text, func
from core.platform.db.models.base import Base


class SummariseOutput(Base):
    __tablename__ = "summarise_outputs"
    __table_args__ = {"schema": "data"}

    id = Column(Integer, primary_key=True)

    normalisation_article_id = Column(Integer, nullable=False, index=True)

    # short/medium/long later
    variant = Column(String, nullable=False, server_default="short")

    content = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)