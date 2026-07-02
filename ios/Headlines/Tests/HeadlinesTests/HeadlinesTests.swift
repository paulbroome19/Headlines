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

    // MARK: - Loader stage pacing (even ~3s stages over a ~12s window; last stage dwells)

    /// Paced stages advance one per `stageSeconds` purely from elapsed time — no backend
    /// input — evenly across the ~12s window; the final stage holds (dwell).
    func testLoaderStagesAdvanceEvenlyOnTimer() {
        let s = LoaderTiming.stageSeconds
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: 0),       0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 0.5), 0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 1.5), 1)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 2.5), 2)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 3.5), 3)
        // Each paced stage spans exactly stageSeconds (~3s) — no stage dwells 5–6s alone.
        XCTAssertEqual(LoaderTiming.stageSeconds, 3.0, accuracy: 0.001)
    }

    /// The bug this guards: a slow manifest must NOT strand the loader on stage 1. Only the
    /// final (dwell) stage holds; stage 1 ends exactly at its first boundary, and past the
    /// ~12s window we're on the dwell stage.
    func testLoaderStage1NeverFreezes() {
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: LoaderTiming.stageSeconds - 0.01), 0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: LoaderTiming.stageSeconds + 0.01), 1)
        // Past the expected window → the dwell (last) stage, never frozen on stage 1.
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: LoaderTiming.expectedSeconds + 1), LoaderTiming.visibleStages - 1)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: 60.0), LoaderTiming.visibleStages - 1)
    }

    /// The determinate bar is elapsed-driven toward the ~12s expectation (even progress);
    /// it never sits at 100% in silence.
    func testLoaderBarFillEvenAndSubOne() {
        let s = LoaderTiming.stageSeconds
        XCTAssertEqual(LoaderTiming.barFill(elapsed: 0),     0.00, accuracy: 0.001)
        XCTAssertEqual(LoaderTiming.barFill(elapsed: s * 1), 0.25, accuracy: 0.02)   // ~3s
        XCTAssertEqual(LoaderTiming.barFill(elapsed: s * 2), 0.50, accuracy: 0.02)   // ~6s
        XCTAssertEqual(LoaderTiming.barFill(elapsed: s * 3), 0.75, accuracy: 0.02)   // ~9s
        XCTAssertLessThanOrEqual(LoaderTiming.barFill(elapsed: 100), 0.97)
    }

    /// The expected window is ~12s (4 paced stages × 3s), plus a dwell stage; the fast-path
    /// floor is small (dismiss on ready), not the full 12s.
    func testLoaderExpectedWindowAndFloor() {
        XCTAssertEqual(LoaderTiming.expectedSeconds, 12.0, accuracy: 0.001)
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
