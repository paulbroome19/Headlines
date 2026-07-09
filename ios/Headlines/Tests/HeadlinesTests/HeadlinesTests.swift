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
