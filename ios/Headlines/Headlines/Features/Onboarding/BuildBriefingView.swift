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
        // Onboarding step 2 (filters): pick topics, persist them, then advance to the
        // LENGTH step (which creates the profile). Nothing is written to the backend
        // here — the profile is created once, at the end, with name + topics + length.
        FiltersScreen(
            chrome: .onboarding(ctaLabel: "CHOOSE YOUR LENGTH", onBack: onBack),
            initialSelection: nil,
            onComplete: { leafIDs in
                if let data = try? JSONEncoder().encode(leafIDs),
                   let json = String(data: data, encoding: .utf8) {
                    selectedTopicsJSON = json
                }
                onContinue()   // → length step
            }
        )
    }
}

// MARK: - Briefing length (preset)

/// The user-facing length options. Under the hood each maps to a `maxDurationMinutes`
/// value the backend turns into the short/medium/detailed threshold preset (which sets
/// the target story count 6/10/16). Time labels are approximate — length flexes with
/// the day's news, but Quick < Standard < Deep always holds.
enum BriefingLength: String, CaseIterable, Identifiable {
    case quick, standard, deep
    var id: String { rawValue }

    /// maxDurationMinutes → _preset_from_minutes: ≤3 short, ≤7 medium, else detailed.
    var minutes: Int { switch self { case .quick: 3; case .standard: 6; case .deep: 12 } }
    var title: String { switch self { case .quick: "QUICK"; case .standard: "STANDARD"; case .deep: "DEEP" } }
    /// Qualitative depth only — no minute figures (actual duration flexes with the day's
    /// news and isn't reliably tied to a fixed minute count).
    var detail: String {
        switch self {
        case .quick:    "The essentials"
        case .standard: "The full picture"
        case .deep:     "The whole story"
        }
    }
    static func from(minutes: Int) -> BriefingLength {
        if minutes <= 3 { return .quick }
        if minutes <= 7 { return .standard }
        return .deep
    }
}

// MARK: - Length step (onboarding step 3) + reusable picker

struct LengthStepView: View {
    var onDone: () -> Void = {}
    var onBack: () -> Void = {}
    var profileService: ProfileServicing = ProfileService()

    @AppStorage("userName")       private var userName = ""
    @AppStorage("profileId")      private var profileId = 0
    @AppStorage("didOnboard")     private var didOnboard = false
    @AppStorage("selectedTopics") private var selectedTopicsJSON = ""
    @AppStorage("briefingLength") private var briefingLengthRaw = BriefingLength.standard.rawValue

    @State private var selected: BriefingLength = .standard
    @State private var isSaving = false
    @State private var saveError: String?

    var body: some View {
        ZStack {
            LightColors.page.ignoresSafeArea()
            VStack(spacing: 0) {
                HStack {
                    Button(action: onBack) {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundColor(LightColors.ink)
                            .frame(width: 40, height: 40, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain).accessibilityLabel("Back to topics")
                    Spacer()
                    StepDots(step: 3, total: 3).padding(.trailing, 8)
                }
                .padding(.horizontal, BottomActionBar.pageMargin - 8)
                .padding(.top, 6)

                LengthPicker(selected: $selected)
                    .padding(.top, 24)

                if let saveError {
                    Text(saveError)
                        .font(.label(10)).tracking(0.5)
                        .foregroundColor(Color(hex: 0xC85B5B))
                        .padding(.top, 10)
                }
                Spacer()
                BottomActionBar(title: isSaving ? "SAVING…" : "COMPLETE SETUP",
                                enabled: !isSaving,
                                action: { Task { await finish() } })
            }
        }
        .onAppear { selected = BriefingLength(rawValue: briefingLengthRaw) ?? .standard }
    }

    private func finish() async {
        isSaving = true; saveError = nil
        let leafIDs = (try? JSONDecoder().decode([String].self,
                        from: Data(selectedTopicsJSON.utf8))) ?? []
        let include: [String]? = leafIDs.isEmpty ? nil : leafIDs.sorted()
        let name = userName.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let profile = try await profileService.createProfile(
                name: name.isEmpty ? "Listener" : name,
                maxDurationMinutes: selected.minutes,
                voice: nil,
                includeCategories: include,
                excludeCategories: nil,
                includeTopStories: true
            )
            profileId = profile.id
            briefingLengthRaw = selected.rawValue
            didOnboard = true          // only after the profile exists
            onDone()
        } catch {
            saveError = "Couldn't save — please try again."
            isSaving = false
        }
    }
}

/// The three length options as tappable cards. Reused by onboarding step 3 and settings.
struct LengthPicker: View {
    @Binding var selected: BriefingLength

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("BRIEFING LENGTH")
                .font(.label(11)).tracking(2.5)
                .foregroundColor(LightColors.ink.opacity(0.45))
                .padding(.horizontal, BottomActionBar.pageMargin)
            ForEach(BriefingLength.allCases) { option in
                Button { selected = option } label: {
                    HStack(spacing: 14) {
                        Image(systemName: selected == option ? "largecircle.fill.circle" : "circle")
                            .font(.system(size: 20))
                            .foregroundColor(selected == option ? LightColors.ink : LightColors.ink.opacity(0.3))
                        VStack(alignment: .leading, spacing: 3) {
                            Text(option.title).font(.label(14)).tracking(1.5)
                                .foregroundColor(LightColors.ink)
                            Text(option.detail).font(.label(11)).tracking(0.3)
                                .foregroundColor(LightColors.ink.opacity(0.45))
                        }
                        Spacer()
                    }
                    .padding(.vertical, 14).padding(.horizontal, 16)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(LightColors.ink.opacity(selected == option ? 0.05 : 0.0))
                            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .strokeBorder(LightColors.ink.opacity(selected == option ? 0.25 : 0.12), lineWidth: 1))
                    )
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.horizontal, BottomActionBar.pageMargin)
            }
        }
    }
}

// MARK: - Preview

#Preview {
    BuildBriefingView()
}
