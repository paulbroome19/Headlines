import Foundation

// MARK: - Response DTOs

struct ProfileDTO: Decodable {
    let id: Int
    let name: String
    let includeCategories: [String]?
    let excludeCategories: [String]?
    let maxStories: Int
    let voice: String?
    let createdAt: Date
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, name, voice
        case includeCategories = "include_categories"
        case excludeCategories = "exclude_categories"
        case maxStories = "max_stories"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct ProfileListDTO: Decodable {
    let profiles: [ProfileDTO]
}

struct BulletinResultDTO: Decodable {
    let profileId: Int
    let profileName: String
    let bulletinId: Int?
    let storyCount: Int?
    let bulletinCached: Bool?
    let script: String?
    let audioId: Int?
    let audioUrl: String?
    let voice: String?
    let audioCached: Bool?

    enum CodingKeys: String, CodingKey {
        case profileId = "profile_id"
        case profileName = "profile_name"
        case bulletinId = "bulletin_id"
        case storyCount = "story_count"
        case bulletinCached = "bulletin_cached"
        case script
        case audioId = "audio_id"
        case audioUrl = "audio_url"
        case voice
        case audioCached = "audio_cached"
    }
}

// MARK: - Request bodies

struct CreateProfileBody: Encodable {
    let name: String
    let maxStories: Int
    let voice: String?
    let includeCategories: [String]?
    let excludeCategories: [String]?

    enum CodingKeys: String, CodingKey {
        case name, voice
        case maxStories = "max_stories"
        case includeCategories = "include_categories"
        case excludeCategories = "exclude_categories"
    }
}

struct UpdateProfileBody: Encodable {
    let name: String
    let maxStories: Int
    let voice: String?
    let includeCategories: [String]?
    let excludeCategories: [String]?

    enum CodingKeys: String, CodingKey {
        case name, voice
        case maxStories = "max_stories"
        case includeCategories = "include_categories"
        case excludeCategories = "exclude_categories"
    }
}
