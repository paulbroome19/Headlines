import SwiftUI

@main
struct HeadlinesApp: App {
    // Loading is the standard launch state: the split-flap board plays on
    // every cold launch, then hands off to the app exactly once. (A warm
    // foreground resume keeps the process — and this flag — so it does not
    // replay.)
    @State private var isLoading = true

    var body: some Scene {
        WindowGroup {
            Group {
                if isLoading {
                    SplitFlapLoadingView {
                        // Guard so the hand-off fires once and never loops.
                        guard isLoading else { return }
                        isLoading = false
                    }
                    .transition(.opacity)
                } else {
                    BriefingView()
                        .transition(.opacity)
                }
            }
            .animation(.easeInOut(duration: 0.45), value: isLoading)
        }
    }
}
