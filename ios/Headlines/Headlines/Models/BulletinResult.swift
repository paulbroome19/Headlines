import Foundation

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
}
