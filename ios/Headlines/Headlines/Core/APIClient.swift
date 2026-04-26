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
        var req = URLRequest(url: try buildURL(path: path, queryItems: queryItems))
        req.httpMethod = "GET"
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await send(req)
    }

    func post<T: Decodable>(_ path: String) async throws -> T {
        try await request(method: "POST", path: path, bodyData: "{}".data(using: .utf8))
    }

    func post<T: Decodable, B: Encodable>(_ path: String, body: B) async throws -> T {
        let data = try JSONEncoder().encode(body)
        return try await request(method: "POST", path: path, bodyData: data)
    }

    func put<T: Decodable, B: Encodable>(_ path: String, body: B) async throws -> T {
        let data = try JSONEncoder().encode(body)
        return try await request(method: "PUT", path: path, bodyData: data)
    }

    // MARK: - Private helpers

    private func request<T: Decodable>(method: String, path: String, bodyData: Data?) async throws -> T {
        var req = URLRequest(url: try buildURL(path: path))
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = bodyData
        return try await send(req)
    }

    private func buildURL(path: String, queryItems: [URLQueryItem] = []) throws -> URL {
        // URLComponents-based construction avoids %2F encoding from appendingPathComponent.
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }
        let cleaned = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        components.path = basePath.isEmpty ? "/\(cleaned)" : "/\(basePath)/\(cleaned)"
        if !queryItems.isEmpty { components.queryItems = queryItems }
        guard let url = components.url else { throw APIError.invalidURL }
        return url
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw APIError.badStatus(-1) }
            guard (200...299).contains(http.statusCode) else { throw APIError.badStatus(http.statusCode) }
            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                throw APIError.decodingFailed(error)
            }
        } catch {
            if let apiError = error as? APIError { throw apiError }
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

            let isoWithFractional = ISO8601DateFormatter()
            isoWithFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = isoWithFractional.date(from: dateString) { return date }

            let isoNoFractional = ISO8601DateFormatter()
            isoNoFractional.formatOptions = [.withInternetDateTime]
            if let date = isoNoFractional.date(from: dateString) { return date }

            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Invalid ISO8601 date: \(dateString)"
            )
        }
        return d
    }
}
