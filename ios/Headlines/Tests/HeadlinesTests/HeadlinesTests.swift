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
