import Foundation

// MARK: - Response DTOs

struct ProfileDTO: Decodable {
    let id: Int
    let name: String
    let includeCategories: [String]?
    let excludeCategories: [String]?
    let maxDurationMinutes: Int
    let voice: String?
    let includeTopStories: Bool?   // nil-safe: absent key → true in domain mapping
    let createdAt: Date
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, name, voice
        case includeCategories   = "include_categories"
        case excludeCategories   = "exclude_categories"
        case maxDurationMinutes  = "max_duration_minutes"
        case includeTopStories   = "include_top_stories"
        case createdAt           = "created_at"
        case updatedAt           = "updated_at"
    }
}

struct ProfileListDTO: Decodable {
    let profiles: [ProfileDTO]
}

// MARK: - Request bodies

struct CreateProfileBody: Encodable {
    let name: String
    let maxDurationMinutes: Int
    let voice: String?
    let includeCategories: [String]?
    let excludeCategories: [String]?
    let includeTopStories: Bool

    enum CodingKeys: String, CodingKey {
        case name, voice
        case maxDurationMinutes  = "max_duration_minutes"
        case includeCategories   = "include_categories"
        case excludeCategories   = "exclude_categories"
        case includeTopStories   = "include_top_stories"
    }
}

struct UpdateProfileBody: Encodable {
    let name: String
    let maxDurationMinutes: Int
    let voice: String?
    let includeCategories: [String]?
    let excludeCategories: [String]?
    let includeTopStories: Bool

    enum CodingKeys: String, CodingKey {
        case name, voice
        case maxDurationMinutes  = "max_duration_minutes"
        case includeCategories   = "include_categories"
        case excludeCategories   = "exclude_categories"
        case includeTopStories   = "include_top_stories"
    }
}
