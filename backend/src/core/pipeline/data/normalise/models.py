from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from core.platform.db.models.base import Base


class NormalisationArticle(Base):
    __tablename__ = "normalisation_articles"
    __table_args__ = {"schema": "data"}

    id = Column(Integer, primary_key=True)

    ingestion_run_id = Column(
        Integer,
        ForeignKey("data.ingestion_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    source = Column(String, nullable=False)

    status = Column(String, nullable=False, server_default="normalised")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)