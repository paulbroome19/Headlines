import SwiftUI

@main
struct HeadlinesApp: App {
    var body: some Scene {
        WindowGroup {
            PlaybackView(
                viewModel: PlaybackViewModel(
                    feedsService: FeedsService()
                )
            )
        }
    }
}
