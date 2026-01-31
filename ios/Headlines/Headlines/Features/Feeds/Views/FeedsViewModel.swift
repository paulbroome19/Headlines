import Foundation

@MainActor
final class FeedsViewModel: ObservableObject {

    // MARK: - State

    @Published private(set) var isLoading: Bool = false
    @Published private(set) var feed: Feed? = nil
    @Published private(set) var errorMessage: String? = nil
    @Published private(set) var lastUpdated: Date? = nil

    // MARK: - Dependencies

    private let feedsService: FeedsServicing

    // MARK: - Init

    init(feedsService: FeedsServicing) {
        self.feedsService = feedsService
    }

    // MARK: - Public API

    func loadLatestFeed() async {
        isLoading = true
        errorMessage = nil

        do {
            let latest = try await feedsService.fetchLatestFeed()
            self.feed = latest
            self.lastUpdated = Date()
        } catch {
            self.feed = nil
            self.errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    // MARK: - UI helpers

    var lastUpdatedText: String? {
        guard let date = lastUpdated else { return nil }

        let delta = Date().timeIntervalSince(date)
        if abs(delta) < 2 {
            return "just now"
        }

        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short

        // Ensure the phrase is "ago" (past) not "in"
        let reference = Date()
        if date > reference {
            return "just now"
        }
        return formatter.localizedString(for: date, relativeTo: reference)
    }
}
