import SwiftUI

@main
struct HeadlinesApp: App {
    var body: some Scene {
        WindowGroup {
            MainView(feedsService: FeedsService())
        }
    }
}
