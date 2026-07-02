//
//  HeadlinesTests.swift
//  HeadlinesTests
//
//  Created by Paul Broome on 31/01/2026.
//

import XCTest
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
    }

    /// Requirement 2: a fresh (idle) player is not playing → the transport shows the
    /// PLAY icon. `isPlaying` is the single definition both the in-app icon and the
    /// lock-screen playback rate derive from.
    @MainActor
    func testFreshPlayerReportsNotPlaying() {
        let player = BulletinPlayer()
        XCTAssertFalse(player.isPlaying)
    }

    // MARK: - Loader stage pacing (even, fixed-timer, decoupled from backend progress)

    /// Stages 1→3 advance one per `stageSeconds` purely from elapsed time — no backend
    /// input — and the last (4th) stage holds. Stage 1 can never outlast its quarter.
    func testLoaderStagesAdvanceEvenlyOnTimer() {
        let s = LoaderTiming.stageSeconds
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: 0),       0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 0.5), 0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 1.5), 1)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 2.5), 2)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: s * 3.5), 3)
    }

    /// The bug this guards: a slow (5–6s) manifest must NOT keep the loader on stage 1.
    /// stageIndex depends only on elapsed, so at any large elapsed it is the LAST (dwell)
    /// stage — never stage 1 — and stage 1 ends exactly at its first quarter boundary.
    func testLoaderStage1NeverFreezes() {
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: LoaderTiming.stageSeconds - 0.01), 0)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: LoaderTiming.stageSeconds + 0.01), 1)
        // 6s of a slow manifest → we're on the dwell stage, not frozen on stage 1.
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: 6.0), LoaderTiming.visibleStages - 1)
        XCTAssertEqual(LoaderTiming.stageIndex(elapsed: 60.0), LoaderTiming.visibleStages - 1)
    }

    /// The determinate bar is elapsed-driven too: even quarters, and it never sits at
    /// 100% in silence (audio starting dismisses the loader).
    func testLoaderBarFillEvenQuartersAndSubOne() {
        let s = LoaderTiming.stageSeconds
        XCTAssertEqual(LoaderTiming.barFill(elapsed: 0),     0.00, accuracy: 0.001)
        XCTAssertEqual(LoaderTiming.barFill(elapsed: s * 1), 0.25, accuracy: 0.02)
        XCTAssertEqual(LoaderTiming.barFill(elapsed: s * 2), 0.50, accuracy: 0.02)
        XCTAssertEqual(LoaderTiming.barFill(elapsed: s * 3), 0.75, accuracy: 0.02)
        XCTAssertLessThanOrEqual(LoaderTiming.barFill(elapsed: 100), 0.97)
    }

    /// The fast/cached-path floor lets all four stages get their equal quarter.
    func testLoaderMinDwellCoversAllStages() {
        XCTAssertEqual(LoaderTiming.visibleStages, 4)
        XCTAssertEqual(LoaderTiming.minLoaderSeconds,
                       LoaderTiming.stageSeconds * Double(LoaderTiming.visibleStages),
                       accuracy: 0.001)
    }
}
