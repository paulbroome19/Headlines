import Foundation

struct FeedItem: Identifiable, Equatable {
    let id: Int
    let position: Int
    let summariseOutputID: Int
    let text: String
}
