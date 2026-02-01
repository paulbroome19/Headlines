import Foundation

// MARK: - Errors

enum APIError: Error, LocalizedError {
    case invalidURL
    case badStatus(Int)
    case decodingFailed(Error)
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid URL"
        case .badStatus(let code):
            return "Server returned status \(code)"
        case .decodingFailed(let err):
            return "Failed to decode response: \(err.localizedDescription)"
        case .transport(let err):
            return "Network error: \(err.localizedDescription)"
        }
    }
}

// MARK: - Client

struct APIClient {
    let baseURL: URL
    private let decoder: JSONDecoder

    init(baseURL: URL) {
        self.baseURL = baseURL
        self.decoder = APIClient.makeDecoder()
    }

    func get<T: Decodable>(
        _ path: String,
        queryItems: [URLQueryItem] = []
    ) async throws -> T {

        // IMPORTANT:
        // Don't do baseURL.appendingPathComponent("feeds/latest")
        // because it turns into "/feeds%2Flatest" (slash encoded) => 404.
        // Instead, construct the URL via URLComponents and set a real path.

        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }

        // Normalize path: allow "feeds/latest" or "/feeds/latest"
        let cleaned = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))

        // If baseURL already has a path, append to it safely
        let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if basePath.isEmpty {
            components.path = "/\(cleaned)"
        } else {
            components.path = "/\(basePath)/\(cleaned)"
        }

        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }

        guard let url = components.url else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        do {
            let (data, response) = try await URLSession.shared.data(for: request)

            guard let http = response as? HTTPURLResponse else {
                throw APIError.badStatus(-1)
            }

            guard (200...299).contains(http.statusCode) else {
                throw APIError.badStatus(http.statusCode)
            }

            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                throw APIError.decodingFailed(error)
            }

        } catch {
            // Don’t double-wrap our own errors
            if let apiError = error as? APIError {
                throw apiError
            }
            throw APIError.transport(error)
        }
    }
}

// MARK: - Decoder

private extension APIClient {
    static func makeDecoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let dateString = try container.decode(String.self)

            // 1) ISO8601 with fractional seconds
            let isoWithFractional = ISO8601DateFormatter()
            isoWithFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = isoWithFractional.date(from: dateString) {
                return date
            }

            // 2) ISO8601 without fractional seconds
            let isoNoFractional = ISO8601DateFormatter()
            isoNoFractional.formatOptions = [.withInternetDateTime]
            if let date = isoNoFractional.date(from: dateString) {
                return date
            }

            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Invalid ISO8601 date: \(dateString)"
            )
        }
        return d
    }
}
