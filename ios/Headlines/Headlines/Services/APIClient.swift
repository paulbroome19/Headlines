import Foundation

enum APIError: Error, LocalizedError {
    case invalidURL
    case badStatus(Int)
    case decodingFailed
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid URL"
        case .badStatus(let code): return "Server returned status \(code)"
        case .decodingFailed: return "Failed to decode response"
        case .transport(let err): return "Network error: \(err.localizedDescription)"
        }
    }
}

struct APIClient {
    let baseURL: URL

    init(baseURL: URL) {
        self.baseURL = baseURL
    }

    func get<T: Decodable>(_ path: String, queryItems: [URLQueryItem] = []) async throws -> T {
        guard var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
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
                return try JSONDecoder().decode(T.self, from: data)
            } catch {
                throw APIError.decodingFailed
            }
        } catch {
            throw APIError.transport(error)
        }
    }
}
