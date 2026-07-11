//
//  HeadlinesTests.swift
//  HeadlinesTests
//
//  Created by Paul Broome on 31/01/2026.
//

import XCTest
import UIKit
import MediaPlayer
@testable import Headlines

final class HeadlinesTests: XCTestCase {

    /// Requirement 4 (regression guard): all four transport controls are ENABLED on the
    /// lock screen / Control Center after the player initialises — none greyed out.
    /// Previous was the one that regressed (`previousTrackCommand.isEnabled = false`).
    @MainActor
    func testLockScreenTransportCommandsAllEnabled() {
        _ = BulletinPlayer()   // init wires + enables the remote command center
        let cc = MPRemoteCommandCenter.shared()
        XCTAssertTrue(cc.playCommand.isEnabled,            "Play must be enabled")
        XCTAssertTrue(cc.pauseCommand.isEnabled,           "Pause must be enabled")
        XCTAssertTrue(cc.togglePlayPauseCommand.isEnabled, "Toggle (headset) must be enabled")
        XCTAssertTrue(cc.nextTrackCommand.isEnabled,       "Next must be enabled")
        XCTAssertTrue(cc.previousTrackCommand.isEnabled,   "Previous must be enabled (the regression)")
        XCTAssertTrue(cc.changePlaybackPositionCommand.isEnabled, "Lock-screen scrubbing must be enabled")
    }

    /// The displayed play/pause state is a PURE FUNCTION OF INTENT: `isPlaying` == the
    /// user's intent, so transient engine states (a skip's `.buffering` rebuild, `.stalled`)
    /// never flip the icon. A fresh (idle) player intends nothing → PLAY icon.
    @MainActor
    func testIsPlayingTracksIntent() {
        let player = BulletinPlayer()
        XCTAssertFalse(player.isPlaying)
        XCTAssertEqual(player.isPlaying, player.intendedPlaying)
    }

    // MARK: - Play-event flush exit-path (the recurring regression hole)

    /// The exit-path capture (detachSummary — what closePlayer calls on Home/back navigation) must
    /// grab accumulated events AND the wire must carry story_id (consumption is keyed on story_id,
    /// not story_hash, which drifts across bulletins). Draining is one-shot.
    @MainActor
    func testDetachSummaryCapturesEventsAndSendsStoryId() throws {
        let player = BulletinPlayer()
        XCTAssertNil(player._testDetachedSummaryJSON(), "a fresh player has nothing to flush")

        player._testSeedEvent(bulletinId: 210, profileId: 22,
                              storyHash: "560756d8909610bb", storyId: "240779", action: "skipped")

        let data = try XCTUnwrap(player._testDetachedSummaryJSON(),
                                 "detachSummary must capture the seeded event (the Home-nav flush)")
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let events = try XCTUnwrap(obj["events"] as? [[String: Any]])
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0]["story_id"] as? String, "240779",
                       "event must carry story_id — the build-45 fix (hash alone drifts and can't match)")
        XCTAssertEqual(events[0]["action"] as? String, "skipped")
        XCTAssertEqual(obj["profile_id"] as? Int, 22)

        XCTAssertNil(player._testDetachedSummaryJSON(), "drain is one-shot — no double-send")
    }

    /// Regression: skipping to a story whose audio is still synthesising (url == nil) used to hit
    /// seekToStoryUnit's pending-tap early return BEFORE the skip event was recorded, so the skip
    /// silently vanished (build-45 symptom: skips never stuck). The skip of the CURRENT story must be
    /// captured on ANY forward skip and be in the flushed payload — skip → immediate close.
    @MainActor
    func testSkipToPendingStoryStillRecordsSkip() throws {
        let player = BulletinPlayer()
        player._testConfigurePendingSkip(currentHash: "farageHash", currentStoryId: "240779")

        player.seekToStoryUnit(at: 1)   // skip forward to the still-synthesising next story (pending)

        // Immediate close → the skip of the current story (Farage) must be in the posted payload.
        let data = try XCTUnwrap(player._testDetachedSummaryJSON(),
                                 "skipping to a PENDING story must still record the skip of the current one")
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let events = try XCTUnwrap(obj["events"] as? [[String: Any]])
        XCTAssertTrue(
            events.contains { ($0["story_id"] as? String) == "240779" && ($0["action"] as? String) == "skipped" },
            "the skip of the current story (240779) must be in the flushed payload; got \(events)"
        )
    }

    /// Regression (build-47/48): the NEXT button and lock-screen next both funnel through
    /// `skip()` → `seekToStoryUnit`, but the skip was recorded off the raw `currentSegment.isStory`.
    /// When Next is pressed while a BRIDGE/transition segment is the live item (or during a nil
    /// buffering gap mid-synthesis), that gate was false and the skip vanished on BOTH the live
    /// /event and batched /summary paths — the "two clean builds, zero events" symptom. The skip
    /// must now be recorded off the LOGICAL current story unit regardless of the live segment.
    @MainActor
    func testSkipWhileBridgePlayingStillRecordsSkip() throws {
        let player = BulletinPlayer()
        player._testConfigureForwardSkip(storyCount: 2, liveIsBridge: true)   // a bridge is the live item

        player.seekToStoryUnit(at: 1)   // Next while the bridge plays; target still synthesising (pending)

        let data = try XCTUnwrap(player._testDetachedSummaryJSON(),
                                 "Next pressed while a bridge is the live segment must still record the skip")
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let events = try XCTUnwrap(obj["events"] as? [[String: Any]])
        XCTAssertTrue(
            events.contains { ($0["story_id"] as? String) == "s0" && ($0["action"] as? String) == "skipped" },
            "the departing story (s0) must be recorded skipped even though currentSegment was a bridge; got \(events)"
        )
    }

    /// A forward jump that clears SEVERAL stories at once (scrubber drag / lock-screen scrub, which
    /// also route through `seekToStoryUnit`) must record a skip for EACH story passed, not just one —
    /// otherwise the middle stories stay queued and re-appear.
    @MainActor
    func testForwardJumpPastMultipleStoriesRecordsEachSkip() throws {
        let player = BulletinPlayer()
        player._testConfigureForwardSkip(storyCount: 3, liveIsBridge: false)  // playing story 0

        player.seekToStoryUnit(at: 2)   // jump forward past stories 0 AND 1 to the (pending) 3rd

        let data = try XCTUnwrap(player._testDetachedSummaryJSON(),
                                 "a multi-story forward jump must flush a skip for each cleared story")
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let events = try XCTUnwrap(obj["events"] as? [[String: Any]])
        let skipped = Set(events.compactMap { ($0["action"] as? String) == "skipped" ? $0["story_id"] as? String : nil })
        XCTAssertTrue(skipped.isSuperset(of: ["s0", "s1"]),
                      "both cleared stories (s0, s1) must be recorded skipped; got \(events)")
    }

    /// Closing the player while a BRIDGE is the live item must still record the in-progress story as
    /// abandoned — the exit-path counterpart to the skip fix (same `currentSegment.isStory` gate).
    @MainActor
    func testCloseWhileBridgePlayingRecordsAbandoned() throws {
        let player = BulletinPlayer()
        player._testConfigureForwardSkip(storyCount: 2, liveIsBridge: true)   // playing story 0, bridge live

        // No skip — straight to close (detachSummary is what closePlayer calls).
        let data = try XCTUnwrap(player._testDetachedSummaryJSON(),
                                 "closing mid-story while a bridge plays must still capture the in-progress story")
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let events = try XCTUnwrap(obj["events"] as? [[String: Any]])
        let abandoned = try XCTUnwrap(
            events.first { ($0["story_id"] as? String) == "s0" && ($0["action"] as? String) == "abandoned" },
            "the in-progress story (s0) must be recorded abandoned even though currentSegment was a bridge; got \(events)"
        )
        // The seam seeded a STALE currentPositionPct of 0.95 (carried over from the previous story).
        // The guard must report 0 for a story whose own audio hasn't started — otherwise abandoned
        // ≥ 0.5 would false-consume a story the user closed on during its lead-in bridge.
        XCTAssertEqual(abandoned["position_pct"] as? Double, 0,
                       "abandoned during a bridge must send position 0 (→ stays queued), not the stale 0.95")
    }

    // MARK: - PR-A: identity across the tap boundary (audit §1.4 seams #1, #2)

    /// Symptom class: "tap a story in the list → a DIFFERENT story plays." A dedup reconcile
    /// renumbers `storyUnits` between render and tap; a positional tap (`playStoryUnit(at: idx)`)
    /// then plays whatever now sits at that offset. The identity tap must play the tapped story.
    @MainActor
    func testTapPlaysStoryByIdentityNotStaleOffset() {
        let p = BulletinPlayer()
        p._testConfigureStories(ids: ["A", "B", "C"])
        // reconcile renumbers the order: offset 0 no longer holds "A".
        p._testReorderStories(ids: ["C", "A", "B"])
        XCTAssertEqual(p.storyUnits[0].storyId, "C", "precondition: a stale offset-0 tap would play C")

        p.seekToStory(id: "A")   // tap the row showing story A, by identity

        XCTAssertEqual(p.storyUnits[p.currentUnitIndex].storyId, "A",
                       "tap must play the tapped story (A) by identity, not the story now at the stale offset (C)")
    }

    /// A tapped-but-pending story's buffering spinner must follow the story across a reconcile
    /// renumber (the pointer is story identity; the view compares it by identity) — not pin a stale
    /// offset (audit §1.4 seam #2). The spinner's row = the live index of `pendingTapStoryId`.
    @MainActor
    func testPendingTapPointerFollowsStoryAcrossReorder() {
        let p = BulletinPlayer()
        p._testConfigureStories(ids: ["A", "B", "C"], pending: ["C"])   // C still synthesising
        p._testSetPendingTap(id: "C")
        XCTAssertEqual(p.pendingTapStoryId, "C")
        XCTAssertEqual(p.storyUnits.firstIndex { $0.storyId == "C" }, 2, "spinner starts on C's original row")

        p._testReorderStories(ids: ["C", "A", "B"], pending: ["C"])     // reconcile moves C to the front

        XCTAssertEqual(p.pendingTapStoryId, "C", "pending intent survives the renumber by identity")
        XCTAssertEqual(p.storyUnits.firstIndex { $0.storyId == "C" }, 0, "spinner follows C to its NEW row, not offset 2")
    }

    /// If a reconcile removes the pending-tapped story entirely (deduped away), the pending intent
    /// must be dropped — the fulfil path must never play a neighbour that inherited the old offset.
    @MainActor
    func testPendingTapDroppedWhenStoryRemovedByReconcile() {
        let p = BulletinPlayer()
        p._testConfigureStories(ids: ["A", "B", "C"], pending: ["C"])
        p._testSetPendingTap(id: "C")

        p._testReorderStories(ids: ["A", "B"])   // C dropped by dedup
        p._testFulfilPendingTapIfReady()

        XCTAssertNil(p.pendingTapStoryId, "pending intent must be dropped when its story leaves the order")
        XCTAssertFalse(p.storyUnits.contains { $0.storyId == "C" }, "C is gone from the order")
    }

    // MARK: - PR-B: published atomic snapshot + identity highlights (audit §1.3 seam α)

    /// The now-playing highlight (`currentStoryId`) must track the playing story across a reconcile
    /// renumber — so the highlight bar never strands on the wrong row. Positional `currentUnitIndex`
    /// alone would point at whatever story slid into that offset.
    @MainActor
    func testCurrentHighlightResolvesByIdentityAfterReorder() {
        let p = BulletinPlayer()
        p._testConfigureStories(ids: ["A", "B", "C"])
        p.seekToStory(id: "B")                         // now playing B (offset 1)
        XCTAssertEqual(p.currentStoryId, "B")

        p._testReorderStories(ids: ["B", "C", "A"])    // reconcile moves B to offset 0

        XCTAssertEqual(p.currentUnitIndex, 0, "current index re-anchored to B's new row by identity")
        XCTAssertEqual(p.currentStoryId, "B", "highlight follows the playing story across the renumber, not the old offset")
        XCTAssertEqual(p.storyUnits.first { $0.storyId == "B" }?.identity, "B",
                       "the row's stable diffing identity is its story id, not its offset")
    }

    // MARK: - PR-C: one cursor (audit §1.1/§1.3 — the storyIndex/currentUnitIndex dual cursor)

    /// There is now exactly ONE story cursor: `currentUnitIndex`. Every current-story surface (board
    /// title, lock-screen title, highlight) derives from it via `currentStoryId` — so the old
    /// `storyIndex` (which lagged during a bridge and fed a second, dead player) can't reintroduce a
    /// board-title ≠ audio divergence. This pins that the single cursor drives the current story.
    @MainActor
    func testSingleCursorDrivesCurrentStory() {
        let p = BulletinPlayer()
        p._testConfigureStories(ids: ["A", "B", "C"])
        XCTAssertEqual(p.currentStoryId, p.storyUnits[p.currentUnitIndex].storyId, "current story derives solely from currentUnitIndex")

        p.seekToStory(id: "C")
        XCTAssertEqual(p.currentStoryId, "C")
        XCTAssertEqual(p.currentStoryId, p.storyUnits[p.currentUnitIndex].storyId,
                       "after navigation the single cursor still drives the current story — no second index to diverge")
    }

    // MARK: - PR-D: reconcile as identity diff + serialization guard (audit seam β + reconcile race)

    private func readiness(_ segs: [(index: Int, storyId: String, ready: Bool)],
                           allReady: Bool = false,
                           storyOrder: [String]? = nil,
                           omitStoryOrder: Bool = false) -> BulletinReadiness {
        let rsegs = segs.map {
            ReadinessSegment(index: $0.index, type: "story", state: $0.ready ? "ready" : "pending",
                             url: $0.ready ? "https://e/\($0.storyId).mp3" : nil,
                             durationMs: $0.ready ? 1000 : nil, storyId: $0.storyId,
                             prevStoryId: nil, nextStoryId: nil)
        }
        let order: [String]? = omitStoryOrder ? nil : (storyOrder ?? rsegs.compactMap { $0.storyId })
        return BulletinReadiness(bulletinId: 700, assembled: true, totalSegments: rsegs.count,
                                 readySegments: rsegs.filter { $0.isReady }.count, failedSegments: 0,
                                 safeToStart: true, segments: rsegs, stage: nil, progress: nil, blocked: nil,
                                 allReady: allReady, storyOrder: order, provisional: false, selectionId: nil)
    }

    /// Seam β: the readiness merge must match segments by their stable backend `.index`, not array
    /// position. With a NON-contiguous index set (B at backend index 2 but array position 1, as
    /// after a dedup reconcile removed a middle story), the old `segments[rs.index]` subscript went
    /// out of bounds and B's url was silently never filled → B stays grey forever / never plays.
    @MainActor
    func testReadinessMergeMatchesBySegmentIndexNotArrayPosition() {
        let p = BulletinPlayer()
        p._testConfigureExplicitIndices([(0, "A", true), (2, "B", false)])   // B: backend index 2, array pos 1
        XCTAssertNil(p._testStorySegmentURL(storyId: "B"), "B starts pending")

        // Both stories present (no shrink → merge path, not reconcile); B is now ready at index 2.
        p._testApplyReadiness(readiness([(index: 0, storyId: "A", ready: true), (index: 2, storyId: "B", ready: true)]))

        XCTAssertNotNil(p._testStorySegmentURL(storyId: "B"),
                        "B's url must merge by backend .index (2), not the array position the old subscript used")
        XCTAssertNotNil(p._testStorySegmentURL(storyId: "A"), "A stays filled")
    }

    /// Serialization guard: a readiness snapshot whose fetch was superseded by a queue-rebuilding
    /// gesture (navGeneration bumped mid-fetch) must be DROPPED — never applied over the new state.
    /// This is the "unguarded reconcile race" the audit flagged.
    @MainActor
    func testStaleReadinessDroppedWhenGestureRaces() {
        let p = BulletinPlayer()
        p._testConfigureExplicitIndices([(0, "A", true), (1, "B", false)])   // B pending
        let genAtFetch = p._testNavGeneration
        p._testBumpNav()   // a seek/skip lands while the readiness request is in flight

        let applied = p._testApplyReadinessIfCurrent(
            readiness([(index: 0, storyId: "A", ready: true), (index: 1, storyId: "B", ready: true)]),
            fetchGen: genAtFetch)

        XCTAssertFalse(applied, "a superseded snapshot must be dropped")
        XCTAssertNil(p._testStorySegmentURL(storyId: "B"), "the stale snapshot must NOT have filled B over the new state")

        // A snapshot whose generation is current DOES apply.
        let applied2 = p._testApplyReadinessIfCurrent(
            readiness([(index: 0, storyId: "A", ready: true), (index: 1, storyId: "B", ready: true)]),
            fetchGen: p._testNavGeneration)
        XCTAssertTrue(applied2 || p._testStorySegmentURL(storyId: "B") != nil, "a current snapshot applies")
        XCTAssertNotNil(p._testStorySegmentURL(storyId: "B"), "current snapshot filled B")
    }

    /// Watchdog re-verify: a backend stall-watchdog REAP (a segment reported `state == "failed"`)
    /// must still be recorded in `failedSegmentIdx` — keyed by the segment's backend index — so
    /// enqueue skips it rather than holding forever. Confirms the reap-surfacing path survives D's
    /// identity-merge + guard changes. (The 45s client HOLD ceiling is time-based and checked on each
    /// applied poll; the guard only defers a check by ≤1s and never resets holdStartedAt — intact.)
    @MainActor
    func testFailedReadinessSegmentIsRecordedForSkip() {
        let p = BulletinPlayer()
        p._testConfigureExplicitIndices([(0, "A", true), (1, "B", false)])
        XCTAssertEqual(p._testHoldCeilingSeconds, 45, "client hold ceiling unchanged")

        let failedForB = BulletinReadiness(
            bulletinId: 700, assembled: true, totalSegments: 2, readySegments: 1, failedSegments: 1,
            safeToStart: true,
            segments: [ReadinessSegment(index: 0, type: "story", state: "ready", url: "https://e/A.mp3", durationMs: 1000, storyId: "A", prevStoryId: nil, nextStoryId: nil),
                       ReadinessSegment(index: 1, type: "story", state: "failed", url: nil, durationMs: nil, storyId: "B", prevStoryId: nil, nextStoryId: nil)],
            stage: nil, progress: nil, blocked: nil, allReady: false, storyOrder: ["A", "B"], provisional: false, selectionId: nil)
        p._testApplyReadiness(failedForB)

        XCTAssertTrue(p._testFailedSegmentIndices.contains(1),
                      "a failed (watchdog-reaped) segment must be recorded by backend index so enqueue skips it")
    }

    // MARK: - PR-E: authoritative order + payload bridge bindings (retire inferred shrink + index bind)

    /// E: the reconcile is driven by the backend's authoritative `story_order` (an identity diff),
    /// and rebuilt transitions carry the bridge pair from the PAYLOAD — retiring both the inferred
    /// shrink heuristic and the bindBy-keyed-by-segment-index path (audit D flag).
    @MainActor
    func testReconcileUsesAuthoritativeOrderAndPayloadBridgeBinding() {
        let p = BulletinPlayer()
        p._testConfigureExplicitIndices([(0, "A", true), (1, "B", true), (2, "C", false)])   // order A,B,C
        XCTAssertEqual(p.storyUnits.compactMap { $0.storyId }, ["A", "B", "C"])

        // Authoritative order drops B (deduped) and binds the transition A→C by identity.
        let r = BulletinReadiness(
            bulletinId: 700, assembled: true, totalSegments: 3, readySegments: 1, failedSegments: 0,
            safeToStart: true,
            segments: [
                ReadinessSegment(index: 0, type: "story", state: "ready", url: "https://e/A.mp3", durationMs: 1000, storyId: "A", prevStoryId: nil, nextStoryId: nil),
                ReadinessSegment(index: 1, type: "transition", state: "pending", url: nil, durationMs: nil, storyId: nil, prevStoryId: "A", nextStoryId: "C"),
                ReadinessSegment(index: 2, type: "story", state: "pending", url: nil, durationMs: nil, storyId: "C", prevStoryId: nil, nextStoryId: nil),
            ],
            stage: nil, progress: nil, blocked: nil, allReady: false, storyOrder: ["A", "C"], provisional: false, selectionId: nil)

        p._testApplyReadiness(r)

        XCTAssertEqual(p.storyUnits.compactMap { $0.storyId }, ["A", "C"],
                       "reconciled to the authoritative order — B (deduped) dropped, by identity not inference")
        let bind = p._testSegmentBinding(atIndex: 1)
        XCTAssertEqual(bind?.prev, "A", "transition prev bound from the payload, not a stale by-index pair")
        XCTAssertEqual(bind?.next, "C", "transition next bound from the payload")
    }

    /// E backward-compat: with NO `story_order` (older backend), the reconcile still fires via the
    /// legacy story-id-set shrink heuristic — the client falls back cleanly, deploy-order-independent.
    @MainActor
    func testReconcileFallsBackToShrinkHeuristicWhenNoAuthoritativeOrder() {
        let p = BulletinPlayer()
        p._testConfigureExplicitIndices([(0, "A", true), (1, "B", true)])   // order A,B
        // Old-backend readiness: only A survives, and story_order omitted entirely.
        p._testApplyReadiness(readiness([(index: 0, storyId: "A", ready: true)], omitStoryOrder: true))
        XCTAssertEqual(p.storyUnits.compactMap { $0.storyId }, ["A"],
                       "shrink heuristic still reconciles to the surviving set when story_order is absent")
    }

    // MARK: - PR-F: the single canonical PlaybackQueue the UI consumes (audit Part 2)

    /// The player's `queue` is the ONE object the tracklist renders: ordered, identity-keyed, with
    /// each row's state (playing/consumed/ready) resolved on the item — exactly one playing row.
    @MainActor
    func testQueueProjectsOrderAndExactlyOnePlayingRow() {
        let p = BulletinPlayer()
        p._testConfigureStories(ids: ["A", "B", "C"])
        p.seekToStory(id: "B")

        let q = p.queue
        XCTAssertEqual(q.items.map { $0.storyId }, ["A", "B", "C"], "queue preserves the running order")
        XCTAssertEqual(q.items.filter { $0.isPlaying }.map { $0.storyId }, ["B"], "exactly the playing story is marked")
        XCTAssertTrue(q.items.allSatisfy { $0.isReady }, "all ready stories report ready")
    }

    /// The queue's per-row state and ORDER follow the story across a reconcile renumber — the
    /// buffering marker rides the tapped story, and the row sequence matches the reconciled order.
    @MainActor
    func testQueueBufferingAndOrderFollowIdentityAcrossReorder() {
        let p = BulletinPlayer()
        p._testConfigureStories(ids: ["A", "B", "C"], pending: ["C"])
        p._testSetPendingTap(id: "C")

        XCTAssertEqual(p.queue.items.first { $0.storyId == "C" }?.isBuffering, true, "tapped pending story buffers")
        XCTAssertEqual(p.queue.items.first { $0.storyId == "C" }?.isReady, false, "still-synthesising story is not ready")

        p._testReorderStories(ids: ["C", "A", "B"], pending: ["C"])

        XCTAssertEqual(p.queue.items.map { $0.storyId }, ["C", "A", "B"], "queue order follows the reconciled order")
        XCTAssertEqual(p.queue.items.first { $0.storyId == "C" }?.isBuffering, true, "buffering marker follows C by identity")
    }

    // MARK: - URL construction: a query in the path must stay a real "?" (the %3F 404 regression)

    /// Regression: `client.get("…/readiness?profile_id=28")` (L-C) put the "?query" into the path
    /// string; `URLComponents.path` percent-encoded "?" → %3F, so the server received
    /// "…/readiness%3Fprofile_id%3D28" → route miss → 404 on EVERY readiness poll → cold play hung
    /// then errored. buildURL must split the "?" suffix into query items so it stays a real query.
    func testBuildURLKeepsQueryAsRealQueryNotEncodedPath() throws {
        let client = APIClient(baseURL: URL(string: "https://headlines.example.com")!)

        let u = try client.buildURL(path: "data/bulletins/258/readiness?profile_id=28")
        XCTAssertEqual(u.absoluteString, "https://headlines.example.com/data/bulletins/258/readiness?profile_id=28",
                       "query must be a real '?' — not %3F baked into the path")
        XCTAssertFalse(u.absoluteString.contains("%3F"), "no percent-encoded '?' in the URL")
        XCTAssertEqual(URLComponents(url: u, resolvingAgainstBaseURL: false)?.queryItems,
                       [URLQueryItem(name: "profile_id", value: "28")], "profile_id is a query item")

        // Query-less paths and explicit queryItems still work.
        let plain = try client.buildURL(path: "data/profiles/28/home-preview")
        XCTAssertEqual(plain.absoluteString, "https://headlines.example.com/data/profiles/28/home-preview")
        XCTAssertNil(URLComponents(url: plain, resolvingAgainstBaseURL: false)?.query)
    }

    // MARK: - PR L-D: events echo the selection_id (server 409s a stale one)

    /// The flushed /summary payload must carry the play session's selection_id, so the server can
    /// reject a stale-selection flush loudly instead of applying it to a different selection's rows.
    @MainActor
    func testFlushedSummaryCarriesSelectionId() throws {
        let p = BulletinPlayer()
        p._testSetSelectionId("7418:c7a5b5bdb9ee")
        p._testSeedEvent(bulletinId: 241, profileId: 28, storyHash: "h", storyId: "240779", action: "skipped")

        let data = try XCTUnwrap(p._testDetachedSummaryJSON(), "must flush the seeded event")
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(obj["selection_id"] as? String, "7418:c7a5b5bdb9ee",
                       "the flush must echo the selection so the server can 409 a stale one")
    }

    // MARK: - PR L-C: the client gate — never start audio whose opener != the board lead

    /// The player must refuse to start playback unless the first audible story (the opener) is the
    /// board's lead (queue.items[0]). Both derive from the running order so they match by
    /// construction — this is the last-resort hard stop for the 240779≠415760 class of incident.
    @MainActor
    func testGateHoldsWhenOpenerDoesNotMatchBoardLead() {
        let p = BulletinPlayer()
        p._testConfigureStories(ids: ["A", "B", "C"])
        XCTAssertEqual(p.firstAudibleStoryId, p.queue.items.first?.storyId, "consistent: opener == board lead")
        XCTAssertTrue(p.openerMatchesQueueLead(), "no hold when they match")

        p._testForceQueueLeadMismatch()   // board lead now C, first audible segment still A

        XCTAssertNotEqual(p.firstAudibleStoryId, p.queue.items.first?.storyId, "forced divergence")
        XCTAssertFalse(p.openerMatchesQueueLead(), "gate must HOLD (never start wrong audio) when opener != board lead")
    }

    // MARK: - Loader stage pacing (brisk ~4s stages over a ~16s window; last stage dwells)

    /// Paced stages advance one per `stageSeconds` purely from elapsed time — no backend
    /// input — evenly across the ~16s window; the final stage holds (dwell).
    func testLoaderStagesAdvanceEvenlyOnTimer() {
        let s = LoaderTiming.stageSeconds
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: 0),       0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 0.5), 0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 1.5), 1)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 2.5), 2)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 3.5), 3)
        // Each paced stage spans exactly stageSeconds (~4.0s) — brisk, none dwells alone.
        XCTAssertEqual(LoaderTiming.stageSeconds, 4.0, accuracy: 0.001)
    }

    /// The bug this guards: a slow manifest must NOT strand the loader on stage 1. Only the
    /// final (dwell) stage holds; stage 1 ends exactly at its first boundary, and past the
    /// ~16s window we're on the dwell stage.
    func testLoaderStage1NeverFreezes() {
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: LoaderTiming.stageSeconds - 0.01), 0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: LoaderTiming.stageSeconds + 0.01), 1)
        // Past the expected window → the dwell (last) stage, never frozen on stage 1.
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: LoaderTiming.expectedSeconds + 1), LoaderTiming.visibleStages - 1)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: 60.0), LoaderTiming.visibleStages - 1)
    }

    /// The expected window is ~16s (4 paced stages × 4.0s), plus a dwell stage; the
    /// fast-path floor is small (dismiss on ready), not the full window.
    func testLoaderExpectedWindowAndFloor() {
        XCTAssertEqual(LoaderTiming.expectedSeconds, 16.0, accuracy: 0.001)
        XCTAssertEqual(LoaderTiming.visibleStages, 5)   // 4 paced + 1 dwell
        XCTAssertLessThan(LoaderTiming.minLoaderSeconds, LoaderTiming.expectedSeconds)
    }

    /// Fix 1: the real app icon is bundled as a standalone image set and resolves at
    /// runtime, so the lock-screen now-playing artwork is the actual icon (not the old
    /// drawn "HEADLINES" placeholder). This is what asset-catalog APP ICONS can't do.
    func testLockScreenArtworkAssetLoads() throws {
        let icon = try XCTUnwrap(UIImage(named: "LockScreenArtwork"),
                                 "Bundled lock-screen artwork (app icon) must load at runtime")
        XCTAssertEqual(icon.size.width, 1024, accuracy: 1)
        XCTAssertEqual(icon.size.height, 1024, accuracy: 1)
    }
}
