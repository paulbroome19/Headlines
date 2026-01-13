from sqlalchemy.orm import Session
from core.pipeline.data.ingest.models import IngestionRun


class IngestionRunRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, source: str) -> IngestionRun:
        run = IngestionRun(source=source)
        self.db.add(run)
        self.db.flush()
        return run