//
//  HomeContainerView.swift
//  Headlines
//
//  Wires BOTH Home buttons to the real app:
//   • Play  (onGenerate) → load a bulletin for the persisted profile (POST
//     manifest) and present the now-playing screen. Back/close stops + returns.
//   • Profile (onProfile) → present the shared filters screen in its SETTINGS
//     context (ProfileFiltersView) — the SAME light tri-state tree as onboarding,
//     pre-loaded with the user's current selections, "DONE" saves + returns.
//

import SwiftUI

struct HomeContainerView: View {
    @AppStorage("userName")  private var userName = ""
    @AppStorage("profileId") private var profileId = 0

    @StateObject private var player = BulletinPlayer()
    @State private var showPlayer = false
    @State private var showProfile = false

    var body: some View {
        HomeView(
            userName: userName.isEmpty ? "PAUL" : userName,
            onGenerate: startBriefing,
            onProfile: { showProfile = true }
        )
        .fullScreenCover(isPresented: $showPlayer) {
            NowPlayingView(player: player, onClose: closePlayer, onRetry: startBriefing)
        }
        .fullScreenCover(isPresented: $showProfile) {
            ProfileFiltersView()
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
