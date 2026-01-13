from sqlalchemy.orm import Session
from core.pipeline.scripts.models import ScriptOutput


class ScriptOutputRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, feed_id: int, content: str, variant: str = "default") -> ScriptOutput:
        row = ScriptOutput(feed_id=feed_id, content=content, variant=variant, status="built")
        self.db.add(row)
        self.db.flush()
        return row

    