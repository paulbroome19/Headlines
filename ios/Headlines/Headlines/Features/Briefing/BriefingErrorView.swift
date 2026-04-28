import SwiftUI

struct BriefingErrorView: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        ErrorStateView(
            title: "Couldn't load your briefing",
            message: message,
            onRetry: onRetry
        )
    }
}
