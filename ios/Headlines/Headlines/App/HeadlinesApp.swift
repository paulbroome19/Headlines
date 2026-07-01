import SwiftUI

@main
struct HeadlinesApp: App {

    /// Drives the cold-launch flow. The loader always plays first; where it
    /// hands off depends on whether the user has completed onboarding.
    private enum Phase { case loading, createProfile, buildBriefing, length, home }

    @AppStorage("didOnboard") private var didOnboard = false
    @AppStorage("userName")   private var userName   = ""

    @State private var phase: Phase = .loading

    init() {
        #if DEBUG
        // DEBUG-only: launch with `-resetFirstRun` (e.g. via `xcrun simctl
        // launch <udid> <bid> -resetFirstRun`) to clear onboarding + the hint
        // flag and re-trigger the full first-run, no reinstall needed.
        if CommandLine.arguments.contains("-resetFirstRun") {
            // Write the first-run values explicitly (not removeObject) so the
            // App's own @AppStorage reads pick them up deterministically on this
            // launch rather than racing a deleted key.
            let d = UserDefaults.standard
            d.set("", forKey: "userName")
            d.set(false, forKey: "didOnboard")
            d.set(false, forKey: "hasSeenHomeHint")
        }
        #endif
    }

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
                    // Step 2 — pick topics (persisted); advance to the length step.
                    // Back returns to the name step (name persists via @AppStorage).
                    BuildBriefingView(onContinue: { phase = .length },
                                      onBack: { phase = .createProfile })
                        .transition(.opacity)

                case .length:
                    // Step 3 — pick briefing length; this CREATES the profile
                    // (name + topics + length), sets `didOnboard`, then Home.
                    LengthStepView(onDone: { phase = .home },
                                   onBack: { phase = .buildBriefing })
                        .transition(.opacity)

                case .home:
                    // Home + the now-playing player. HomeContainerView owns the
                    // BulletinPlayer and wires Play → generate → playback.
                    HomeContainerView()
                        .transition(.opacity)
                }
            }
            .animation(.easeInOut(duration: 0.45), value: phase)
        }
    }
}
