from core.platform.queue.registry import HandlerRegistry

from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED
from core.pipeline.data.ingest.handlers.requested import handle_ingest_requested

from core.pipeline.data.normalise.events import DATA_NORMALISE_REQUESTED
from core.pipeline.data.normalise.handlers.requested import handle_normalise_requested

from core.pipeline.data.cluster.events import DATA_CLUSTER_REQUESTED, DATA_CLUSTER_COMPLETED
from core.pipeline.data.cluster.handlers.requested import handle_cluster_requested

from core.pipeline.data.categorise.events import DATA_CATEGORISE_COMPLETED
from core.pipeline.data.categorise.handlers.cluster_completed import handle_cluster_completed

from core.pipeline.ranking.events import DATA_RANK_COMPLETED
from core.pipeline.ranking.handlers.categorise_completed import handle_categorise_completed

from core.pipeline.data.summarise.events import DATA_SUMMARISE_REQUESTED, DATA_SUMMARISE_COMPLETED
from core.pipeline.data.summarise.handlers.requested import handle_summarise_requested
from core.pipeline.data.summarise.handlers.rank_completed import handle_rank_completed

from core.pipeline.data.bulletin.events import DATA_BULLETIN_ASSEMBLED
from core.pipeline.data.bulletin.handlers.summarise_completed import handle_summarise_completed as handle_bulletin_assembly

from core.pipeline.feeds.events import FEEDS_REQUESTED
from core.pipeline.feeds.handlers.requested import handle_feeds_requested

from core.pipeline.scripts.events import SCRIPTS_REQUESTED
from core.pipeline.scripts.handlers.requested import handle_scripts_requested

from core.pipeline.audio.events import AUDIO_REQUESTED
from core.pipeline.audio.handlers.requested import handle_audio_requested


def build_registry() -> HandlerRegistry:
    r = HandlerRegistry()
    r.register(DATA_INGEST_REQUESTED, handle_ingest_requested)
    r.register(DATA_NORMALISE_REQUESTED, handle_normalise_requested)
    r.register(DATA_CLUSTER_REQUESTED, handle_cluster_requested)
    r.register(DATA_CLUSTER_COMPLETED, handle_cluster_completed)
    r.register(DATA_CATEGORISE_COMPLETED, handle_categorise_completed)
    r.register(DATA_RANK_COMPLETED, handle_rank_completed)
    r.register(DATA_SUMMARISE_REQUESTED, handle_summarise_requested)
    r.register(DATA_SUMMARISE_COMPLETED, handle_bulletin_assembly)
    r.register(FEEDS_REQUESTED, handle_feeds_requested)
    r.register(SCRIPTS_REQUESTED, handle_scripts_requested)
    r.register(AUDIO_REQUESTED, handle_audio_requested)
    return r
