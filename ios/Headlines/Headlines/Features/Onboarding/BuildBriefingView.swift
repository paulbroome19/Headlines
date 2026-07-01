//
//  BuildBriefingView.swift
//  Headlines
//
//  Onboarding step 2 — the LIGHT-register filters screen in its ONBOARDING
//  context. A thin wrapper over the shared `FiltersScreen`: it supplies the
//  "COMPLETE SETUP" CTA and the onboarding finish action — create a backend
//  profile (POST /data/profiles) with the chosen categories, persist its id +
//  the leaf-id JSON, set `didOnboard`, then route Home. The visual screen (light
//  page, tri-state tree, pinned action bar) is the shared component, so this is
//  pixel-identical to the settings-context filters (ProfileFiltersView).
//
//  Struct name + `onContinue` signature are unchanged, so the launch-flow
//  routing (createProfile → buildBriefing → home) is untouched.
//

import SwiftUI

struct BuildBriefingView: View {
    var onContinue: () -> Void = {}
    /// Back to the name step (onboarding). No-op by default (e.g. previews).
    var onBack: () -> Void = {}
    var profileService: ProfileServicing = ProfileService()

    @AppStorage("userName")       private var userName = ""
    @AppStorage("profileId")      private var profileId = 0
    @AppStorage("didOnboard")     private var didOnboard = false
    @AppStorage("selectedTopics") private var selectedTopicsJSON = ""

    var body: some View {
        // Onboarding context: defaults pre-lit (initialSelection nil), step
        // indicator shown, finish creates the profile and completes first-run.
        FiltersScreen(
            chrome: .onboarding(ctaLabel: "COMPLETE SETUP", onBack: onBack),
            initialSelection: nil,
            onComplete: { leafIDs in try await createProfileAndFinish(leafIDs) }
        )
    }

    /// The leaf ids ARE the real backend category slugs (node ids == slugs), so
    /// they form a valid `includeCategories`. Only on a successful profile
    /// creation do we mark onboarding complete and route Home.
    private func createProfileAndFinish(_ leafIDs: [String]) async throws {
        let include: [String]? = leafIDs.isEmpty ? nil : leafIDs.sorted()
        let trimmedName = userName.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = trimmedName.isEmpty ? "Listener" : trimmedName

        let profile = try await profileService.createProfile(
            name: name,
            maxDurationMinutes: 5,
            voice: nil,
            includeCategories: include,
            excludeCategories: nil,
            includeTopStories: true
        )

        // Persist the backend profile id for Home/playback to generate against,
        // plus the leaf-id JSON for the settings context to rehydrate.
        profileId = profile.id
        if let data = try? JSONEncoder().encode(leafIDs),
           let json = String(data: data, encoding: .utf8) {
            selectedTopicsJSON = json
        }
        // Only mark onboarding complete AFTER the profile exists.
        didOnboard = true
        onContinue()
    }
}

// MARK: - Preview

#Preview {
    BuildBriefingView()
}
