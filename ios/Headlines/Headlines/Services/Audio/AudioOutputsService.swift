import Foundation

// MARK: - Protocol

protocol AudioOutputsServicing {
    func fetchLatestAudioOutput(feedID: Int) async throws -> AudioOutput
    func audioFileURL(forScriptsOutputID scriptsOutputID: Int) -> URL
}

// MARK: - Domain model

struct AudioOutput: Identifiable, Equatable {
    let id: Int
    let feedID: Int
    let scriptsOutputID: Int
    let format: String
    let status: String
    let durationSeconds: Int?
    let createdAt: Date
}

// MARK: - DTO

private struct AudioOutputDTO: Decodable {
    let id: Int
    let feedID: Int
    let scriptsOutputID: Int
    let format: String
    let status: String
    let durationSeconds: Int?
    let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id
        case feedID = "feed_id"
        case scriptsOutputID = "scripts_output_id"
        case format
        case status
        case durationSeconds = "duration_seconds"
        case createdAt = "created_at"
    }
}

// MARK: - Service

final class AudioOutputsService: AudioOutputsServicing {
    private let client: APIClient

    init(client: APIClient = APIClient(baseURL: AppConfig.apiBaseURL)) {
        self.client = client
    }

    func fetchLatestAudioOutput(feedID: Int) async throws -> AudioOutput {
        let dto: AudioOutputDTO = try await client.get(
            "audio/outputs/latest",
            queryItems: [URLQueryItem(name: "feed_id", value: String(feedID))]
        )

        return AudioOutput(
            id: dto.id,
            feedID: dto.feedID,
            scriptsOutputID: dto.scriptsOutputID,
            format: dto.format,
            status: dto.status,
            durationSeconds: dto.durationSeconds,
            createdAt: dto.createdAt
        )
    }

    /// IMPORTANT: the file endpoint takes scripts_output_id (path param),
    /// not feed_id.
    func audioFileURL(forScriptsOutputID scriptsOutputID: Int) -> URL {
        client.baseURL
            .appendingPathComponent("audio/outputs")
            .appendingPathComponent(String(scriptsOutputID))
            .appendingPathComponent("file")
    }
}
