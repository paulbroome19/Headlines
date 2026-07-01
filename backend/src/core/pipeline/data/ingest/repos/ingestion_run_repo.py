from sqlalchemy.orm import Session
from core.pipeline.data.ingest.models import IngestionRun


class IngestionRunRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        source: str,
        pool_category: str | None = None,
        pool_country: str | None = None,
    ) -> IngestionRun:
        run = IngestionRun(
            source=source,
            pool_category=pool_category,
            pool_country=pool_country,
        )
        self.db.add(run)
        self.db.flush()
        return run