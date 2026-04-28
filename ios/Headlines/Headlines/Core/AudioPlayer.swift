import Foundation
import AVFoundation

/// Downloads and plays a single MP3. Owned by BriefingViewModel.
/// Not an ObservableObject — callers poll progress via a timer.
@MainActor
final class AudioPlayer {

    private(set) var avPlayer: AVAudioPlayer?

    var duration: TimeInterval    { avPlayer?.duration ?? 0 }
    var currentTime: TimeInterval { avPlayer?.currentTime ?? 0 }
    var isAtEnd: Bool             { (avPlayer?.currentTime ?? 0) >= (avPlayer?.duration ?? 0) }
    var progress: Double {
        guard let p = avPlayer, p.duration > 0 else { return 0 }
        return p.currentTime / p.duration
    }

    func seek(to time: TimeInterval) {
        avPlayer?.currentTime = max(0, min(time, duration))
    }

    func load(from url: URL) throws {
        let p = try AVAudioPlayer(contentsOf: url)
        p.prepareToPlay()
        avPlayer = p
    }

    func play() {
        // Required on physical device: default session category (.soloAmbient) is
        // silenced by the mute switch and doesn't route to speaker correctly.
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
            try AVAudioSession.sharedInstance().setActive(true)
        } catch {
            #if DEBUG
            print("⚠️ AudioPlayer AVAudioSession setup failed: \(error)")
            #endif
        }
        avPlayer?.play()
    }

    func pause() { avPlayer?.pause() }

    func stop() {
        avPlayer?.stop()
        avPlayer = nil
    }

    static func download(from remoteURL: URL, id: Int) async throws -> URL {
        #if DEBUG
        print("🎵 audio download → \(remoteURL)")
        #endif
        let (data, response) = try await URLSession.shared.data(from: remoteURL)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            let body = String(data: data, encoding: .utf8) ?? "(binary \(data.count) bytes)"
            let snippet = String(body.prefix(300))
            #if DEBUG
            print("⚠️ audio download failed \(http.statusCode): \(snippet)")
            #endif
            throw APIError.badStatus(http.statusCode, snippet)
        }
        let localURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("bulletin_\(id).mp3")
        try data.write(to: localURL, options: .atomic)
        #if DEBUG
        print("✅ audio download ok: \(data.count) bytes → \(localURL.lastPathComponent)")
        #endif
        return localURL
    }
}
