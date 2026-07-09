import Foundation

struct ManifestSegment: Decodable, Identifiable {
    let index: Int
    let type: String
    // STREAMING: url + durationMs are nil in the skeleton manifest (returned in ~4s, before any
    // audio exists). They arrive per-segment via /readiness as each segment synthesises and are
    // merged in (hence `var`). A nil url means "not yet synthesised" (HOLD), never "skip".
    var url: String?
    var durationMs: Int?
    let storyHash: String?
    let storyId: String?
    let title: String?
    let sources: [String]?   // top ranked outlets for this story (credibility-ordered)
    // Bridge binding (backend bridge_guard): a `transition` carries the exact ordered story PAIR
    // it was written for — it FOLLOWS prevStoryId and PREVIEWS nextStoryId. If a reorder/reconcile
    // separates it from that pair, it must not play (it would name the wrong story). nil on
    // non-transitions and on older bulletins (unbound → not validated).
    let prevStoryId: String?
    let nextStoryId: String?

    var id: Int { index }
    var isStory: Bool { type == "story" }
    var isReady: Bool { (url?.isEmpty == false) }
    var durationSeconds: Double { Double(durationMs ?? 0) / 1000.0 }

    enum CodingKeys: String, CodingKey {
        case index, type, url, title, sources
        case durationMs  = "duration_ms"
        case storyHash   = "story_hash"
        case storyId     = "story_id"
        case prevStoryId = "prev_story_id"
        case nextStoryId = "next_story_id"
    }
}

struct BulletinManifest: Decodable {
    let bulletinId: Int
    let rankingRunId: Int?    // optional — the streaming skeleton may omit it
    let segments: [ManifestSegment]

    enum CodingKeys: String, CodingKey {
        case bulletinId  = "bulletin_id"
        case rankingRunId = "ranking_run_id"
        case segments
    }
}

// MARK: - Readiness (GET /data/bulletins/{id}/readiness)

/// Per-segment audio readiness reported by the backend.
/// state ∈ "ready" | "pending" | "failed".
struct ReadinessSegment: Decodable {
    let index: Int
    let type: String
    let state: String
    // STREAMING: present once state == "ready" — the incremental source of the playable url +
    // duration (the skeleton manifest had neither), plus story_id for the Netflix-style list.
    let url: String?
    let durationMs: Int?
    let storyId: String?

    var isReady: Bool { state == "ready" }

    enum CodingKeys: String, CodingKey {
        case index, type, state, url
        case durationMs = "duration_ms"
        case storyId    = "story_id"
    }
}

/// Honest readiness signal the loader gate polls after requesting the manifest.
/// `safeToStart` = intro + first story audio are ready, so the greeting is never
/// skipped when playback begins.
struct BulletinReadiness: Decodable {
    let bulletinId: Int
    let assembled: Bool
    let totalSegments: Int
    let readySegments: Int
    let failedSegments: Int
    let safeToStart: Bool
    let segments: [ReadinessSegment]

    // Ordered generation milestones (PR #70). Optional so a pre-#70 backend still
    // decodes — the loader falls back to the milestone bools / segment counts.
    let stage: String?       // assembled → audio_generating → buffered → ready | failed
    let progress: Int?       // 0–100 hint; never the dismissal trigger (safeToStart is)
    let blocked: Bool?       // a critical segment failed → safeToStart unreachable
    let allReady: Bool?

    var introReady: Bool {
        segments.first(where: { $0.type == "intro" })?.isReady ?? false
    }
    var firstStoryReady: Bool {
        segments.first(where: { $0.type == "story" })?.isReady ?? false
    }
    var isBlocked: Bool { (blocked ?? false) || stage == "failed" }
    /// 0–1 hint from the server progress, if present.
    var progressFraction: Double? { progress.map { Double($0) / 100.0 } }

    enum CodingKeys: String, CodingKey {
        case bulletinId     = "bulletin_id"
        case assembled
        case totalSegments  = "total_segments"
        case readySegments  = "ready_segments"
        case failedSegments = "failed_segments"
        case safeToStart    = "safe_to_start"
        case segments
        case stage
        case progress
        case blocked
        case allReady       = "all_ready"
    }
}

/// One navigable chapter in the bulletin: an optional transition followed by one or more
/// consecutive story segments SHARING a story_id. The lead is split into a short opener +
/// remainder (two segments, same story_id) so playback can start on the opener; they are one
/// unit here. The scrubber timeline spans all story units only; wrappers are excluded.
struct StoryUnit: Identifiable {
    let index: Int                           // 0-based position among story units
    let transitionSegment: ManifestSegment?
    let storySegments: [ManifestSegment]     // ≥1, same story_id (opener [+ remainder])
    let cumulativeStartSeconds: Double       // start of this unit in the story-only timeline

    var id: Int { index }
    /// The unit's PRIMARY story segment — its opener/first. Carries the title, story_id, sources
    /// and the seeded story_hash (the backend seeds one user_story_state row per story, keyed to
    /// the first segment's hash), and is the seek/enqueue anchor for the unit.
    var storySegment: ManifestSegment { storySegments[0] }
    var transitionDurationSeconds: Double {
        transitionSegment.map { Double($0.durationMs ?? 0) / 1000.0 } ?? 0
    }
    var storyDurationSeconds: Double {
        storySegments.reduce(0.0) { $0 + Double($1.durationMs ?? 0) / 1000.0 }
    }
    var totalDurationSeconds: Double  { transitionDurationSeconds + storyDurationSeconds }
    var storyHash: String? { storySegment.storyHash }
    var storyId: String?  { storySegment.storyId }
    /// Stable diffing key for the UI: story identity (never the row offset), so SwiftUI reuses the
    /// right row across a dedup renumber. Falls back to a positional token only if a unit somehow
    /// has no story_id (shouldn't happen for a real story unit).
    var identity: String  { storyId ?? "unit-\(index)" }
    var title: String?    { storySegment.title }
    /// True if `segmentIndex` is any of this unit's segments (transition or a story part).
    func owns(segmentIndex: Int) -> Bool {
        transitionSegment?.index == segmentIndex || storySegments.contains { $0.index == segmentIndex }
    }
    /// The last story segment's index — the point at which the story naturally completes.
    var lastStorySegmentIndex: Int { storySegments.last?.index ?? storySegment.index }
}

// MARK: - Home front-page preview (GET /data/profiles/{id}/home-preview)

/// A single top-ranked story for the Home front page — real selection, no audio generated.
struct HomeStory: Decodable, Identifiable {
    let storyId: String
    let headline: String
    let category: String?
    let standfirst: String?      // short newspaper standfirst (nil until the story is summarised)
    let sources: [String]        // ranked outlets (BBC before a minor blog)

    var id: String { storyId }

    enum CodingKeys: String, CodingKey {
        case storyId = "story_id"
        case headline, category, standfirst, sources
    }
}

/// The Home preview: the real top stories for the profile's selected categories plus the
/// briefing's estimated minutes + story count. Cheap / read-only — does NOT generate audio.
struct HomePreview: Decodable {
    let profileId: Int
    let name: String?
    let storyCount: Int
    let totalMinutes: Int
    let stories: [HomeStory]

    enum CodingKeys: String, CodingKey {
        case profileId = "profile_id"
        case name
        case storyCount = "story_count"
        case totalMinutes = "total_minutes"
        case stories
    }
}

final class HomePreviewService {
    private let client: APIClient
    init(client: APIClient = APIClient(baseURL: AppConfig.apiBaseURL)) {
        self.client = client
    }

    func fetch(profileId: Int) async throws -> HomePreview {
        try await client.get("data/profiles/\(profileId)/home-preview")
    }
}
