//
//  HomeContainerView.swift
//  Headlines
//
//  Wires BOTH Home buttons to the real app:
//   • Play  (onGenerate) → load a bulletin for the persisted profile (POST
//     manifest) and present the now-playing screen. Back/close stops + returns.
//   • Profile (onProfile) → present the profile/filter editor for the current
//     profile (the working ProfileFormView). Previously unwired (a stub), so
//     tapping the P did nothing.
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
        .sheet(isPresented: $showProfile) {
            ProfileSheet()
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

// MARK: - Profile sheet

/// Loads the user's current profile and presents the working ProfileFormView in
/// edit mode (falls back to creating one if none exists). Self-contained so the
/// Home profile button can present it directly.
private struct ProfileSheet: View {
    @AppStorage("profileId") private var profileId = 0
    @Environment(\.dismiss) private var dismiss

    private enum LoadState: Equatable {
        case loading
        case edit(Profile)
        case create
        case failed(String)
    }
    @State private var state: LoadState = .loading
    private let service = ProfileService()

    var body: some View {
        switch state {
        case .loading:
            NavigationStack {
                ProgressView("Loading profile…")
                    .navigationTitle("Profile")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            }
            .task { await load() }

        case .edit(let profile):
            ProfileFormView(mode: .edit(profile), service: service, onSaved: {})

        case .create:
            ProfileFormView(mode: .create, service: service, onSaved: {})

        case .failed(let msg):
            NavigationStack {
                VStack(spacing: 12) {
                    Text("Couldn't load your profile").font(.headline)
                    Text(msg).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    Button("Retry") { state = .loading; Task { await load() } }
                }
                .padding()
                .navigationTitle("Profile")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            }
        }
    }

    private func load() async {
        do {
            let profiles = try await service.fetchProfiles()
            if let p = profiles.first(where: { $0.id == profileId }) ?? profiles.first {
                state = .edit(p)
            } else {
                state = .create
            }
        } catch {
            state = .failed(error.localizedDescription)
        }
    }
}
