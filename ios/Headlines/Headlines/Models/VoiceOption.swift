import Foundation

struct VoiceOption: Decodable, Identifiable {
    let key: String
    let displayName: String
    let provider: String
    let voiceId: String

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, provider
        case displayName = "display_name"
        case voiceId     = "voice_id"
    }
}

private struct VoiceListDTO: Decodable {
    let voices: [VoiceOption]
}

func fetchVoices(client: APIClient = APIClient(baseURL: AppConfig.apiBaseURL)) async -> [VoiceOption] {
    do {
        let dto: VoiceListDTO = try await client.get("data/voices")
        return dto.voices
    } catch {
        return []
    }
}
