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
    @Environment(\.scenePhase) private var scenePhase

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
        // Flush accumulated play events when the app backgrounds (fire-and-forget) so events aren't
        // lost if the user leaves mid-briefing without returning to Home.
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { player.sendSummary() }
        }
        // The flush+refresh is hooked to the cover's TEARDOWN (onDismiss → leavePlayer), NOT to each
        // exit button. Every exit — the chevron, the completion-screen HOME disc, the empty-state HOME
        // disc, a future new exit, or a programmatic dismissal — lowers `showPlayer` and therefore runs
        // the SAME detach → post → refresh exactly once. Buttons only REQUEST leaving (requestLeave);
        // they can't bypass or half-run the sequence. (Before: each button called closePlayer directly,
        // and the completion-screen Home — the exit added after the #181/#183 flush fixes — was the one
        // that didn't reliably reload Home, so the just-finished stories lingered.)
        .fullScreenCover(isPresented: $showPlayer, onDismiss: leavePlayer) {
            NowPlayingView(player: player, onClose: requestLeave, onRetry: startBriefing)
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
            // L-D: pin the tapped selection — the server serves EXACTLY what the board showed, so a
            // ranking run landing between this render and the tap can't swap the briefing.
            await player.load(profileId: pid, selectionId: preview?.selectionId)
        }
    }

    /// A player-screen exit button (chevron / completion HOME / empty-state HOME). Buttons ONLY request
    /// dismissal: stop the audio instantly (a finished `.ended` briefing is already silent, so this is a
    /// no-op there) and lower the flag. Lowering the flag drives the cover's onDismiss → leavePlayer,
    /// where the single flush+refresh actually runs — so no button carries (or can forget) that logic.
    private func requestLeave() {
        player.pause()
        showPlayer = false
    }

    /// The ONE teardown for the player screen — runs on the cover's dismissal for EVERY exit (button,
    /// programmatic, or system). Capture the play events BEFORE tearing the player down — stopSilently()
    /// nils the bulletin/profile ids and clears the event buffer, so draining after stop (the old #181
    /// bug) posted nothing. Snapshot first, stop, then POST + refetch in a Task the dismissed screen
    /// doesn't own (so it can't be cancelled mid-flight — the #183 fix). Home reloads only AFTER the POST
    /// so the just-finished briefing's consumed stories are gone.
    private func leavePlayer() {
        let summary = player.detachSummary()
        player.stopSilently()
        Task {
            await player.postSummary(summary)
            await loadPreview()
        }
    }
}
