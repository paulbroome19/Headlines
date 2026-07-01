//
//  FiltersScreen.swift
//  Headlines
//
//  The ONE shared light filters screen — built once, wrapped twice so the
//  onboarding and settings contexts share the SAME tree, light styling, toggles
//  and partial/cascade logic and can never drift apart. Only the navigation
//  CHROME differs (parameterised via `Chrome`):
//   • .onboarding — a forward step: the bottom action bar ("COMPLETE SETUP" +
//     arrow) and the "2 OF 2" step indicator.
//   • .settings — a detour: a top-left back chevron (matching playback), NO
//     bottom bar, tree fills the screen. Back = save (the same updateProfile
//     path the old "DONE" button used) + return Home.
//
//  Data source: the REAL backend taxonomy (GET /data/categories) → TopicNode
//  tree. Selections persist as leaf-id JSON (see the wrappers) so the settings
//  context can rehydrate exactly what the user currently has.
//

import SwiftUI

// MARK: - Selection helpers (overlay a leaf-id set onto a freshly-loaded tree)

/// Fresh-install default — a new user starts with TOP STORIES only (nothing else).
/// It's the highest-coverage "best across everything" front page, so it always fills
/// (the backend guarantees it). `groupSlugs` light a whole top group (cascades to all
/// its region descendants); `leafSlugs` would light individual children. Real slugs.
private let onboardingDefaultGroupSlugs: Set<String> = ["top-stories"]
private let onboardingDefaultLeafSlugs:  Set<String> = []

private func leafIDs(under node: TopicNode) -> [String] {
    node.isLeaf ? [node.id] : node.children.flatMap(leafIDs(under:))
}

/// The default selected leaf-ids for a fresh onboarding tree:
/// World, UK, Business, Technology.
func onboardingDefaultSelection(in roots: [TopicNode]) -> Set<String> {
    var out = Set<String>()
    func walk(_ nodes: [TopicNode]) {
        for node in nodes {
            if onboardingDefaultGroupSlugs.contains(node.id) {
                out.formUnion(leafIDs(under: node))   // whole group on
            } else if node.isLeaf, onboardingDefaultLeafSlugs.contains(node.id) {
                out.insert(node.id)                    // single child on
            } else {
                walk(node.children)
            }
        }
    }
    walk(roots)
    return out
}

/// Apply a selected-id set onto a tree (leaves on/off; parents derive).
/// A saved id that matches a PARENT lights that whole subtree — this both honours
/// "select the group" and gracefully migrates selections saved before the taxonomy
/// gained depth (e.g. an old `sport.football` leaf is now a parent of the teams).
func applyingSelection(_ roots: [TopicNode], selected: Set<String>) -> [TopicNode] {
    func lightSubtree(_ node: TopicNode) -> TopicNode {
        var n = node
        if n.isLeaf { n.isOn = true } else { n.children = n.children.map(lightSubtree) }
        return n
    }
    return roots.map { node in
        var n = node
        if selected.contains(n.id) {
            return lightSubtree(n)          // saved id matches this node → whole subtree on
        }
        if n.isLeaf {
            n.isOn = false
        } else {
            n.children = applyingSelection(n.children, selected: selected)
        }
        return n
    }
}

// MARK: - The shared screen

struct FiltersScreen: View {
    /// The navigation chrome — the ONLY thing that differs between contexts.
    enum Chrome {
        /// Forward setup step: bottom action bar + "2 OF 2". `onBack` (when set)
        /// shows a top-left chevron to return to the previous onboarding step (name).
        case onboarding(ctaLabel: String, onBack: (() -> Void)?)
        /// Settings detour: top-left back chevron, no footer. `onClose` is the
        /// plain return-to-Home used when leaving while there's nothing to save
        /// (still loading / failed); a ready screen saves first (see `back()`).
        case settings(onClose: () -> Void)
    }

    let chrome: Chrome
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

    private var isOnboarding: Bool {
        if case .onboarding = chrome { return true }
        return false
    }

    var body: some View {
        ZStack {
            LightColors.page.ignoresSafeArea()

            VStack(spacing: 0) {
                topChrome

                content

                // Bottom action bar — onboarding only. Settings has no footer;
                // the tree fills to the bottom and back-saves from the top.
                if isOnboarding { footer }
            }
        }
        .task { await loadCategories() }
    }

    // MARK: Top chrome (step indicator OR back chevron)

    @ViewBuilder
    private var topChrome: some View {
        switch chrome {
        case .onboarding(_, let onBack):
            HStack {
                if let onBack {
                    Button(action: onBack) {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundColor(LightColors.ink)
                            .frame(width: 40, height: 40, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Back to name")
                }
                Spacer()
                Text("2 OF 2")
                    .font(.label(12))
                    .tracking(2)
                    .foregroundColor(LightColors.ink.opacity(0.4))
            }
            .padding(.horizontal, BottomActionBar.pageMargin + 4)
            .padding(.top, 8)

        case .settings:
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    backButton
                    Spacer()
                }
                .padding(.leading, BottomActionBar.pageMargin - 8)
                .padding(.top, 6)

                if let saveError {
                    Text(saveError)
                        .font(.label(10))
                        .foregroundColor(Color(hex: 0xC85B5B))
                        .tracking(0.5)
                        .padding(.horizontal, BottomActionBar.pageMargin)
                        .padding(.bottom, 4)
                }
            }
        }
    }

    /// Back chevron — matches the playback screen. Saves-on-back when ready;
    /// otherwise just returns. Shows a spinner while the save is in flight.
    private var backButton: some View {
        Button(action: back) {
            Group {
                if isSaving {
                    ProgressView().tint(LightColors.ink)
                } else {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(LightColors.ink)
                }
            }
            .frame(width: 40, height: 40, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
        .accessibilityLabel("Back")
    }

    private func back() {
        if loadState == .ready {
            finish()                       // save (updateProfile) + dismiss
        } else if case .settings(let onClose) = chrome {
            onClose()                      // nothing to save — just return
        }
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

    // MARK: Pinned footer (onboarding only — shared action bar + soft fade)

    private var footer: some View {
        // The CTA label lives on the onboarding chrome.
        let ctaLabel: String = {
            if case .onboarding(let label, _) = chrome { return label }
            return ""
        }()
        return VStack(spacing: 0) {
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
/// current selections, shows a top-left back chevron (no footer), and on back
/// saves the edited selections to the profile and returns to Home. No step
/// indicator.
struct ProfileFiltersView: View {
    var service: ProfileServicing = ProfileService()

    @AppStorage("profileId")      private var profileId = 0
    @AppStorage("userName")       private var userName = ""
    @AppStorage("selectedTopics") private var selectedTopicsJSON = ""
    @AppStorage("briefingLength") private var briefingLengthRaw = BriefingLength.standard.rawValue
    @Environment(\.dismiss)       private var dismiss

    private enum Stage: Equatable {
        case loading
        case ready(profile: Profile?, selection: Set<String>)
        case failed(String)
    }
    @State private var stage: Stage = .loading

    // Editable preferences (name + length + filters), seeded from the profile.
    @State private var editedName = ""
    @State private var editedLength: BriefingLength = .standard
    @State private var workingSelection: Set<String> = []
    @State private var loadedProfile: Profile?
    @State private var showFilters = false
    @State private var isSaving = false
    @State private var saveError: String?

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

            case .ready:
                settingsHub
            }
        }
        .task { if stage == .loading { await load() } }
        .sheet(isPresented: $showFilters) {
            FiltersScreen(
                chrome: .settings(onClose: { showFilters = false }),
                initialSelection: workingSelection,
                onComplete: { leafIDs in
                    workingSelection = Set(leafIDs)   // update working state; save on hub back
                    showFilters = false
                }
            )
        }
    }

    /// The settings hub — name, length and topics all editable (nothing write-once).
    /// Back saves everything to the (device-keyed) profile and returns Home.
    private var settingsHub: some View {
        VStack(spacing: 0) {
            HStack {
                Button(action: { Task { await saveAll() } }) {
                    Group {
                        if isSaving { ProgressView().tint(LightColors.ink) }
                        else { Image(systemName: "chevron.left").font(.system(size: 18, weight: .semibold)) }
                    }
                    .foregroundColor(LightColors.ink)
                    .frame(width: 40, height: 40, alignment: .leading).contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(isSaving).accessibilityLabel("Save and close")
                Spacer()
                Text("SETTINGS").font(.label(12)).tracking(2).foregroundColor(LightColors.ink.opacity(0.4))
                Spacer()
                Color.clear.frame(width: 40, height: 40)
            }
            .padding(.horizontal, BottomActionBar.pageMargin - 8).padding(.top, 6)

            if let saveError {
                Text(saveError)
                    .font(.label(11)).tracking(0.5)
                    .foregroundColor(Color(hex: 0xC85B5B))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, BottomActionBar.pageMargin)
                    .padding(.top, 4)
            }

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 26) {
                    // NAME
                    VStack(alignment: .leading, spacing: 8) {
                        Text("YOUR NAME").font(.label(11)).tracking(2.5)
                            .foregroundColor(LightColors.ink.opacity(0.45))
                        TextField("Your name", text: $editedName)
                            .font(.label(16)).foregroundColor(LightColors.ink)
                            .textInputAutocapitalization(.words)
                            .padding(.vertical, 12).padding(.horizontal, 16)
                            .background(RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .strokeBorder(LightColors.ink.opacity(0.15), lineWidth: 1))
                    }
                    .padding(.horizontal, BottomActionBar.pageMargin)

                    // LENGTH
                    LengthPicker(selected: $editedLength)

                    // TOPICS → filters sheet
                    VStack(alignment: .leading, spacing: 8) {
                        Text("TOPICS").font(.label(11)).tracking(2.5)
                            .foregroundColor(LightColors.ink.opacity(0.45))
                        Button(action: { showFilters = true }) {
                            HStack {
                                Text(workingSelection.isEmpty ? "Top Stories only" : "\(workingSelection.count) selected")
                                    .font(.label(14)).foregroundColor(LightColors.ink)
                                Spacer()
                                Image(systemName: "chevron.right").font(.system(size: 14, weight: .semibold))
                                    .foregroundColor(LightColors.ink.opacity(0.35))
                            }
                            .padding(.vertical, 14).padding(.horizontal, 16)
                            .background(RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .strokeBorder(LightColors.ink.opacity(0.15), lineWidth: 1))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, BottomActionBar.pageMargin)
                }
                .padding(.top, 20).padding(.bottom, 40)
            }
        }
    }

    /// Load the user's current profile so we can show — and preserve — what they
    /// already have. Falls back to the locally-persisted leaf-id JSON if the
    /// profile can't be read, so the screen still opens with the right state.
    private func load() async {
        stage = .loading
        var profile: Profile?
        do {
            let profiles = try await service.fetchProfiles()
            profile = profiles.first(where: { $0.id == profileId }) ?? profiles.first
        } catch {
            profile = nil   // offline — edit against locally-saved state
        }
        loadedProfile = profile
        editedName = profile?.name ?? userName
        editedLength = BriefingLength.from(minutes: profile?.maxDurationMinutes ?? 6)
        workingSelection = Set(profile?.includeCategories ?? decodedSavedTopics())
        stage = .ready(profile: profile, selection: workingSelection)
    }

    private func decodedSavedTopics() -> [String] {
        guard let data = selectedTopicsJSON.data(using: .utf8),
              let ids = try? JSONDecoder().decode([String].self, from: data) else { return [] }
        return ids
    }

    /// Save name + length + filters to the profile (create if none), persist locally,
    /// dismiss. A transient error keeps the user on the hub so edits aren't lost.
    private func saveAll() async {
        isSaving = true
        saveError = nil
        let leafIDs = Array(workingSelection)
        let include: [String]? = leafIDs.isEmpty ? nil : leafIDs.sorted()
        let trimmed = editedName.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalName = trimmed.isEmpty ? "Listener" : trimmed
        do {
            if let profile = loadedProfile {
                _ = try await service.updateProfile(
                    id: profile.id, name: finalName,
                    maxDurationMinutes: editedLength.minutes, voice: profile.voice,
                    includeCategories: include, excludeCategories: profile.excludeCategories,
                    includeTopStories: profile.includeTopStories)
                profileId = profile.id
            } else {
                let created = try await service.createProfile(
                    name: finalName, maxDurationMinutes: editedLength.minutes, voice: nil,
                    includeCategories: include, excludeCategories: nil, includeTopStories: true)
                profileId = created.id
            }
            userName = finalName
            briefingLengthRaw = editedLength.rawValue
            persistSelection(leafIDs)
            dismiss()
        } catch {
            // Surface the failure instead of silently stopping the spinner — the user
            // must know the save didn't land (they stay on the hub to retry).
            saveError = "Couldn't save your changes. Check your connection and try again."
            isSaving = false
        }
    }

    private func persistSelection(_ leafIDs: [String]) {
        if let data = try? JSONEncoder().encode(leafIDs),
           let json = String(data: data, encoding: .utf8) {
            selectedTopicsJSON = json
        }
    }
}
