import Foundation

enum AppConfig {

    // MARK: - Device IP
    //
    // When running on a physical iPhone the app cannot reach 127.0.0.1 —
    // it must use your Mac's LAN IP instead.
    //
    // How to find your Mac's LAN IP:
    //   System Settings → Wi-Fi → (your network) → Details → IP Address
    //   or: open Terminal and run:  ipconfig getifaddr en0
    //
    // Set the IP once here.  Simulator always uses 127.0.0.1 automatically.
    static let deviceLANIP = "YOUR_MAC_LAN_IP"   // e.g. "192.168.1.42"

    // MARK: - Internal

    private static let infoPlistKey = "API_BASE_URL"
    private static let port = 8000

    static var apiBaseURL: URL {
        // 1) Prefer Info.plist key (set via Xcode User-Defined build setting API_BASE_URL)
        if let raw = Bundle.main.object(forInfoDictionaryKey: infoPlistKey) as? String {
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty, let url = URL(string: trimmed) {
                return url
            }
        }

        // 2) Debug auto-select
        #if DEBUG
        let host: String
        #if targetEnvironment(simulator)
        host = "127.0.0.1"
        #else
        host = deviceLANIP
        #endif
        guard let url = URL(string: "http://\(host):\(port)") else {
            fatalError("AppConfig: invalid debug base URL for host \(host)")
        }
        return url
        #else
        // 3) Release — set a real production URL here
        guard let url = URL(string: "https://api.yourdomain.com") else {
            fatalError("AppConfig: invalid release base URL")
        }
        return url
        #endif
    }
}
