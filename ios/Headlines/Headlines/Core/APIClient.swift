import Foundation
import Security

// MARK: - Device identity

/// A stable per-device id sent as `X-Device-Id` so the backend can scope profiles to
/// this device. Stored in the Keychain (not UserDefaults) so it SURVIVES app
/// reinstall — reinstalling recovers the same backend profile instead of orphaning it
/// or adopting someone else's. Generated once on first access.
enum DeviceIdentity {
    private static let service = "com.headlines.identity"
    private static let account = "device-id"

    static let id: String = load() ?? generateAndStore()

    private static func load() -> String? {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String:  true,
            kSecMatchLimit as String:  kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data, let s = String(data: data, encoding: .utf8),
              !s.isEmpty else { return nil }
        return s
    }

    private static func generateAndStore() -> String {
        let value = UUID().uuidString
        let data = Data(value.utf8)
        // Delete any stale item, then add. AfterFirstUnlock so a background launch can
        // read it; not synced to iCloud (per-device identity).
        let base: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(base as CFDictionary)
        var add = base
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(add as CFDictionary, nil)
        return value
    }
}

// MARK: - Errors

enum APIError: Error, LocalizedError {
    case invalidURL
    case badStatus(Int, String)   // code + response body snippet
    case decodingFailed(Error)
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid URL"
        case .badStatus(let code, let body):
            let snippet = body.isEmpty ? "" : " — \(body)"
            return "Server returned \(code)\(snippet)"
        case .decodingFailed(let err):
            return "Failed to decode response: \(err.localizedDescription)"
        case .transport(let err):
            return "Network error: \(err.localizedDescription)"
        }
    }

    /// True when the server signalled a *normal* empty result — the filters matched
    /// no stories right now (structured `code: no_stories`) — as opposed to a genuine
    /// failure. Lets the UI show a graceful "all caught up" state instead of an error.
    var isNoStoriesMatch: Bool {
        if case let .badStatus(code, body) = self, code == 404 {
            return body.contains("no_stories")
        }
        return false
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
        req.setValue(DeviceIdentity.id, forHTTPHeaderField: "X-Device-Id")
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
        req.setValue(DeviceIdentity.id, forHTTPHeaderField: "X-Device-Id")
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
            guard let http = response as? HTTPURLResponse else {
                throw APIError.badStatus(-1, "")
            }
            guard (200...299).contains(http.statusCode) else {
                let body = String(data: data, encoding: .utf8) ?? "(binary \(data.count) bytes)"
                let snippet = String(body.prefix(300))
                #if DEBUG
                print("⚠️ API \(request.httpMethod ?? "?") \(request.url?.absoluteString ?? "?") → \(http.statusCode): \(snippet)")
                #endif
                throw APIError.badStatus(http.statusCode, snippet)
            }
            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                #if DEBUG
                let url = request.url?.absoluteString ?? "(unknown)"
                let body = String(data: data, encoding: .utf8) ?? "(binary \(data.count) bytes)"
                print("❌ Decode failed  type=\(T.self)  url=\(url)")
                print("   body: \(body.prefix(600))")
                switch error {
                case DecodingError.keyNotFound(let key, let ctx):
                    print("   keyNotFound: \"\(key.stringValue)\" at \(ctx.codingPath.map(\.stringValue))")
                case DecodingError.valueNotFound(let type, let ctx):
                    print("   valueNotFound: \(type) at \(ctx.codingPath.map(\.stringValue))")
                case DecodingError.typeMismatch(let type, let ctx):
                    print("   typeMismatch: expected \(type) at \(ctx.codingPath.map(\.stringValue))")
                case DecodingError.dataCorrupted(let ctx):
                    print("   dataCorrupted: \(ctx.debugDescription) at \(ctx.codingPath.map(\.stringValue))")
                default:
                    print("   error: \(error)")
                }
                #endif
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
