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

    // Real Home front-page data (top stories + minutes/count), fetched cheaply/read-only.
    @State private var preview: HomePreview?
    @State private var lastUpdated: Date?
    private let previewService = HomePreviewService()

    var body: some View {
        HomeView(
            userName: userName.isEmpty ? "PAUL" : userName,
            preview: preview,
            lastUpdated: lastUpdated,
            onGenerate: startBriefing,
            onProfile: { showProfile = true },
            onRefresh: { await loadPreview() }
        )
        .task { await loadPreview() }
        // Re-fetch when returning from the filters screen (selections may have changed).
        .onChange(of: showProfile) { _, presenting in
            if !presenting { Task { await loadPreview() } }
        }
        .fullScreenCover(isPresented: $showPlayer) {
            NowPlayingView(player: player, onClose: closePlayer, onRetry: startBriefing)
        }
        .fullScreenCover(isPresented: $showProfile) {
            ProfileFiltersView()
        }
    }

    /// Fetch the real top stories + briefing length for the current profile. Resolves the
    /// profile id the same way `startBriefing` does (adopting the first backend profile for
    /// pre-profile-creation users). Silent on failure — Home degrades to greeting-only.
    private func loadPreview() async {
        var pid = profileId
        if pid <= 0 {
            if let first = try? await ProfileService().fetchProfiles().first {
                pid = first.id
                profileId = first.id
            }
        }
        guard pid > 0 else { return }
        if let p = try? await previewService.fetch(profileId: pid) {
            preview = p
            lastUpdated = Date()
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
