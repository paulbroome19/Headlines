import Foundation

struct ManifestSegment: Decodable, Identifiable {
    let index: Int
    let type: String
    let url: String
    let durationMs: Int
    let storyHash: String?
    let storyId: String?
    let title: String?

    var id: Int { index }
    var isStory: Bool { type == "story" }
    var durationSeconds: Double { Double(durationMs) / 1000.0 }

    enum CodingKeys: String, CodingKey {
        case index, type, url, title
        case durationMs  = "duration_ms"
        case storyHash   = "story_hash"
        case storyId     = "story_id"
    }
}

struct BulletinManifest: Decodable {
    let bulletinId: Int
    let rankingRunId: Int
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

    var isReady: Bool { state == "ready" }
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

    var introReady: Bool {
        segments.first(where: { $0.type == "intro" })?.isReady ?? false
    }
    var firstStoryReady: Bool {
        segments.first(where: { $0.type == "story" })?.isReady ?? false
    }

    enum CodingKeys: String, CodingKey {
        case bulletinId     = "bulletin_id"
        case assembled
        case totalSegments  = "total_segments"
        case readySegments  = "ready_segments"
        case failedSegments = "failed_segments"
        case safeToStart    = "safe_to_start"
        case segments
    }
}

/// One navigable chapter in the bulletin: an optional transition followed by a story segment.
/// The scrubber timeline spans all story units only; wrappers (sting/intro/outro) are excluded.
struct StoryUnit: Identifiable {
    let index: Int                           // 0-based position among story units
    let transitionSegment: ManifestSegment?
    let storySegment: ManifestSegment
    let cumulativeStartSeconds: Double       // start of this unit in the story-only timeline

    var id: Int { index }
    var transitionDurationSeconds: Double {
        transitionSegment.map { Double($0.durationMs) / 1000.0 } ?? 0
    }
    var storyDurationSeconds: Double { Double(storySegment.durationMs) / 1000.0 }
    var totalDurationSeconds: Double  { transitionDurationSeconds + storyDurationSeconds }
    var storyHash: String? { storySegment.storyHash }
    var title: String?    { storySegment.title }
}
