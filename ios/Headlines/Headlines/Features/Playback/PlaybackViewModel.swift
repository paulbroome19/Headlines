import Foundation
import AVFoundation

@MainActor
final class PlaybackViewModel: ObservableObject {

    // MARK: - Types

    enum PlaybackState: Equatable {
        case idle
        case loading
        case ready
        case playing
        case paused
        case ended
        case failed(String)
    }

    struct NowPlaying: Equatable {
        let feedID: Int
        let itemIndex: Int
        let item: FeedItem

        /// 0.0 ... 1.0 progress within current story segment
        let progress: Double
    }

    // MARK: - Published state

    @Published private(set) var state: PlaybackState = .idle
    @Published private(set) var nowPlaying: NowPlaying? = nil
    @Published private(set) var lastErrorMessage: String? = nil

    // MARK: - Dependencies

    private let feedsService: FeedsServicing
    private let audioOutputsService: AudioOutputsServicing

    // MARK: - Private state

    private var feed: Feed? = nil
    private var audioOutput: AudioOutput? = nil

    private var player: AVAudioPlayer? = nil
    private var progressTimer: Timer? = nil

    // MARK: - Init

    init(
        feedsService: FeedsServicing,
        audioOutputsService: AudioOutputsServicing = AudioOutputsService()
    ) {
        self.feedsService = feedsService
        self.audioOutputsService = audioOutputsService
    }

    deinit {
        progressTimer?.invalidate()
    }

    // MARK: - Public API

    /// Loads latest feed + latest audio output for that feed, then prepares AVAudioPlayer.
    func loadLatest() async {
        state = .loading
        lastErrorMessage = nil

        stopProgressTimer()
        player?.stop()
        player = nil
        feed = nil
        audioOutput = nil
        nowPlaying = nil

        do {
            // 1) Fetch latest feed
            let latestFeed = try await feedsService.fetchLatestFeed()
            self.feed = latestFeed

            guard let first = latestFeed.items.first else {
                fail("No stories available.")
                return
            }

            // Initial NowPlaying
            nowPlaying = NowPlaying(
                feedID: latestFeed.id,
                itemIndex: 0,
                item: first,
                progress: 0
            )

            // 2) Fetch latest audio output for this feed (gives scriptsOutputID)
            let audio = try await audioOutputsService.fetchLatestAudioOutput(feedID: latestFeed.id)
            self.audioOutput = audio

            // 3) Download wav for scripts_output_id (use service to build URL)
            let remoteURL = audioOutputsService.audioFileURL(forScriptsOutputID: audio.scriptsOutputID)
            let localFileURL = try await downloadAudioFile(from: remoteURL, scriptsOutputID: audio.scriptsOutputID)

            // 4) Prepare AVAudioPlayer
            let p = try AVAudioPlayer(contentsOf: localFileURL)
            p.prepareToPlay()
            self.player = p

            updateNowPlayingFromPlayerTime()
            state = .ready

        } catch {
            fail(error.localizedDescription)
        }
    }

    func play() {
        guard let player else { return }

        switch state {
        case .ready, .paused:
            player.play()
            state = .playing
            startProgressTimer()
        default:
            return
        }
    }

    func pause() {
        guard let player else { return }
        guard state == .playing else { return }

        player.pause()
        state = .paused
        stopProgressTimer()
        updateNowPlayingFromPlayerTime()
    }

    func togglePlayPause() {
        if state == .playing { pause() } else { play() }
    }

    func skipToNext() {
        guard let feed, let player else { return }
        guard !feed.items.isEmpty else { return }

        let currentIndex = currentItemIndexFromPlayerTime()
        let nextIndex = currentIndex + 1

        if nextIndex >= feed.items.count {
            player.currentTime = player.duration
            stopProgressTimer()
            updateNowPlayingFromPlayerTime()
            state = .ended
            return
        }

        seekToItem(index: nextIndex)
        if state == .playing { player.play() }
    }

    func skipToPrevious() {
        guard let feed, let player else { return }
        guard !feed.items.isEmpty else { return }

        let currentIndex = currentItemIndexFromPlayerTime()
        let prevIndex = max(0, currentIndex - 1)

        seekToItem(index: prevIndex)
        if state == .playing { player.play() }
    }

    /// Seek within current story segment (0...1)
    func seek(progress: Double) {
        guard let feed, let player else { return }
        guard !feed.items.isEmpty else { return }

        let clamped = min(max(progress, 0), 1)

        let idx = currentItemIndexFromPlayerTime()
        let seg = segmentDuration(for: player.duration, itemCount: feed.items.count)
        let start = Double(idx) * seg
        player.currentTime = min(player.duration, start + (clamped * seg))

        updateNowPlayingFromPlayerTime()
    }

    // MARK: - Download

    private func downloadAudioFile(from remoteURL: URL, scriptsOutputID: Int) async throws -> URL {
        var request = URLRequest(url: remoteURL)
        request.httpMethod = "GET"

        let (data, response) = try await URLSession.shared.data(for: request)

        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw APIError.badStatus(http.statusCode, "")
        }

        let tmp = FileManager.default.temporaryDirectory
        let fileURL = tmp.appendingPathComponent("feed_audio_\(scriptsOutputID).wav")

        try data.write(to: fileURL, options: .atomic)
        return fileURL
    }

    // MARK: - Timer / progress

    private func startProgressTimer() {
        stopProgressTimer()

        progressTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.tickProgress()
            }
        }
    }

    private func stopProgressTimer() {
        progressTimer?.invalidate()
        progressTimer = nil
    }

    private func tickProgress() {
        guard state == .playing else { return }
        guard let player else { return }

        if player.currentTime >= player.duration {
            stopProgressTimer()
            updateNowPlayingFromPlayerTime()
            state = .ended
            return
        }

        updateNowPlayingFromPlayerTime()
    }

    // MARK: - Mapping player time -> story index/progress

    private func segmentDuration(for total: TimeInterval, itemCount: Int) -> TimeInterval {
        guard itemCount > 0 else { return total }
        return max(0.1, total / Double(itemCount))
    }

    private func currentItemIndexFromPlayerTime() -> Int {
        guard let feed, let player else { return 0 }
        let seg = segmentDuration(for: player.duration, itemCount: feed.items.count)
        let idx = Int(player.currentTime / seg)
        return min(max(idx, 0), max(feed.items.count - 1, 0))
    }

    private func updateNowPlayingFromPlayerTime() {
        guard let feed, let player else { return }
        guard !feed.items.isEmpty else { return }

        let seg = segmentDuration(for: player.duration, itemCount: feed.items.count)
        let idx = currentItemIndexFromPlayerTime()

        let start = Double(idx) * seg
        let within = max(0, player.currentTime - start)
        let progress = min(max(within / seg, 0), 1)

        nowPlaying = NowPlaying(
            feedID: feed.id,
            itemIndex: idx,
            item: feed.items[idx],
            progress: progress
        )
    }

    private func seekToItem(index: Int) {
        guard let feed, let player else { return }
        let seg = segmentDuration(for: player.duration, itemCount: feed.items.count)
        player.currentTime = min(player.duration, Double(index) * seg)
        updateNowPlayingFromPlayerTime()
    }

    private func fail(_ message: String) {
        state = .failed(message)
        lastErrorMessage = message
    }
}
