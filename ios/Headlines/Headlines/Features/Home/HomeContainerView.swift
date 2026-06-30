//
//  HomeContainerView.swift
//  Headlines
//
//  Wires Home's Play button to the real pipeline: owns a BulletinPlayer, and on
//  Play generates a bulletin for the persisted profile (POST manifest) and
//  presents the now-playing screen. Back/close stops playback and returns to Home.
//

import SwiftUI

struct HomeContainerView: View {
    @AppStorage("userName")  private var userName = ""
    @AppStorage("profileId") private var profileId = 0

    @StateObject private var player = BulletinPlayer()
    @State private var showPlayer = false

    var body: some View {
        HomeView(
            userName: userName.isEmpty ? "PAUL" : userName,
            onGenerate: startBriefing
        )
        .fullScreenCover(isPresented: $showPlayer) {
            NowPlayingView(player: player, onClose: closePlayer, onRetry: startBriefing)
        }
    }

    /// Present the player immediately (shows a loading state), then load the
    /// bulletin for the user's profile via the proven manifest path.
    private func startBriefing() {
        showPlayer = true
        Task {
            var pid = profileId
            if pid <= 0 {
                // Existing user who onboarded before profile-creation landed:
                // adopt their first backend profile so Play still works.
                if let first = try? await ProfileService().fetchProfiles().first {
                    pid = first.id
                    profileId = first.id
                }
            }
            // An invalid/zero pid hits the backend and returns 404, which the
            // player surfaces as its failed state — no fake data, no crash.
            await player.load(profileId: pid)
        }
    }

    private func closePlayer() {
        player.stop()
        showPlayer = false
    }
}
