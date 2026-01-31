import Foundation

enum FeedsServiceError: Error {
    case invalidURL
    case badStatusCode(Int)
}

final class FeedsService: FeedsServicing {
    // iOS Simulator: 127.0.0.1 should work for services running on your Mac
    // Physical device: you'll need your Mac's LAN IP (e.g. http://192.168.1.20:8001)
    private let baseURL = "http://127.0.0.1:8001"

    func fetchLatestFeed() async throws -> Feed {
        let urlString = "\(baseURL)/feeds/latest"
        guard let url = URL(string: urlString) else { throw FeedsServiceError.invalidURL }

        let (data, response) = try await URLSession.shared.data(from: url)

        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw FeedsServiceError.badStatusCode(http.statusCode)
        }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let dateString = try container.decode(String.self)

            // 1) Try ISO8601 with fractional seconds (most common for APIs)
            let isoWithFractional = ISO8601DateFormatter()
            isoWithFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = isoWithFractional.date(from: dateString) {
                return date
            }

            // 2) Fallback: ISO8601 without fractional seconds
            let isoNoFractional = ISO8601DateFormatter()
            isoNoFractional.formatOptions = [.withInternetDateTime]
            if let date = isoNoFractional.date(from: dateString) {
                return date
            }

            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Expected date string to be ISO8601-formatted."
            )
        }

        let dto = try decoder.decode(FeedsLatestResponseDTO.self, from: data)

        let items = dto.items
            .sorted { $0.position < $1.position }
            .map { "Item \($0.position) (summary_id: \($0.summarise_output_id))" }

        return Feed(
            id: dto.feed.id,
            title: "Latest Feed (\(dto.feed.source))",
            items: items
        )
    }
}
