from sqlalchemy.orm import Session
from core.pipeline.data.normalise.models import NormalisationArticle


class NormalisationArticleRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        ingestion_run_id: int,
        title: str,
        url: str,
        source: str,
    ) -> NormalisationArticle:
        row = NormalisationArticle(
            ingestion_run_id=ingestion_run_id,
            title=title,
            url=url,
            source=source,
        )
        self.db.add(row)
        self.db.flush()
        return row