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
}
