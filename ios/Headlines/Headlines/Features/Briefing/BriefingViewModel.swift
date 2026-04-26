import Foundation
import AVFoundation

@MainActor
final class BriefingViewModel: ObservableObject {

    // MARK: - State

    enum State: Equatable {
        case idle
        case loadingProfiles
        case noProfiles
        case profileSelected
        case generating
        case downloadingAudio
        case readyToPlay
        case playing
        case paused
        case ended
        case failed(String)
    }

    // MARK: - Published

    @Published private(set) var state: State = .idle
    @Published private(set) var profiles: [Profile] = []
    @Published private(set) var selectedProfile: Profile?
    @Published private(set) var bulletin: BulletinResult?
    @Published private(set) var progress: Double = 0
    @Published private(set) var audioDuration: TimeInterval = 0

    // MARK: - Dependencies

    let service: ProfileServicing

    // MARK: - Private

    private var player: AVAudioPlayer?
    private var progressTimer: Timer?

    // MARK: - Init

    init(service: ProfileServicing = ProfileService()) {
        self.service = service
    }

    deinit { progressTimer?.invalidate() }

    // MARK: - Computed

    var canGenerate: Bool {
        guard selectedProfile != nil else { return false }
        switch state {
        case .generating, .downloadingAudio, .loadingProfiles: return false
        default: return true
        }
    }

    var canTogglePlayPause: Bool {
        switch state {
        case .readyToPlay, .playing, .paused: return true
        default: return false
        }
    }

    // MARK: - Profile management

    func loadProfiles() async {
        guard state == .idle else { return }
        await fetchProfiles()
    }

    func reloadProfiles() async {
        await fetchProfiles()
    }

    func selectProfile(_ profile: Profile) {
        selectedProfile = profile
        stopPlayer()
        bulletin = nil
        progress = 0
        audioDuration = 0
        state = .profileSelected
    }

    // MARK: - Bulletin generation

    func generateBulletin() async {
        guard let profile = selectedProfile else { return }

        stopPlayer()
        bulletin = nil
        progress = 0
        audioDuration = 0
        state = .generating

        do {
            let result = try await service.generateBulletin(profileID: profile.id)
            bulletin = result

            guard let bulletinId = result.bulletinId else {
                state = .failed("Bulletin generated but no audio is available yet.")
                return
            }

            state = .downloadingAudio
            let remoteURL: URL
            if let urlString = result.audioUrl, let url = URL(string: urlString) {
                remoteURL = url
            } else {
                remoteURL = service.audioFileURL(forBulletinID: bulletinId)
            }
            let localURL = try await downloadAudio(from: remoteURL, bulletinId: bulletinId)

            let p = try AVAudioPlayer(contentsOf: localURL)
            p.prepareToPlay()
            player = p
            audioDuration = p.duration
            progress = 0
            state = .readyToPlay

        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    // MARK: - Playback controls

    func play() {
        guard let player else { return }
        guard state == .readyToPlay || state == .paused else { return }
        player.play()
        state = .playing
        startProgressTimer()
    }

    func pause() {
        guard let player else { return }
        guard state == .playing else { return }
        player.pause()
        stopProgressTimer()
        state = .paused
    }

    func togglePlayPause() {
        if state == .playing { pause() } else { play() }
    }

    // MARK: - Private helpers

    private func fetchProfiles() async {
        state = .loadingProfiles
        do {
            profiles = try await service.fetchProfiles()
            if profiles.isEmpty {
                state = .noProfiles
            } else {
                if let current = selectedProfile,
                   let refreshed = profiles.first(where: { $0.id == current.id }) {
                    selectedProfile = refreshed
                } else if selectedProfile == nil {
                    selectedProfile = profiles.first
                }
                state = .profileSelected
            }
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    private func downloadAudio(from remoteURL: URL, bulletinId: Int) async throws -> URL {
        let (data, response) = try await URLSession.shared.data(from: remoteURL)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw APIError.badStatus(http.statusCode)
        }
        let localURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("bulletin_\(bulletinId).mp3")
        try data.write(to: localURL, options: .atomic)
        return localURL
    }

    private func stopPlayer() {
        stopProgressTimer()
        player?.stop()
        player = nil
    }

    private func startProgressTimer() {
        stopProgressTimer()
        progressTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.tick() }
        }
    }

    private func stopProgressTimer() {
        progressTimer?.invalidate()
        progressTimer = nil
    }

    private func tick() {
        guard state == .playing, let player else { return }
        if player.currentTime >= player.duration {
            stopProgressTimer()
            progress = 1
            state = .ended
            return
        }
        progress = player.duration > 0 ? player.currentTime / player.duration : 0
    }
}
