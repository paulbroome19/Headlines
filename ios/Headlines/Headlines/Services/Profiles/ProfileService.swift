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

    func createProfile(name: String, maxDurationMinutes: Int, voice: String?, includeCategories: [String]?, excludeCategories: [String]?, includeTopStories: Bool) async throws -> Profile {
        let body = CreateProfileBody(
            name: name,
            maxDurationMinutes: maxDurationMinutes,
            voice: nilIfEmpty(voice),
            includeCategories: nilIfEmpty(includeCategories),
            excludeCategories: nilIfEmpty(excludeCategories),
            includeTopStories: includeTopStories
        )
        let dto: ProfileDTO = try await client.post("data/profiles", body: body)
        return Profile(dto: dto)
    }

    func updateProfile(id: Int, name: String, maxDurationMinutes: Int, voice: String?, includeCategories: [String]?, excludeCategories: [String]?, includeTopStories: Bool) async throws -> Profile {
        let body = UpdateProfileBody(
            name: name,
            maxDurationMinutes: maxDurationMinutes,
            voice: nilIfEmpty(voice),
            includeCategories: nilIfEmpty(includeCategories),
            excludeCategories: nilIfEmpty(excludeCategories),
            includeTopStories: includeTopStories
        )
        let dto: ProfileDTO = try await client.put("data/profiles/\(id)", body: body)
        return Profile(dto: dto)
    }
}

// MARK: - Mapping

private extension Profile {
    init(dto: ProfileDTO) {
        self.init(
            id: dto.id,
            name: dto.name,
            includeCategories: dto.includeCategories,
            excludeCategories: dto.excludeCategories,
            maxDurationMinutes: dto.maxDurationMinutes,
            voice: dto.voice,
            includeTopStories: dto.includeTopStories ?? true,
            updatedAt: dto.updatedAt
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
