import AVFoundation
import Combine
import Foundation
import MediaPlayer

// MARK: - Request / response types

private struct ManifestRequest: Encodable {
    let includeTopStories: Bool = true
    enum CodingKeys: String, CodingKey {
        case includeTopStories = "include_top_stories"
    }
}

private struct EventRequest: Encodable {
    let profileId: Int
    let storyHash: String
    let action: String
    let positionPct: Double
    enum CodingKeys: String, CodingKey {
        case profileId   = "profile_id"
        case storyHash   = "story_hash"
        case action
        case positionPct = "position_pct"
    }
}

private struct SummaryRequest: Encodable {
    let profileId: Int
    let events: [EventItem]
    struct EventItem: Encodable {
        let storyHash: String
        let action: String
        let positionPct: Double
        enum CodingKeys: String, CodingKey {
            case storyHash   = "story_hash"
            case action
            case positionPct = "position_pct"
        }
    }
    enum CodingKeys: String, CodingKey {
        case profileId = "profile_id"
        case events
    }
}

// Accepts {"status": "...", "updated": N?} — used for fire-and-forget endpoints.
private struct StatusResponse: Decodable {
    let status: String
    let updated: Int?
}

private struct StoryEvent {
    let storyHash: String
    let action: String
    let positionPct: Double
}

// MARK: - BulletinPlayer

/// Streaming bulletin player backed by AVQueuePlayer.
///
/// Lifecycle:
///   load(profileId:) → manifest fetch → enqueue first 2 segments
///   play()           → player.play(), state → .playing
///   skip()           → fire "skipped" event, advance past story + transition
///   sendSummary()    → batch-fire all accumulated events (call on background/terminate)
///   stop()           → sendSummary + teardown
///
/// Sliding window: item N's readyToPlay triggers enqueue of item N+2. At most 3
/// items are in the queue at once.
///
/// Stall detection: 2 × 2s timer. After 4s total stall, silently skip item.
@MainActor
final class BulletinPlayer: NSObject, ObservableObject {

    // MARK: State

    enum PlayerState: Equatable {
        case idle
        case loadingManifest
        case preparing        // manifest received; gating on readiness (loader shown)
        case buffering        // manifest received; items loading; waiting for play tap
        case playing
        case paused
        case stalled          // buffering mid-playback
        case ended
        case empty            // the filters matched no stories right now (normal, not a failure)
        case failed(String)
    }

    // MARK: Published

    @Published private(set) var playerState: PlayerState = .idle {
        didSet {
            #if DEBUG
            print("🎙 playerState: \(oldValue) → \(playerState)")
            #endif
            // Keep the lock screen / Control Center in sync — MPNowPlayingInfo's
            // playback rate (playing vs paused) is derived from playerState, so it
            // must refresh whenever the state changes, not only on segment advance.
            if playerState != oldValue { updateNowPlaying() }
        }
    }
    @Published private(set) var currentSegment: ManifestSegment?
    @Published private(set) var storyIndex: Int = 0
    @Published private(set) var storyCount: Int = 0
    @Published private(set) var positionSeconds: Double = 0
    @Published private(set) var currentPositionPct: Double = 0
    /// Position (0–1) across the entire story-unit timeline (wrappers excluded).
    @Published private(set) var globalPositionPct: Double = 0
    /// Story units that have been consumed (furthest position ≥ threshold) this session.
    @Published private(set) var consumedStoryHashes: Set<String> = []
    /// Index of the story unit the playhead is currently in (updated on transition entry too).
    @Published private(set) var currentUnitIndex: Int = 0
    /// Loader fill (0–1) shown while `playerState == .preparing`. Smooth + asymptotic;
    /// only reaches 1.0 when audio actually starts. See LoadProgressModel.
    @Published private(set) var loadProgress: Double = 0

    // MARK: Accessors

    private(set) var manifest: BulletinManifest?
    var storySegments: [ManifestSegment] { _storySegments }
    var storyUnits: [StoryUnit] { _storyUnits }
    var totalStoryDurationSeconds: Double { _totalStoryDurationSeconds }
    var profileId: Int? { _profileId }
    var bulletinId: Int? { _bulletinId }

    // MARK: Dependencies

    private let client: APIClient

    // MARK: Private — context

    private var _profileId: Int?
    private var _bulletinId: Int?
    private var segments: [ManifestSegment] = []
    private var _storySegments: [ManifestSegment] = []
    private var _storyUnits: [StoryUnit] = []
    private var _totalStoryDurationSeconds: Double = 0

    // MARK: Private — seek state

    private var lastSkipBackTime: Date?

    // MARK: Private — queue player

    private var player: AVQueuePlayer?
    private var timeObserver: Any?
    private var cancellables = Set<AnyCancellable>()
    // Per-item observations (readyToPlay / failed). Stored separately so we can
    // remove them when items are force-removed from the queue (skip, stall skip).
    private var itemObservations = [ObjectIdentifier: AnyCancellable]()
    private var itemSegmentMap   = [ObjectIdentifier: ManifestSegment]()
    private var nextEnqueueIdx   = 0

    // MARK: Private — stall detection

    private var stallTimer: Timer?
    private var stallCount = 0
    private let stallTimeout: Double = 2.0   // tunable
    private let stallMaxCount = 2            // 2 × 2s = 4s total

    // MARK: Private — event tracking

    // Distinguishes how the previous item left the queue so handleCurrentItemChanged
    // knows whether to fire a "completed" event.
    private enum PreviousOutcome { case natural, userSkip, stallSkip }
    private var prevOutcome: PreviousOutcome = .natural

    private var accumulatedEvents = [StoryEvent]()

    // MARK: Init / deinit

    init(client: APIClient = APIClient(baseURL: AppConfig.apiBaseURL)) {
        self.client = client
        super.init()
        setupAudioSession()
        setupRemoteCommands()
        registerSystemObservers()
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
        let cc = MPRemoteCommandCenter.shared()
        cc.playCommand.removeTarget(self)
        cc.pauseCommand.removeTarget(self)
        cc.nextTrackCommand.removeTarget(self)
    }

    // MARK: - Public API

    func load(profileId: Int) async {
        // Stop any current playback (sends summary if mid-session).
        stopInternal(sendEvents: true)

        _profileId = profileId
        loadProgress = 0
        playerState = .loadingManifest

        do {
            let m: BulletinManifest = try await client.post(
                "data/profiles/\(profileId)/manifest",
                body: ManifestRequest()
            )
            manifest = m
            _bulletinId = m.bulletinId
            segments = m.segments
            _storySegments = segments.filter { $0.isStory }
            _storyUnits = Self.buildStoryUnits(from: segments)
            _totalStoryDurationSeconds = _storyUnits.reduce(0) { $0 + $1.totalDurationSeconds }
            storyCount = _storySegments.count
            storyIndex = 0
            currentUnitIndex = 0
            globalPositionPct = 0

            #if DEBUG
            print("🎙 BulletinPlayer: manifest ok — \(segments.count) segments, \(storyCount) stories")
            for seg in segments {
                print("   [\(seg.index)] \(seg.type.padding(toLength: 12, withPad: " ", startingAt: 0))  \(seg.url.split(separator: "/").last ?? "?")")
            }
            #endif

            // Readiness gate (the real "no-greeting" fix): don't enqueue/play until
            // the intro (greeting) + first story audio are ready, so the greeting is
            // never reached-before-ready and skipped. Drives the loader meanwhile.
            await gateOnReadinessThenPlay()

        } catch {
            #if DEBUG
            print("🎙 BulletinPlayer: load failed — \(error)")
            #endif
            // A "no stories match these filters" result is a normal empty state, not
            // a failure — surface it distinctly so the UI shows a graceful message.
            if let api = error as? APIError, api.isNoStoriesMatch {
                playerState = .empty
            } else {
                playerState = .failed(error.localizedDescription)
            }
        }
    }

    // MARK: - Readiness gate + loader (Parts 2 & 3)

    /// After the manifest, poll the readiness signal (~every 0.5s), drive the smooth
    /// loader, and begin playback only once `safe_to_start` (intro + first story
    /// audio ready) — so the greeting is never reached-before-ready and skipped.
    /// Falls back to best-effort playback if readiness is unavailable or slow, so it
    /// never hangs. Stays in `.preparing` (loader visible) until audio actually starts.
    private func gateOnReadinessThenPlay() async {
        guard let bid = _bulletinId else {
            setupQueuePlayer(); playerState = .buffering; play(); return
        }

        playerState = .preparing
        var model = LoadProgressModel()
        // Creep toward the pre-start ceiling from the outset. The first phase
        // (manifest → assembled) is the long ~8s blocking one and reports no honest
        // intermediate progress, so the bar must CREEP through it continuously (so the
        // Solari status words advance and are readable) rather than stall just below
        // the next milestone. The slow creep rate spreads that motion over the phase;
        // real milestones still snap the bar forward as they arrive.
        model.reachedMilestone(floor: LoadProgressModel.manifestFloor,
                               nextCap: LoadProgressModel.preStartCap)
        loadProgress = model.progress

        let tickInterval = 0.05                  // 20 fps display cadence
        let pollEveryTicks = 10                  // 10 × 0.05s ≈ poll every 0.5s
        let maxTicks = Int(60.0 / tickInterval)  // 60s hard cap → never hang

        var t = 0
        while t < maxTicks {
            // Poll readiness immediately on the first tick, then ~every 0.5s.
            if t % pollEveryTicks == 0 {
                if let r: BulletinReadiness = try? await client.get("data/bulletins/\(bid)/readiness") {
                    // A critical segment failed → safe_to_start can never fire. Stop
                    // and surface an error instead of holding the loader at ~95%.
                    if r.isBlocked {
                        playerState = .failed("This briefing was delayed — a segment couldn't be prepared.")
                        return
                    }
                    applyReadinessMilestones(&model, r)
                    loadProgress = model.progress
                    if r.safeToStart { break }
                } else if t == 0 {
                    // Readiness unavailable (e.g. older backend) — don't gate; best effort.
                    break
                }
            }
            // Slow continuous creep toward the ceiling — spreads motion across the
            // whole blocking phase (~0.15→~0.57 over 8s, crossing several word bands)
            // so it's never frozen; milestones snap it forward when they land.
            model.tick(dt: tickInterval, ratePerSec: LoadProgressModel.creepRate)
            loadProgress = model.progress
            try? await Task.sleep(nanoseconds: UInt64(tickInterval * 1_000_000_000))
            t += 1
        }

        // Ready (or gave up): enqueue and start. Hold near-complete but < 1.0 — audio
        // actually starting is what snaps loadProgress to 1.0 (handleTimeControlStatus).
        model.reachedMilestone(floor: LoadProgressModel.firstStoryFloor,
                               nextCap: LoadProgressModel.preStartCap)
        loadProgress = model.progress
        setupQueuePlayer()
        activateAudioSession()
        player?.play()
        // Reflect playback in state directly — audio is gated-ready, so don't wait for
        // a timeControlStatus(.playing) edge (AVQueuePlayer may not emit one). This snaps
        // the loader to complete and shows the transport with the correct pause icon.
        // handleTimeControlStatus still handles genuine stalls/resumes from here.
        loadProgress = 1.0
        playerState = .playing
    }

    private func applyReadinessMilestones(_ model: inout LoadProgressModel, _ r: BulletinReadiness) {
        if r.assembled {
            model.reachedMilestone(floor: LoadProgressModel.assembledFloor,
                                   nextCap: LoadProgressModel.introReadyFloor)
        }
        if r.introReady {
            model.reachedMilestone(floor: LoadProgressModel.introReadyFloor,
                                   nextCap: LoadProgressModel.firstStoryFloor)
        }
        if r.introReady && r.firstStoryReady {
            model.reachedMilestone(floor: LoadProgressModel.firstStoryFloor,
                                   nextCap: LoadProgressModel.preStartCap)
        }
        // Prefer the server's honest 0–100 progress as a floor (PR #70), clamped
        // below preStartCap so the bar still never reaches 1.0 before audio starts.
        if let pf = r.progressFraction {
            model.reachedMilestone(floor: min(pf, LoadProgressModel.preStartCap),
                                   nextCap: LoadProgressModel.preStartCap)
        }
    }

    /// Seek to a story unit's start and begin playing it. Used by the tracklist so
    /// any row — including already-played ones — is replayable on tap, even when the
    /// player is paused (plain `seekToStoryUnit` only resumes if it was already playing).
    func playStoryUnit(at index: Int) {
        seekToStoryUnit(at: index)
        play()
    }

    func play() {
        switch playerState {
        case .buffering, .paused, .stalled:
            activateAudioSession()
            player?.play()
            playerState = .playing   // reflect immediately (incl. stall-resume) — don't wait for a KVO edge
        default:
            break
        }
    }

    func pause() {
        guard playerState == .playing || playerState == .stalled else { return }
        player?.pause()
        cancelStallTimer()
        playerState = .paused
    }

    func togglePlayPause() {
        if playerState == .playing || playerState == .stalled { pause() } else { play() }
    }

    func skip() {
        guard let p = player else { return }

        // Fire skipped event for the current story before advancing.
        if let seg = currentSegment, seg.isStory, let hash = seg.storyHash {
            let ev = StoryEvent(storyHash: hash, action: "skipped", positionPct: currentPositionPct)
            fireEvent(ev)
            accumulatedEvents.append(ev)
        }
        prevOutcome = .userSkip

        // Remove the following transition (if any) so we land on the next story.
        let items = p.items()
        if items.count >= 2 {
            let next = items[1]
            if let nextSeg = itemSegmentMap[ObjectIdentifier(next)], nextSeg.type == "transition" {
                let key = ObjectIdentifier(next)
                p.remove(next)
                itemObservations.removeValue(forKey: key)
                itemSegmentMap.removeValue(forKey: key)
                // Fill the gap with the next un-enqueued segment.
                enqueueNext()
            }
        }

        p.advanceToNextItem()
    }

    func stop() {
        stopInternal(sendEvents: true)
    }

    func sendSummary() {
        guard let pid = _profileId, let bid = _bulletinId else { return }

        // Append abandoned event for the story currently mid-play.
        if let seg = currentSegment, seg.isStory, let hash = seg.storyHash,
           playerState == .playing || playerState == .paused || playerState == .stalled {
            let ev = StoryEvent(storyHash: hash, action: "abandoned", positionPct: currentPositionPct)
            accumulatedEvents.append(ev)
        }

        guard !accumulatedEvents.isEmpty else { return }

        let events = accumulatedEvents
        accumulatedEvents = []

        let req = SummaryRequest(
            profileId: pid,
            events: events.map { .init(storyHash: $0.storyHash, action: $0.action, positionPct: $0.positionPct) }
        )
        Task { _ = try? await self.client.post("data/bulletins/\(bid)/summary", body: req) as StatusResponse }
    }

    // MARK: - Audio session

    private func setupAudioSession() {
        activateAudioSession()
    }

    private func activateAudioSession() {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default, options: [])
            try AVAudioSession.sharedInstance().setActive(true)
        } catch {
            #if DEBUG
            print("⚠️ BulletinPlayer audio session: \(error)")
            #endif
        }
    }

    // MARK: - System observers (interruptions + headphone unplug)

    private func registerSystemObservers() {
        NotificationCenter.default.addObserver(
            self, selector: #selector(handleInterruption),
            name: AVAudioSession.interruptionNotification,
            object: nil
        )
        NotificationCenter.default.addObserver(
            self, selector: #selector(handleRouteChange),
            name: AVAudioSession.routeChangeNotification,
            object: nil
        )
    }

    // NotificationCenter delivers these on whatever thread AVAudioSession posts on
    // (often a background thread). Extract the primitives, then hop to the main actor
    // before touching @Published state — mutating it off-main silently breaks SwiftUI
    // observation on device (the in-app play/pause icon stops reflecting state).
    @objc private func handleInterruption(_ note: Notification) {
        guard let ui = note.userInfo,
              let raw = ui[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        let shouldResume = (ui[AVAudioSessionInterruptionOptionKey] as? UInt).map {
            AVAudioSession.InterruptionOptions(rawValue: $0).contains(.shouldResume)
        } ?? false
        Task { @MainActor [weak self] in
            guard let self else { return }
            switch type {
            case .began:
                if self.playerState == .playing { self.player?.pause(); self.playerState = .paused }
            case .ended:
                if shouldResume { self.play() }
            @unknown default: break
            }
        }
    }

    @objc private func handleRouteChange(_ note: Notification) {
        guard let ui = note.userInfo,
              let raw = ui[AVAudioSessionRouteChangeReasonKey] as? UInt,
              AVAudioSession.RouteChangeReason(rawValue: raw) == .oldDeviceUnavailable else { return }
        Task { @MainActor [weak self] in
            guard let self, self.playerState == .playing else { return }
            self.player?.pause()
            self.playerState = .paused
        }
    }

    // MARK: - Remote command center

    private func setupRemoteCommands() {
        let cc = MPRemoteCommandCenter.shared()
        // MPRemoteCommandCenter may deliver these off the main thread. Hop to the main
        // actor before touching @Published playerState — otherwise a lock-screen /
        // Control Center tap mutates it off-main and the in-app icon strands/desyncs
        // (same fix already applied to the AVAudioSession notification handlers above).
        cc.playCommand.addTarget  { [weak self] _ in Task { @MainActor [weak self] in self?.play() };  return .success }
        cc.pauseCommand.addTarget { [weak self] _ in Task { @MainActor [weak self] in self?.pause() }; return .success }
        cc.nextTrackCommand.addTarget { [weak self] _ in Task { @MainActor [weak self] in self?.skip() }; return .success }
        cc.nextTrackCommand.isEnabled      = true
        cc.previousTrackCommand.isEnabled  = false
        cc.changePlaybackPositionCommand.isEnabled = false
    }

    // MARK: - Queue player lifecycle

    private func setupQueuePlayer() {
        teardownQueuePlayer()

        let q = AVQueuePlayer()
        q.automaticallyWaitsToMinimizeStalling = false   // tunable
        player = q

        // Observe currentItem to detect item advancement.
        q.publisher(for: \.currentItem)
            .receive(on: RunLoop.main)
            .sink { [weak self] item in self?.handleCurrentItemChanged(item) }
            .store(in: &cancellables)

        // Observe timeControlStatus for stall detection.
        q.publisher(for: \.timeControlStatus)
            .receive(on: RunLoop.main)
            .sink { [weak self] status in self?.handleTimeControlStatus(status) }
            .store(in: &cancellables)

        // 0.1s periodic time observer for position tracking.
        let interval = CMTime(seconds: 0.1, preferredTimescale: 600)
        timeObserver = q.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            MainActor.assumeIsolated { self?.handlePeriodicTime(time) }
        }

        // Initial sliding window: load first 2 segments.
        nextEnqueueIdx = 0
        enqueueNext()
        enqueueNext()
    }

    private func teardownQueuePlayer() {
        cancelStallTimer()
        if let obs = timeObserver { player?.removeTimeObserver(obs); timeObserver = nil }
        player?.pause()
        player = nil
        cancellables.removeAll()
        itemObservations.removeAll()
        itemSegmentMap.removeAll()
        nextEnqueueIdx = 0
    }

    private func stopInternal(sendEvents: Bool) {
        if sendEvents { sendSummary() }
        teardownQueuePlayer()
        manifest       = nil
        segments       = []
        _storySegments = []
        _storyUnits    = []
        _totalStoryDurationSeconds = 0
        storyCount     = 0
        storyIndex     = 0
        currentUnitIndex     = 0
        currentSegment       = nil
        positionSeconds      = 0
        currentPositionPct   = 0
        globalPositionPct    = 0
        consumedStoryHashes  = []
        accumulatedEvents    = []
        prevOutcome          = .natural
        lastSkipBackTime     = nil
        _profileId           = nil
        _bulletinId          = nil
        playerState          = .idle
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    // MARK: - Sliding window enqueue

    private func enqueueNext() {
        guard let p = player, nextEnqueueIdx < segments.count else { return }
        let seg = segments[nextEnqueueIdx]
        nextEnqueueIdx += 1

        guard let url = URL(string: seg.url) else {
            enqueueNext()   // skip malformed URL, try next
            return
        }

        let item = AVPlayerItem(url: url)
        let key  = ObjectIdentifier(item)
        itemSegmentMap[key] = seg
        p.insert(item, after: nil)

        #if DEBUG
        print("🎙 enqueue [\(seg.index)] \(seg.type)  \(url.lastPathComponent)")
        #endif

        // Observe readyToPlay (triggers N+2 pre-fetch) and failed (silent skip).
        itemObservations[key] = item.publisher(for: \.status)
            .receive(on: RunLoop.main)
            .sink { [weak self, weak item] status in
                guard let self, let item else { return }
                let label = self.itemSegmentMap[ObjectIdentifier(item)].map { "[\($0.index)] \($0.type)" } ?? "?"
                switch status {
                case .readyToPlay:
                    #if DEBUG
                    print("🎙 readyToPlay \(label)")
                    #endif
                    self.enqueueNext()
                case .failed:
                    #if DEBUG
                    let err = item.error?.localizedDescription ?? "unknown"
                    print("🎙 FAILED \(label) — \(err)")
                    #endif
                    self.handleItemFailed(item)
                default:
                    break
                }
            }
    }

    // MARK: - Playback callbacks

    private func handleCurrentItemChanged(_ item: AVPlayerItem?) {
        #if DEBUG
        if let item {
            let label = itemSegmentMap[ObjectIdentifier(item)].map { "[\($0.index)] \($0.type)" } ?? "unknown"
            print("🎙 currentItemChanged → \(label)  playerState=\(playerState)")
        } else {
            print("🎙 currentItemChanged → nil (queue exhausted)  playerState=\(playerState)")
        }
        #endif
        let prevSeg = currentSegment
        let outcome = prevOutcome
        prevOutcome = .natural

        // Fire completion event for the previous story on natural advancement.
        if let prev = prevSeg, prev.isStory, let hash = prev.storyHash, outcome == .natural {
            let ev = StoryEvent(storyHash: hash, action: "completed", positionPct: 1.0)
            fireEvent(ev)
            accumulatedEvents.append(ev)
            consumedStoryHashes.insert(hash)
        }

        guard let item = item else {
            currentSegment = nil
            // Guard against spurious nil from AVQueuePlayer init AND from seek queue rebuilds.
            // seekToStoryUnit sets playerState = .buffering before removeAllItems() so both
            // cases are covered by the same check.
            guard playerState != .buffering else { return }
            playerState    = .ended
            updateNowPlaying()
            return
        }

        let seg = itemSegmentMap[ObjectIdentifier(item)]
        currentSegment     = seg
        positionSeconds    = 0
        currentPositionPct = 0

        if let seg {
            if let unit = storyUnit(forSegment: seg) {
                currentUnitIndex = unit.index
                if seg.isStory { storyIndex = unit.index }
            }
        }

        updateNowPlaying()
    }

    private func handleTimeControlStatus(_ status: AVPlayer.TimeControlStatus) {
        #if DEBUG
        let statusName = status == .playing ? "playing" : status == .paused ? "paused" : "waitingToPlay"
        print("🎙 timeControlStatus → \(statusName)  playerState=\(playerState)")
        #endif
        switch status {
        case .playing:
            cancelStallTimer()
            stallCount = 0
            if playerState == .preparing {
                // Audio actually started — snap the loader to complete and dismiss it.
                loadProgress = 1.0
                playerState = .playing
            } else if playerState == .buffering || playerState == .stalled {
                playerState = .playing
            }
        case .waitingToPlayAtSpecifiedRate:
            // Only start stall detection during active playback (not initial buffering).
            if playerState == .playing {
                playerState = .stalled
                startStallTimer()
            }
        case .paused:
            cancelStallTimer()
        @unknown default:
            break
        }
    }

    private func handlePeriodicTime(_ time: CMTime) {
        let secs = CMTimeGetSeconds(time)
        guard secs.isFinite else { return }
        positionSeconds = secs

        if let seg = currentSegment, seg.isStory {
            currentPositionPct = seg.durationSeconds > 0 ? min(1.0, secs / seg.durationSeconds) : 0
        }

        updateGlobalPosition(positionWithinSegment: secs)

        // Keep lock screen elapsed time current without rebuilding the full info dict.
        if var info = MPNowPlayingInfoCenter.default().nowPlayingInfo {
            info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = secs
            MPNowPlayingInfoCenter.default().nowPlayingInfo = info
        }
    }

    private func updateGlobalPosition(positionWithinSegment secs: Double) {
        guard let seg = currentSegment,
              let unit = storyUnit(forSegment: seg),
              _totalStoryDurationSeconds > 0 else { return }

        let offsetInUnit: Double
        if let trans = unit.transitionSegment, seg.index == trans.index {
            offsetInUnit = secs  // inside the transition part
        } else {
            offsetInUnit = unit.transitionDurationSeconds + secs  // inside the story part
        }

        let absolute = unit.cumulativeStartSeconds + offsetInUnit
        globalPositionPct = min(1.0, absolute / _totalStoryDurationSeconds)
    }

    private func handleItemFailed(_ item: AVPlayerItem) {
        #if DEBUG
        print("⚠️ BulletinPlayer: item failed — \(item.error?.localizedDescription ?? "unknown")")
        #endif
        prevOutcome = .stallSkip
        if player?.currentItem === item {
            player?.advanceToNextItem()
        } else {
            let key = ObjectIdentifier(item)
            player?.remove(item)
            itemObservations.removeValue(forKey: key)
            itemSegmentMap.removeValue(forKey: key)
            enqueueNext()  // replace the failed item in the window
        }
    }

    // MARK: - Seek / navigation

    /// Jump to story unit `index`, optionally at `offsetSeconds` within that unit
    /// (0 = start of its transition, or story if no transition).
    func seekToStoryUnit(at index: Int, offsetSeconds: Double = 0) {
        let clamped = max(0, min(index, _storyUnits.count - 1))
        guard clamped < _storyUnits.count, let p = player else { return }
        let unit = _storyUnits[clamped]
        let wasPlaying = playerState == .playing || playerState == .stalled

        // Fire skipped event for the current story when seeking forward.
        if clamped > storyIndex, let seg = currentSegment, seg.isStory,
           let hash = seg.storyHash {
            let ev = StoryEvent(storyHash: hash, action: "skipped", positionPct: currentPositionPct)
            fireEvent(ev)
            accumulatedEvents.append(ev)
        }
        prevOutcome = .userSkip

        // Determine which segment to start from and the seek offset within it.
        let startArrayIdx: Int
        let seekSeconds: Double
        if offsetSeconds < unit.transitionDurationSeconds,
           let trans = unit.transitionSegment,
           let idx = segments.firstIndex(where: { $0.index == trans.index }) {
            startArrayIdx = idx
            seekSeconds   = offsetSeconds
        } else if let idx = segments.firstIndex(where: { $0.index == unit.storySegment.index }) {
            startArrayIdx = idx
            seekSeconds   = max(0, offsetSeconds - unit.transitionDurationSeconds)
        } else {
            return
        }

        // Update tracking immediately for responsive UI.
        storyIndex       = clamped
        currentUnitIndex = clamped

        // Set .buffering BEFORE removeAllItems(). The currentItemChanged(nil) KVO is deferred
        // to the next RunLoop iteration via .receive(on: RunLoop.main), so playerState must
        // already be .buffering by then to satisfy the existing nil guard. _isSeeking would
        // be set synchronously and cleared before the deferred callback fires — wrong timing.
        playerState = .buffering
        p.pause()
        p.removeAllItems()
        itemObservations.removeAll()
        itemSegmentMap.removeAll()

        nextEnqueueIdx = startArrayIdx
        enqueueNext()
        enqueueNext()

        if seekSeconds > 0 {
            let t = CMTime(seconds: seekSeconds, preferredTimescale: 600)
            p.seek(to: t, toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    if wasPlaying {
                        self.player?.play()
                        // Set .playing directly — the removeAllItems()→re-enqueue and the
                        // in-buffer seek can leave timeControlStatus already .playing, so the
                        // KVO edge may never fire and the state would strand at .buffering.
                        self.playerState = .playing
                    }
                }
            }
        } else {
            if wasPlaying {
                p.play()
                playerState = .playing   // see note above — don't depend on a KVO edge
            }
        }
    }

    /// Seek to a fraction (0–1) across the story-unit timeline.
    func seekToGlobalFraction(_ fraction: Double) {
        guard !_storyUnits.isEmpty, _totalStoryDurationSeconds > 0 else { return }
        let targetSecs = max(0, min(1, fraction)) * _totalStoryDurationSeconds

        var unitIndex    = _storyUnits.count - 1
        var offsetInUnit = _storyUnits.last!.totalDurationSeconds

        for unit in _storyUnits {
            let unitEnd = unit.cumulativeStartSeconds + unit.totalDurationSeconds
            if targetSecs <= unitEnd {
                unitIndex    = unit.index
                offsetInUnit = targetSecs - unit.cumulativeStartSeconds
                break
            }
        }

        seekToStoryUnit(at: unitIndex, offsetSeconds: max(0, offsetInUnit))
    }

    /// Skip backward: restart current unit, or go to previous unit if near the start
    /// or called again within 2 s (standard podcast double-tap-back pattern).
    func skipBack() {
        let now = Date()
        let nearStart    = positionSeconds < 2.0
        let recentBack   = lastSkipBackTime.map { now.timeIntervalSince($0) < 2.0 } ?? false

        if nearStart || recentBack {
            seekToStoryUnit(at: max(0, currentUnitIndex - 1))
        } else {
            seekToStoryUnit(at: currentUnitIndex)
        }
        lastSkipBackTime = now
    }

    // MARK: - Stall detection

    private func startStallTimer() {
        cancelStallTimer()
        stallTimer = Timer.scheduledTimer(withTimeInterval: stallTimeout, repeats: false) { [weak self] _ in
            Task { @MainActor [weak self] in self?.handleStallTimeout() }
        }
    }

    private func cancelStallTimer() {
        stallTimer?.invalidate()
        stallTimer = nil
    }

    private func handleStallTimeout() {
        // Never stall-skip the intro (greeting). It's unique per user and can take a
        // few seconds to generate; skipping it is exactly the "no-greeting" bug. Wait
        // for it instead. (The readiness gate makes this rare, but this is the belt.)
        if currentSegment?.type == "intro" {
            stallCount = 0
            startStallTimer()   // keep waiting; do not skip
            return
        }
        stallCount += 1
        if stallCount >= stallMaxCount {
            stallCount  = 0
            prevOutcome = .stallSkip
            // Reset to .playing so stall detection can start fresh for the next item.
            playerState = .playing
            player?.advanceToNextItem()
        } else {
            startStallTimer()
        }
    }

    // MARK: - Story unit helpers

    /// Returns the StoryUnit that contains the given segment (transition or story).
    private func storyUnit(forSegment seg: ManifestSegment) -> StoryUnit? {
        _storyUnits.first {
            $0.storySegment.index == seg.index ||
            $0.transitionSegment?.index == seg.index
        }
    }

    /// Builds the ordered story-unit list with pre-computed cumulative start times.
    static func buildStoryUnits(from segments: [ManifestSegment]) -> [StoryUnit] {
        var units: [StoryUnit] = []
        var pendingTransition: ManifestSegment?
        var cumulativeSecs = 0.0

        for seg in segments {
            switch seg.type {
            case "transition":
                pendingTransition = seg
            case "story":
                let unit = StoryUnit(
                    index: units.count,
                    transitionSegment: pendingTransition,
                    storySegment: seg,
                    cumulativeStartSeconds: cumulativeSecs
                )
                cumulativeSecs += unit.totalDurationSeconds
                units.append(unit)
                pendingTransition = nil
            default:
                pendingTransition = nil   // sting / intro / outro reset the pending transition
            }
        }
        return units
    }

    // MARK: - Event firing

    private func fireEvent(_ ev: StoryEvent) {
        guard let pid = _profileId, let bid = _bulletinId else { return }
        let req = EventRequest(
            profileId:   pid,
            storyHash:   ev.storyHash,
            action:      ev.action,
            positionPct: ev.positionPct
        )
        Task { _ = try? await self.client.post("data/bulletins/\(bid)/event", body: req) as StatusResponse }
    }

    // MARK: - Lock screen

    private func updateNowPlaying() {
        guard let seg = currentSegment else {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        let title = seg.isStory ? (seg.title ?? "Headlines") : "Headlines"
        var info: [String: Any] = [
            MPMediaItemPropertyTitle:                   title,
            MPMediaItemPropertyArtist:                  "Headlines",
            MPNowPlayingInfoPropertyElapsedPlaybackTime: positionSeconds,
            MPMediaItemPropertyPlaybackDuration:        seg.durationSeconds,
            MPNowPlayingInfoPropertyPlaybackRate:       (playerState == .playing) ? 1.0 : 0.0,
        ]
        if seg.isStory, storyCount > 0 {
            info[MPNowPlayingInfoPropertyChapterNumber] = storyIndex + 1
            info[MPNowPlayingInfoPropertyChapterCount]  = storyCount
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }
}

// MARK: - Loader progress model

/// Smooth, honest loader progress for the first-play readiness gate (Part 3).
///
///  - Readiness milestones set a **floor** the bar snaps up to (manifest assembled,
///    intro ready, first-story ready).
///  - Between milestones the bar **creeps** via `tick`, easing asymptotically toward
///    a **cap** just below the next milestone — always moving, never overtaking reality.
///  - It **never reaches 1.0** until `complete()` (called only when audio actually
///    starts), so the bar never sits "finished" in silence.
///
/// Pure value type, no timers/IO — unit-testable in isolation. Kept in this file
/// (rather than a new file) so it needs no Xcode target/pbxproj change.
struct LoadProgressModel {

    // Milestone floors (0–1). Tunable; ordered.
    static let manifestFloor:   Double = 0.15   // manifest received
    static let assembledFloor:  Double = 0.25   // bulletin assembled
    static let introReadyFloor: Double = 0.60   // greeting audio ready
    static let firstStoryFloor: Double = 0.85   // first story ready (safe_to_start imminent)
    static let preStartCap:     Double = 0.97   // hard ceiling before audio actually starts
    /// Slow asymptotic creep rate for the blocking pre-readiness phase, tuned so the
    /// bar spreads ~0.15→~0.57 over ~8s (crossing several Solari word bands) and keeps
    /// inching up even on a long assembly — never frozen, never 1.0 until audio starts.
    static let creepRate:       Double = 0.09

    private(set) var progress: Double = 0
    private var floor: Double = 0
    private var cap: Double = LoadProgressModel.manifestFloor

    /// Snap up to a milestone `floor` and set the creep ceiling. Floors and progress
    /// only ever move forward (monotonic); the cap is clamped ≤ preStartCap but never
    /// below current progress, so a later milestone can only snap the bar FORWARD —
    /// it can never yank it backward (which would read as a stutter).
    mutating func reachedMilestone(floor f: Double, nextCap c: Double) {
        floor = max(floor, f)
        progress = max(progress, floor)
        cap = max(min(c, LoadProgressModel.preStartCap), progress)
    }

    /// Creep asymptotically toward the current cap. `dt` = seconds since last tick.
    /// Covers a fraction `1 - e^(-rate·dt)` of the remaining gap each tick, so it
    /// approaches the cap smoothly and never overshoots it.
    mutating func tick(dt: Double, ratePerSec: Double = 0.6) {
        guard dt > 0, progress < cap else { return }
        let remaining = cap - progress
        progress = min(cap, progress + remaining * (1 - exp(-ratePerSec * dt)))
    }

    /// Audio has actually started — snap to 100% and dismiss the loader.
    mutating func complete() {
        floor = 1.0
        cap = 1.0
        progress = 1.0
    }
}
