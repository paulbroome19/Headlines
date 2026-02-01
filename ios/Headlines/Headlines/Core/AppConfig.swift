//
//  AppConfig.swift
//  Headlines
//

import Foundation

enum AppConfig {

    /// Info.plist override key (optional)
    /// Add a String key named `API_BASE_URL` to override base URL per build.
    private static let infoPlistKey = "API_BASE_URL"

    /// Default base URL used in DEBUG when no Info.plist override exists.
    /// - Simulator: http://127.0.0.1:8001 usually works (your Mac)
    /// - Physical device: replace with your Mac LAN IP (e.g. http://192.168.1.20:8001)
    private static let debugDefaultBaseURLString = "http://127.0.0.1:8001"

    /// Default base URL used in RELEASE when no Info.plist override exists.
    /// Put your production API URL here later.
    private static let releaseDefaultBaseURLString = "https://api.yourdomain.com"

    /// The base URL used by the app.
    /// Always returns a valid URL or crashes early with a clear message.
    static var apiBaseURL: URL {
        // 1) Prefer Info.plist override if present
        if let raw = Bundle.main.object(forInfoDictionaryKey: infoPlistKey) as? String {
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                guard let url = URL(string: trimmed) else {
                    fatalError("AppConfig: Invalid \(infoPlistKey) in Info.plist: \(trimmed)")
                }
                return url
            }
        }

        // 2) Fall back to defaults
        #if DEBUG
        guard let url = URL(string: debugDefaultBaseURLString) else {
            fatalError("AppConfig: Invalid debug default base URL: \(debugDefaultBaseURLString)")
        }
        return url
        #else
        guard let url = URL(string: releaseDefaultBaseURLString) else {
            fatalError("AppConfig: Invalid release default base URL: \(releaseDefaultBaseURLString)")
        }
        return url
        #endif
    }
}
