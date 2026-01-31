import Foundation

struct Feed: Identifiable, Codable, Equatable {
    let id: Int
    let title: String
    let items: [String]
}
