import Foundation

final class ProfileService: ProfileServicing {
    private let client: APIClient

    init(client: APIClient = APIClient(baseURL: AppConfig.apiBaseURL)) {
        self.client = client
    }

    func fetchProfiles() async throws -> [Profile] {
        let dto: ProfileListDTO = try await client.get("data/profiles")
        return dto.profiles.map(Profile.init(dto:))
    }

    func createProfile(name: String, maxStories: Int, voice: String?, includeCategories: [String]?, excludeCategories: [String]?) async throws -> Profile {
        let body = CreateProfileBody(
            name: name,
            maxStories: maxStories,
            voice: nilIfEmpty(voice),
            includeCategories: nilIfEmpty(includeCategories),
            excludeCategories: nilIfEmpty(excludeCategories)
        )
        let dto: ProfileDTO = try await client.post("data/profiles", body: body)
        return Profile(dto: dto)
    }

    func updateProfile(id: Int, name: String, maxStories: Int, voice: String?, includeCategories: [String]?, excludeCategories: [String]?) async throws -> Profile {
        let body = UpdateProfileBody(
            name: name,
            maxStories: maxStories,
            voice: nilIfEmpty(voice),
            includeCategories: nilIfEmpty(includeCategories),
            excludeCategories: nilIfEmpty(excludeCategories)
        )
        let dto: ProfileDTO = try await client.put("data/profiles/\(id)", body: body)
        return Profile(dto: dto)
    }

    func generateBulletin(profileID: Int) async throws -> BulletinResult {
        let dto: BulletinResultDTO = try await client.post("data/profiles/\(profileID)/bulletin")
        return BulletinResult(dto: dto)
    }

    func audioFileURL(forBulletinID bulletinId: Int) -> URL {
        var components = URLComponents(url: client.baseURL, resolvingAgainstBaseURL: false)!
        components.path = "/dev/api/audio/\(bulletinId)/file"
        return components.url!
    }
}

// MARK: - Mapping helpers

private extension Profile {
    init(dto: ProfileDTO) {
        self.init(
            id: dto.id,
            name: dto.name,
            includeCategories: dto.includeCategories,
            excludeCategories: dto.excludeCategories,
            maxStories: dto.maxStories,
            voice: dto.voice,
            updatedAt: dto.updatedAt
        )
    }
}

private extension BulletinResult {
    init(dto: BulletinResultDTO) {
        self.init(
            profileId: dto.profileId,
            profileName: dto.profileName,
            bulletinId: dto.bulletinId,
            storyCount: dto.storyCount ?? 0,
            bulletinCached: dto.bulletinCached ?? false,
            script: dto.script,
            audioId: dto.audioId,
            audioUrl: dto.audioUrl,
            voice: dto.voice,
            audioCached: dto.audioCached ?? false
        )
    }
}

private func nilIfEmpty(_ s: String?) -> String? {
    guard let s, !s.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
    return s
}

private func nilIfEmpty(_ arr: [String]?) -> [String]? {
    guard let arr, !arr.isEmpty else { return nil }
    return arr
}
