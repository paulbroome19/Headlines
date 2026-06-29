import SwiftUI

@main
struct HeadlinesApp: App {

    /// Drives the cold-launch flow. The loader always plays first; where it
    /// hands off depends on whether the user has completed onboarding.
    private enum Phase { case loading, createProfile, buildBriefing, home }

    @AppStorage("didOnboard") private var didOnboard = false
    @AppStorage("userName")   private var userName   = ""

    @State private var phase: Phase = .loading

    var body: some Scene {
        WindowGroup {
            Group {
                switch phase {
                case .loading:
                    // The split-flap board plays on every cold launch, then
                    // branches once: first-run users onboard, everyone else
                    // lands straight on Home.
                    SplitFlapLoadingView {
                        guard phase == .loading else { return }   // fire once
                        phase = didOnboard ? .home : .createProfile
                    }
                    .transition(.opacity)

                case .createProfile:
                    // Step 1 — persists `userName`, then advances to topics.
                    CreateProfileView { phase = .buildBriefing }
                        .transition(.opacity)

                case .buildBriefing:
                    // Step 2 — persists topics + sets `didOnboard`, then Home.
                    BuildBriefingView { phase = .home }
                        .transition(.opacity)

                case .home:
                    // Greets with the stored name; "PAUL" only as a preview/empty
                    // fallback that onboarding always overwrites.
                    HomeView(userName: userName.isEmpty ? "PAUL" : userName)
                        .transition(.opacity)
                }
            }
            .animation(.easeInOut(duration: 0.45), value: phase)
        }
    }
}
