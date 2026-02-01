import Foundation

final class FeedsService: FeedsServicing {
    private let client: APIClient

    // Default init uses AppConfig.apiBaseURL (URL)
    init(client: APIClient = APIClient(baseURL: AppConfig.apiBaseURL)) {
        self.client = client
    }

    func fetchLatestFeed() async throws -> Feed {
        // Backend returns: { "feed": {...}, "items": [...] }
        let dto: FeedsLatestResponseDTO = try await client.get("feeds/latest")

        let items: [FeedItem] = dto.items
            .sorted { $0.position < $1.position }
            .map { item in
                FeedItem(
                    id: item.id,
                    position: item.position,
                    summariseOutputID: item.summariseOutputId,
                    text: item.text
                )
            }

        return Feed(
            id: dto.feed.id,
            title: "Latest Feed (\(dto.feed.source))",
            items: items
        )
    }
}
