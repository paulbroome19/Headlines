import Foundation

struct BulletinStory: Identifiable, Equatable {
    let id: String
    let headline: String
    let category: String?
    // Populated once the backend includes segment timestamps (start_time per story).
    // Nil until then — navigation controls stay hidden when this is absent.
    let startTime: TimeInterval?
}

struct BulletinResult: Equatable {
    let profileId: Int
    let profileName: String
    let bulletinId: Int?
    let storyCount: Int
    let bulletinCached: Bool
    let script: String?
    let audioId: Int?
    let audioUrl: String?
    let voice: String?
    let audioCached: Bool
    let stories: [BulletinStory]
}
