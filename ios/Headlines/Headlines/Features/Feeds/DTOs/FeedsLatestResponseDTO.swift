import Foundation

struct FeedsLatestResponseDTO: Decodable {
    let feed: FeedDTO
    let items: [FeedItemDTO]
}

struct FeedDTO: Decodable {
    let id: Int
    let source: String
    let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, source
        case createdAt = "created_at"
    }
}

struct FeedItemDTO: Decodable {
    let id: Int
    let feedId: Int
    let summariseOutputId: Int
    let position: Int
    let createdAt: Date
    let text: String

    enum CodingKeys: String, CodingKey {
        case id, position, text
        case feedId = "feed_id"
        case summariseOutputId = "summarise_output_id"
        case createdAt = "created_at"
    }
}
