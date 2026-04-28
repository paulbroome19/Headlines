import Foundation

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
    @Published private(set) var currentStoryIndex: Int? = nil

    // MARK: - Dependencies

    let profileService: ProfileServicing
    private let bulletinService: BulletinServicing
    private let audioPlayer = AudioPlayer()
    private var progressTimer: Timer?

    // MARK: - Init

    init(
        profileService: ProfileServicing  = ProfileService(),
        bulletinService: BulletinServicing = BulletinService()
    ) {
        self.profileService  = profileService
        self.bulletinService = bulletinService
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

    /// True when every story in the bulletin carries a start_time.
    /// Controls are hidden until the backend provides this data.
    var hasStoryTimings: Bool {
        guard let stories = bulletin?.stories, !stories.isEmpty else { return false }
        return stories.allSatisfy { $0.startTime != nil }
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
        stopAudio()
        bulletin          = nil
        progress          = 0
        audioDuration     = 0
        currentStoryIndex = nil
        state = .profileSelected
    }

    // MARK: - Bulletin generation

    func generateBulletin() async {
        guard let profile = selectedProfile else { return }

        stopAudio()
        bulletin          = nil
        progress          = 0
        audioDuration     = 0
        currentStoryIndex = nil
        state = .generating

        do {
            let result = try await bulletinService.generateBulletin(profileID: profile.id)
            bulletin = result

            guard let bulletinId = result.bulletinId else {
                state = .failed("Bulletin generated but no audio is available yet.")
                return
            }

            state = .downloadingAudio
            let remoteURL = resolvedAudioURL(audioUrl: result.audioUrl, bulletinId: bulletinId)
            #if DEBUG
            print("🎵 bulletin audio URL: \(remoteURL)")
            #endif
            let localURL = try await AudioPlayer.download(from: remoteURL, id: bulletinId)
            try audioPlayer.load(from: localURL)
            audioDuration = audioPlayer.duration
            progress      = 0
            currentStoryIndex = resolveCurrentStoryIndex(at: 0)
            state         = .readyToPlay

        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    // MARK: - Playback controls

    func play() {
        guard canTogglePlayPause else { return }
        audioPlayer.play()
        state = .playing
        startProgressTimer()
    }

    func pause() {
        guard state == .playing else { return }
        audioPlayer.pause()
        stopProgressTimer()
        state = .paused
    }

    func togglePlayPause() {
        if state == .playing { pause() } else { play() }
    }

    // MARK: - Story navigation

    func seekToStory(at index: Int) {
        guard let bulletin = bulletin,
              bulletin.stories.indices.contains(index),
              let startTime = bulletin.stories[index].startTime,
              audioDuration > 0 else { return }
        audioPlayer.seek(to: startTime)
        progress          = startTime / audioDuration
        currentStoryIndex = index
    }

    func previousStory() {
        guard let current = currentStoryIndex,
              let bulletin = bulletin,
              let startTime = bulletin.stories[current].startTime else { return }
        // If more than 3 s into the current story, restart it; otherwise go to previous.
        let elapsed = audioPlayer.currentTime - startTime
        if elapsed > 3.0 {
            seekToStory(at: current)
        } else {
            seekToStory(at: max(0, current - 1))
        }
    }

    func nextStory() {
        guard let current = currentStoryIndex,
              let bulletin = bulletin else { return }
        seekToStory(at: min(bulletin.stories.count - 1, current + 1))
    }

    // MARK: - Private

    private func fetchProfiles() async {
        state = .loadingProfiles
        do {
            profiles = try await profileService.fetchProfiles()
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

    private func stopAudio() {
        stopProgressTimer()
        audioPlayer.stop()
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
        guard state == .playing else { return }
        if audioPlayer.isAtEnd {
            stopProgressTimer()
            progress          = 1
            currentStoryIndex = resolveCurrentStoryIndex(at: audioDuration)
            state             = .ended
            return
        }
        progress          = audioPlayer.progress
        currentStoryIndex = resolveCurrentStoryIndex(at: audioPlayer.currentTime)
    }

    /// Returns the index of the story whose start_time is <= currentTime,
    /// or nil if timing data is not available.
    private func resolveCurrentStoryIndex(at currentTime: TimeInterval) -> Int? {
        guard let stories = bulletin?.stories, !stories.isEmpty,
              stories.first?.startTime != nil else { return nil }
        var result: Int? = nil
        for (i, story) in stories.enumerated() {
            if let st = story.startTime, st <= currentTime {
                result = i
            }
        }
        return result
    }

    // Prefer a non-localhost audio_url from the server (S3/CDN).
    // If the URL is localhost/127.0.0.1 (cached from a simulator run) or absent,
    // fall back to the dev serving route resolved against AppConfig.apiBaseURL.
    private func resolvedAudioURL(audioUrl: String?, bulletinId: Int) -> URL {
        if let urlString = audioUrl,
           let url = URL(string: urlString),
           let host = url.host,
           !["localhost", "127.0.0.1"].contains(host) {
            return url
        }
        return bulletinService.audioFileURL(forBulletinID: bulletinId)
    }
}
