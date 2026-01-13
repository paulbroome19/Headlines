from sqlalchemy.orm import Session
from core.pipeline.feeds.models import Feed


class FeedRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, source: str = "system") -> Feed:
        row = Feed(source=source)
        self.db.add(row)
        self.db.flush()
        return row