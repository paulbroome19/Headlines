from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from core.platform.db.models.base import Base


class Feed(Base):
    __tablename__ = "feeds"
    __table_args__ = {"schema": "feeds"}

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False, server_default="system")
    status = Column(String, nullable=False, server_default="built")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FeedItem(Base):
    __tablename__ = "feed_items"
    __table_args__ = {"schema": "feeds"}

    id = Column(Integer, primary_key=True)

    feed_id = Column(
        Integer,
        ForeignKey("feeds.feeds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    summarise_output_id = Column(Integer, nullable=False, index=True)
    position = Column(Integer, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)