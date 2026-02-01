import Foundation

struct MockFeedsService: FeedsServicing {
    func fetchLatestFeed() async throws -> Feed {
        Feed(
            id: 1,
            title: "Your Daily Headlines",
            items: [
                FeedItem(
                    id: 1,
                    position: 1,
                    summariseOutputID: 101,
                    text: "Markets rallied overnight as inflation cooled"
                ),
                FeedItem(
                    id: 2,
                    position: 2,
                    summariseOutputID: 102,
                    text: "Tech stocks led gains across Europe"
                ),
                FeedItem(
                    id: 3,
                    position: 3,
                    summariseOutputID: 103,
                    text: "Central banks signal rate cuts later this year"
                )
            ]
        )
    }
}
