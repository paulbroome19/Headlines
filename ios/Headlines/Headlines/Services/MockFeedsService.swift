import Foundation

struct MockFeedsService: FeedsServicing {
    func fetchLatestFeed() async throws -> Feed {
        Feed(
            id: 1,
            title: "Your Daily Headlines",
            items: [
                "Markets rallied overnight as inflation cooled",
                "Tech stocks led gains across Europe",
                "Central banks signal rate cuts later this year"
            ]
        )
    }
}
