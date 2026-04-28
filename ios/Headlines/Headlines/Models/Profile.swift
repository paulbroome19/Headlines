import Foundation

struct Profile: Identifiable, Equatable {
    let id: Int
    let name: String
    let includeCategories: [String]?
    let excludeCategories: [String]?
    let maxDurationMinutes: Int
    let voice: String?
    let includeTopStories: Bool
    let updatedAt: Date
}
