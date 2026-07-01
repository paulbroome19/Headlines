from sqlalchemy import Column, DateTime, Integer, String, func
from core.platform.db.models.base import Base


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = {"schema": "data"}

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    status = Column(String, nullable=False, default="started")
    # Pool stamp (pool-aware ingestion): which (category, country) GNews pool this
    # run fetched. Null for manual/legacy ingests.
    pool_category = Column(String, nullable=True)
    pool_country = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)