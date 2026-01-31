import Foundation

struct FeedsLatestResponseDTO: Decodable {
    let feed: FeedDTO
    let items: [FeedItemDTO]
}

struct FeedDTO: Decodable {
    let id: Int
    let source: String
    let created_at: Date
}

struct FeedItemDTO: Decodable {
    let id: Int
    let feed_id: Int
    let summarise_output_id: Int
    let position: Int
    let created_at: Date
}
