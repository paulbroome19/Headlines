from sqlalchemy.orm import Session
from core.pipeline.feeds.models import FeedItem


class FeedItemRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, *, feed_id: int, summarise_output_id: int, position: int) -> FeedItem:
        row = FeedItem(
            feed_id=feed_id,
            summarise_output_id=summarise_output_id,
            position=position,
        )
        self.db.add(row)
        self.db.flush()
        return row