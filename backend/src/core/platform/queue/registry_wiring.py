from core.platform.queue.registry import HandlerRegistry

from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED
from core.pipeline.data.ingest.handlers.requested import handle_ingest_requested
from core.pipeline.data.ingest.handlers.test_event import handle_test_event

from core.pipeline.data.normalise.events import DATA_NORMALISE_REQUESTED
from core.pipeline.data.normalise.handlers.requested import handle_normalise_requested

from core.pipeline.data.cluster.events import DATA_CLUSTER_REQUESTED, DATA_CLUSTER_COMPLETED
from core.pipeline.data.cluster.handlers.requested import handle_cluster_requested

from core.pipeline.data.categorise.events import DATA_CATEGORISE_COMPLETED
from core.pipeline.data.categorise.handlers.cluster_completed import handle_cluster_completed

from core.pipeline.ranking.handlers.categorise_completed import handle_categorise_completed


def build_registry() -> HandlerRegistry:
    r = HandlerRegistry()
    r.register("test.event", handle_test_event)  # completes /health/test-event bus smoke-test
    r.register(DATA_INGEST_REQUESTED, handle_ingest_requested)
    r.register(DATA_NORMALISE_REQUESTED, handle_normalise_requested)
    r.register(DATA_CLUSTER_REQUESTED, handle_cluster_requested)
    r.register(DATA_CLUSTER_COMPLETED, handle_cluster_completed)
    r.register(DATA_CATEGORISE_COMPLETED, handle_categorise_completed)
    # The ingest cascade ENDS at ranking: handle_categorise_completed runs StoryRanker +
    # editorial precompute + stable ranked-list maintenance inline. Per-story summarisation and
    # bulletin assembly are LAZY — they run inline in routes/data.py for the stories a user's
    # briefing actually selects, never via events. (The dead data.rank.completed → summarise →
    # bulletin.assembled and feeds→scripts→audio chains were removed — audit §6/§7.)
    return r
