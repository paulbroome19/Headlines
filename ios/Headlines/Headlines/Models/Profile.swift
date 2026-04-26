import Foundation

struct Profile: Identifiable, Equatable {
    let id: Int
    let name: String
    let includeCategories: [String]?
    let excludeCategories: [String]?
    let maxStories: Int
    let voice: String?
    let updatedAt: Date
}
