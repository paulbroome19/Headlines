from core.platform.queue.registry import HandlerRegistry

from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED
from core.pipeline.data.ingest.handlers.requested import handle_ingest_requested

from core.pipeline.data.normalise.events import DATA_NORMALISE_REQUESTED
from core.pipeline.data.normalise.handlers.requested import handle_normalise_requested

from core.pipeline.data.summarise.events import DATA_SUMMARISE_REQUESTED
from core.pipeline.data.summarise.handlers.requested import handle_summarise_requested

from core.pipeline.feeds.events import FEEDS_REQUESTED
from core.pipeline.feeds.handlers.requested import handle_feeds_requested

from core.pipeline.scripts.events import SCRIPTS_REQUESTED
from core.pipeline.scripts.handlers.requested import handle_scripts_requested

from core.pipeline.audio.events import AUDIO_REQUESTED
from core.pipeline.audio.handlers.requested import handle_audio_requested

from core.pipeline.data.cluster.events import DATA_CLUSTER_REQUESTED
from core.pipeline.data.cluster.handlers.requested import handle_cluster_requested


def build_registry() -> HandlerRegistry:
    r = HandlerRegistry()
    r.register(DATA_INGEST_REQUESTED, handle_ingest_requested)
    r.register(DATA_NORMALISE_REQUESTED, handle_normalise_requested)
    r.register(DATA_CLUSTER_REQUESTED, handle_cluster_requested)
    r.register(DATA_SUMMARISE_REQUESTED, handle_summarise_requested)
    r.register(FEEDS_REQUESTED, handle_feeds_requested)
    r.register(SCRIPTS_REQUESTED, handle_scripts_requested)
    r.register(AUDIO_REQUESTED, handle_audio_requested)
    return r

