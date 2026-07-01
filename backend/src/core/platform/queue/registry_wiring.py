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

from core.pipeline.data.summarise.events import DATA_SUMMARISE_REQUESTED, DATA_SUMMARISE_COMPLETED
from core.pipeline.data.summarise.handlers.requested import handle_summarise_requested
# handle_rank_completed (eager summarise) is intentionally NOT imported/registered —
# summarisation is lazy (bulletin-assembly time). See build_registry() below.

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
    r.register("test.event", handle_test_event)  # completes /health/test-event bus smoke-test
    r.register(DATA_INGEST_REQUESTED, handle_ingest_requested)
    r.register(DATA_NORMALISE_REQUESTED, handle_normalise_requested)
    r.register(DATA_CLUSTER_REQUESTED, handle_cluster_requested)
    r.register(DATA_CLUSTER_COMPLETED, handle_cluster_completed)
    r.register(DATA_CATEGORISE_COMPLETED, handle_categorise_completed)
    # NOTE: DATA_RANK_COMPLETED is intentionally NOT wired to summarisation.
    # Per-story summarisation (Haiku) is LAZY — it fires only at bulletin-assembly
    # time via ensure_story_summaries(candidate_ids) in routes/data.py, for the
    # stories a user's bulletin actually selects. Wiring the eager
    # rank.completed -> handle_rank_completed path here would summarise the global
    # top-20 on every ingest/ranking cascade regardless of whether anyone listens
    # — the cost the pool schedule must not incur. (rank.completed now has no
    # handler and is simply acked.) See docs/ingestion-pool-design.md Part 1.3.
    r.register(DATA_SUMMARISE_REQUESTED, handle_summarise_requested)
    r.register(DATA_SUMMARISE_COMPLETED, handle_bulletin_assembly)
    r.register(FEEDS_REQUESTED, handle_feeds_requested)
    r.register(SCRIPTS_REQUESTED, handle_scripts_requested)
    r.register(AUDIO_REQUESTED, handle_audio_requested)
    return r
