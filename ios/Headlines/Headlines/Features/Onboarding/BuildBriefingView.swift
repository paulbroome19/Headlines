//
//  BuildBriefingView.swift
//  Headlines
//
//  Onboarding step 2 — pick the topics that shape the briefing from the REAL
//  backend taxonomy (GET /data/categories), then create a backend profile
//  (POST /data/profiles) with the chosen categories and persist its id. Only on
//  a successful profile creation do we mark onboarding complete and route to Home.
//

import SwiftUI

struct BuildBriefingView: View {
    var onContinue: () -> Void = {}
    var profileService: ProfileServicing = ProfileService()

    @AppStorage("userName")       private var userName = ""
    @AppStorage("profileId")      private var profileId = 0
    @AppStorage("didOnboard")     private var didOnboard = false
    @AppStorage("selectedTopics") private var selectedTopicsJSON = ""

    @StateObject private var model = FilterTreeModel(roots: [])

    private enum LoadState: Equatable { case loading, ready, failed(String) }
    @State private var loadState: LoadState = .loading
    @State private var isSaving = false
    @State private var saveError: String?

    var body: some View {
        ZStack {
            BoardColors.background.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                content
                footer
            }
        }
        .task { await loadCategories() }
    }

    // MARK: Fixed heading

    private var header: some View {
        VStack(spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("BUILD YOUR BRIEFING")
                    .font(.board(20))
                    .foregroundColor(BoardColors.character)
                    .tracking(2)
                Spacer()
                StepDots(step: 2, total: 2)
            }
            .padding(.horizontal, 24)
            .padding(.top, 12)

            Rectangle()
                .fill(BoardColors.character.opacity(0.18))
                .frame(height: 1)
        }
        .background(BoardColors.background)
    }

    // MARK: Body content — load state drives this

    @ViewBuilder
    private var content: some View {
        switch loadState {
        case .loading:
            VStack(spacing: 12) {
                Spacer()
                ProgressView().tint(BoardColors.character.opacity(0.6))
                Text("LOADING CATEGORIES")
                    .font(.board(11))
                    .foregroundColor(BoardColors.character.opacity(0.45))
                    .tracking(2)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .failed(let msg):
            VStack(spacing: 14) {
                Spacer()
                Text("COULDN'T LOAD CATEGORIES")
                    .font(.board(13))
                    .foregroundColor(BoardColors.character.opacity(0.7))
                    .tracking(1.5)
                Text(msg)
                    .font(.board(10))
                    .foregroundColor(BoardColors.character.opacity(0.4))
                    .tracking(1)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
                retryButton { Task { await loadCategories() } }
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .ready:
            ScrollView(showsIndicators: false) {
                FilterTreeView(model: model)
                    .padding(.top, 4)
                    .padding(.bottom, 16)
            }
        }
    }

    private func retryButton(_ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text("RETRY")
                .font(.board(13))
                .foregroundColor(BoardColors.character)
                .tracking(2)
                .padding(.horizontal, 22)
                .padding(.vertical, 10)
                .overlay(
                    Capsule().strokeBorder(BoardColors.character.opacity(0.4), lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .padding(.top, 6)
    }

    // MARK: Fixed footer

    private var footer: some View {
        VStack(spacing: 0) {
            // Soft divider above the footer.
            LinearGradient(colors: [.clear, BoardColors.seam.opacity(0.7)],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: 14)
                .allowsHitTesting(false)

            if let saveError {
                Text(saveError)
                    .font(.board(10))
                    .foregroundColor(Color(hex: 0xC85B5B))
                    .tracking(0.5)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 24)
                    .padding(.bottom, 8)
            }

            Button(action: continueTapped) {
                HStack {
                    Text(isSaving ? "CREATING PROFILE…" : "CONTINUE")
                        .font(.board(18))
                        .foregroundColor(BoardColors.character.opacity(continueEnabled ? 1 : 0.4))
                        .tracking(2)
                    Spacer()
                    if isSaving {
                        ProgressView().tint(BoardColors.character.opacity(0.7))
                    } else {
                        OpenChevron()
                            .stroke(BoardColors.character.opacity(continueEnabled ? 0.9 : 0.3),
                                    style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                            .frame(width: 11, height: 18)
                    }
                }
                .padding(.horizontal, 24)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!continueEnabled)
            .padding(.top, 10)
            .padding(.bottom, 28)
        }
        .background(BoardColors.background)
    }

    private var continueEnabled: Bool { loadState == .ready && !isSaving }

    // MARK: Load categories

    private func loadCategories() async {
        loadState = .loading
        switch await fetchTopCategories() {
        case .loaded(let groups):
            model.replaceRoots(TopicNode.tree(from: groups))
            loadState = .ready
        case .failed(let msg):
            loadState = .failed(msg)
        }
    }

    // MARK: Create profile + advance

    private func continueTapped() {
        guard continueEnabled else { return }
        Task { await createProfileAndContinue() }
    }

    private func createProfileAndContinue() async {
        isSaving = true
        saveError = nil

        // selectedLeafIDs() are the real backend slugs (node ids == slugs).
        let leaves = model.selectedLeafIDs()
        let include: [String]? = leaves.isEmpty ? nil : leaves.sorted()
        let trimmedName = userName.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = trimmedName.isEmpty ? "Listener" : trimmedName

        do {
            let profile = try await profileService.createProfile(
                name: name,
                maxDurationMinutes: 5,
                voice: nil,
                includeCategories: include,
                excludeCategories: nil,
                includeTopStories: true
            )
            // Persist the backend profile id for Home/playback to generate against.
            profileId = profile.id
            if let data = try? JSONEncoder().encode(leaves),
               let json = String(data: data, encoding: .utf8) {
                selectedTopicsJSON = json
            }
            // Only mark onboarding complete AFTER the profile exists.
            didOnboard = true
            onContinue()
        } catch {
            // Graceful: surface the error, stay on this screen, allow retry.
            saveError = error.localizedDescription
            isSaving = false
        }
    }
}

// MARK: - Preview

#Preview {
    BuildBriefingView()
}
