//
//  FiltersScreen.swift
//  Headlines
//
//  The ONE shared light filters screen — built once, wrapped twice so the
//  onboarding ("COMPLETE SETUP") and settings ("DONE") contexts are guaranteed
//  pixel-identical and can never drift apart. Only the footer CTA, the finish
//  action, the step indicator, and the initial selection differ; everything else
//  (light page, the tri-state tree, the pinned bottom action bar) is shared.
//
//  Data source: the REAL backend taxonomy (GET /data/categories) → TopicNode
//  tree. Selections persist as leaf-id JSON (see the wrappers) so the settings
//  context can rehydrate exactly what the user currently has.
//

import SwiftUI

// MARK: - Selection helpers (overlay a leaf-id set onto a freshly-loaded tree)

/// Fresh-install defaults — a couple of top groups ship pre-lit so a user who
/// taps nothing still gets a sensible briefing. These are real backend group
/// slugs; turning a group on cascades to all its descendants.
private let onboardingDefaultGroupSlugs: Set<String> = ["world", "business"]

private func leafIDs(under node: TopicNode) -> [String] {
    node.isLeaf ? [node.id] : node.children.flatMap(leafIDs(under:))
}

/// The default selected leaf-ids for a fresh onboarding tree.
func onboardingDefaultSelection(in roots: [TopicNode]) -> Set<String> {
    var out = Set<String>()
    for node in roots where onboardingDefaultGroupSlugs.contains(node.id) {
        out.formUnion(leafIDs(under: node))
    }
    return out
}

/// Apply a selected-leaf-id set onto a tree (leaves on/off; parents derive).
func applyingSelection(_ roots: [TopicNode], selected: Set<String>) -> [TopicNode] {
    roots.map { node in
        var n = node
        if n.isLeaf {
            n.isOn = selected.contains(n.id)
        } else {
            n.children = applyingSelection(n.children, selected: selected)
        }
        return n
    }
}

// MARK: - The shared screen

struct FiltersScreen: View {
    /// Footer CTA label — "COMPLETE SETUP" (onboarding) / "DONE" (settings).
    let ctaLabel: String
    /// Onboarding shows "2 OF 2" top-right; settings shows nothing up there.
    let showStepIndicator: Bool
    /// Saved leaf-ids to pre-light (settings). `nil` → apply onboarding defaults.
    let initialSelection: Set<String>?
    /// Persist the chosen leaf-ids (create/update profile, save JSON, route).
    /// Throw to surface an error and keep the user on this screen.
    let onComplete: (_ leafIDs: [String]) async throws -> Void

    @StateObject private var model = FilterTreeModel(roots: [])

    private enum LoadState: Equatable { case loading, ready, failed(String) }
    @State private var loadState: LoadState = .loading
    @State private var isSaving = false
    @State private var saveError: String?

    var body: some View {
        ZStack {
            LightColors.page.ignoresSafeArea()

            VStack(spacing: 0) {
                if showStepIndicator {
                    HStack {
                        Spacer()
                        Text("2 OF 2")
                            .font(.label(12))
                            .tracking(2)
                            .foregroundColor(LightColors.ink.opacity(0.4))
                    }
                    .padding(.horizontal, BottomActionBar.pageMargin + 4)
                    .padding(.top, 8)
                }

                content

                footer
            }
        }
        .task { await loadCategories() }
    }

    // MARK: Body content — load state drives this

    @ViewBuilder
    private var content: some View {
        switch loadState {
        case .loading:
            VStack(spacing: 12) {
                Spacer()
                ProgressView().tint(LightColors.ink.opacity(0.5))
                Text("LOADING CATEGORIES")
                    .font(.label(11))
                    .foregroundColor(LightColors.ink.opacity(0.45))
                    .tracking(2)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .failed(let msg):
            VStack(spacing: 14) {
                Spacer()
                Text("COULDN'T LOAD CATEGORIES")
                    .font(.label(13))
                    .foregroundColor(LightColors.ink.opacity(0.7))
                    .tracking(1.5)
                Text(msg)
                    .font(.label(10))
                    .foregroundColor(LightColors.ink.opacity(0.4))
                    .tracking(1)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
                Button(action: { Task { await loadCategories() } }) {
                    Text("RETRY")
                        .font(.label(13))
                        .foregroundColor(LightColors.ink)
                        .tracking(2)
                        .padding(.horizontal, 22)
                        .padding(.vertical, 10)
                        .overlay(Capsule().strokeBorder(LightColors.ink.opacity(0.4), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .padding(.top, 6)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .ready:
            ScrollView(showsIndicators: false) {
                LightFilterTreeView(model: model)
                    .padding(.bottom, 8)
            }
        }
    }

    // MARK: Pinned footer (shared action bar + a soft fade so it reads as pinned)

    private var footer: some View {
        VStack(spacing: 0) {
            // Subtle fade so scrolling tree content dissolves under the footer.
            LinearGradient(colors: [LightColors.page.opacity(0), LightColors.page],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: 16)
                .allowsHitTesting(false)

            if let saveError {
                Text(saveError)
                    .font(.label(10))
                    .foregroundColor(Color(hex: 0xC85B5B))
                    .tracking(0.5)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 24)
                    .padding(.bottom, 6)
            }

            BottomActionBar(
                title: isSaving ? "SAVING…" : ctaLabel,
                enabled: loadState == .ready && !isSaving,
                action: finish
            )
        }
        .background(LightColors.page)
    }

    // MARK: Load categories (real backend taxonomy)

    private func loadCategories() async {
        loadState = .loading
        switch await fetchTopCategories() {
        case .loaded(let groups):
            let tree = TopicNode.tree(from: groups)
            let selection = initialSelection ?? onboardingDefaultSelection(in: tree)
            model.replaceRoots(applyingSelection(tree, selected: selection))
            loadState = .ready
        case .failed(let msg):
            loadState = .failed(msg)
        }
    }

    // MARK: Finish

    private func finish() {
        guard loadState == .ready, !isSaving else { return }
        isSaving = true
        saveError = nil
        Task { @MainActor in
            do {
                try await onComplete(model.selectedLeafIDs())
                // The wrapper routes/dismisses on success.
            } catch {
                saveError = error.localizedDescription
                isSaving = false
            }
        }
    }
}

// MARK: - Settings context wrapper (opened from the Home "P")

/// The SAME filters screen, in its settings context: pre-loads the user's
/// current selections, footer CTA "DONE", saves the edited selections back to
/// the profile and returns to Home. No step indicator.
struct ProfileFiltersView: View {
    var service: ProfileServicing = ProfileService()

    @AppStorage("profileId")      private var profileId = 0
    @AppStorage("userName")       private var userName = ""
    @AppStorage("selectedTopics") private var selectedTopicsJSON = ""
    @Environment(\.dismiss)       private var dismiss

    private enum Stage: Equatable {
        case loading
        case ready(profile: Profile?, selection: Set<String>)
        case failed(String)
    }
    @State private var stage: Stage = .loading

    var body: some View {
        ZStack {
            LightColors.page.ignoresSafeArea()
            switch stage {
            case .loading:
                VStack(spacing: 12) {
                    ProgressView().tint(LightColors.ink.opacity(0.5))
                    Text("LOADING YOUR FILTERS")
                        .font(.label(11)).tracking(2)
                        .foregroundColor(LightColors.ink.opacity(0.45))
                }

            case .failed(let msg):
                VStack(spacing: 14) {
                    Text("COULDN'T LOAD YOUR FILTERS")
                        .font(.label(13)).tracking(1.5)
                        .foregroundColor(LightColors.ink.opacity(0.7))
                    Text(msg)
                        .font(.label(10)).tracking(1)
                        .foregroundColor(LightColors.ink.opacity(0.4))
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                    Button(action: { Task { await load() } }) {
                        Text("RETRY").font(.label(13)).tracking(2)
                            .foregroundColor(LightColors.ink)
                            .padding(.horizontal, 22).padding(.vertical, 10)
                            .overlay(Capsule().strokeBorder(LightColors.ink.opacity(0.4), lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    Button(action: { dismiss() }) {
                        Text("CANCEL").font(.label(11)).tracking(2)
                            .foregroundColor(LightColors.ink.opacity(0.5))
                    }
                    .buttonStyle(.plain).padding(.top, 4)
                }

            case .ready(let profile, let selection):
                FiltersScreen(
                    ctaLabel: "DONE",
                    showStepIndicator: false,
                    initialSelection: selection,
                    onComplete: { leafIDs in
                        try await save(leafIDs, profile: profile)
                    }
                )
            }
        }
        .task { if stage == .loading { await load() } }
    }

    /// Load the user's current profile so we can show — and preserve — what they
    /// already have. Falls back to the locally-persisted leaf-id JSON if the
    /// profile can't be read, so the screen still opens with the right state.
    private func load() async {
        stage = .loading
        do {
            let profiles = try await service.fetchProfiles()
            let profile = profiles.first(where: { $0.id == profileId }) ?? profiles.first
            let selection = Set(profile?.includeCategories ?? decodedSavedTopics())
            stage = .ready(profile: profile, selection: selection)
        } catch {
            // Offline / backend down — still let them edit against saved JSON.
            stage = .ready(profile: nil, selection: Set(decodedSavedTopics()))
        }
    }

    private func decodedSavedTopics() -> [String] {
        guard let data = selectedTopicsJSON.data(using: .utf8),
              let ids = try? JSONDecoder().decode([String].self, from: data) else { return [] }
        return ids
    }

    /// Save the edited selections back to the profile, persist the JSON, dismiss.
    private func save(_ leafIDs: [String], profile: Profile?) async throws {
        let include: [String]? = leafIDs.isEmpty ? nil : leafIDs.sorted()
        let trimmedName = userName.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = profile?.name ?? (trimmedName.isEmpty ? "Listener" : trimmedName)

        if let profile {
            // Preserve the user's other settings; only the categories change.
            _ = try await service.updateProfile(
                id: profile.id,
                name: name,
                maxDurationMinutes: profile.maxDurationMinutes,
                voice: profile.voice,
                includeCategories: include,
                excludeCategories: profile.excludeCategories,
                includeTopStories: profile.includeTopStories
            )
        } else {
            // No backend profile yet — create one so the edit isn't lost.
            let created = try await service.createProfile(
                name: name, maxDurationMinutes: 5, voice: nil,
                includeCategories: include, excludeCategories: nil, includeTopStories: true
            )
            profileId = created.id
        }

        persistSelection(leafIDs)
        dismiss()
    }

    private func persistSelection(_ leafIDs: [String]) {
        if let data = try? JSONEncoder().encode(leafIDs),
           let json = String(data: data, encoding: .utf8) {
            selectedTopicsJSON = json
        }
    }
}
