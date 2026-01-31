import Foundation

struct Feed: Identifiable, Equatable {
    let id: Int
    let title: String
    let items: [FeedItem]
}
