import Foundation

// MARK: - Protocol

protocol BulletinServicing {
    func generateBulletin(profileID: Int) async throws -> BulletinResult
    func audioFileURL(forBulletinID bulletinId: Int) -> URL
}

// MARK: - Service

final class BulletinService: BulletinServicing {
    private let client: APIClient

    init(client: APIClient = APIClient(baseURL: AppConfig.apiBaseURL)) {
        self.client = client
    }

    func generateBulletin(profileID: Int) async throws -> BulletinResult {
        let dto: BulletinResultDTO = try await client.post("data/profiles/\(profileID)/bulletin")
        let stories = (dto.stories ?? []).map {
            BulletinStory(id: $0.storyId, headline: $0.headline, category: $0.category, startTime: $0.startTime)
        }
        return BulletinResult(
            profileId:      dto.profileId,
            profileName:    dto.profileName,
            bulletinId:     dto.bulletinId,
            storyCount:     dto.storyCount ?? stories.count,
            bulletinCached: dto.bulletinCached ?? false,
            script:         dto.script,
            audioId:        dto.audioId,
            audioUrl:       dto.audioUrl,
            voice:          dto.voice,
            audioCached:    dto.audioCached ?? false,
            stories:        stories
        )
    }

    func audioFileURL(forBulletinID bulletinId: Int) -> URL {
        var components = URLComponents(url: client.baseURL, resolvingAgainstBaseURL: false)!
        components.path = "/dev/api/audio/\(bulletinId)/file"
        return components.url!
    }
}

// MARK: - DTOs (private to this file)

private struct BulletinStoryDTO: Decodable {
    let storyId:   String
    let headline:  String
    let category:  String?
    let startTime: Double?

    enum CodingKeys: String, CodingKey {
        case storyId   = "story_id"
        case headline
        case category
        case startTime = "start_time"
    }
}

private struct BulletinResultDTO: Decodable {
    let profileId:      Int
    let profileName:    String
    let bulletinId:     Int?
    let storyCount:     Int?
    let bulletinCached: Bool?
    let script:         String?
    let stories:        [BulletinStoryDTO]?
    let audioId:        Int?
    let audioUrl:       String?
    let voice:          String?
    let audioCached:    Bool?

    enum CodingKeys: String, CodingKey {
        case profileId      = "profile_id"
        case profileName    = "profile_name"
        case bulletinId     = "bulletin_id"
        case storyCount     = "story_count"
        case bulletinCached = "bulletin_cached"
        case script
        case stories
        case audioId        = "audio_id"
        case audioUrl       = "audio_url"
        case voice
        case audioCached    = "audio_cached"
    }
}
