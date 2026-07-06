import AVFoundation
import Combine
import Foundation
import MediaPlayer
import UIKit

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
    /// Elapsed seconds across the FULL audio timeline — every segment, intro → stories →
    /// outro. Counts from the moment audio starts (the greeting), so the timer/bar advance
    /// continuously from 0 like a podcast player (the story-only globalPositionPct left the
    /// intro as dead 0:00 time). Paired with `totalDurationSeconds`.
    @Published private(set) var playbackElapsedSeconds: Double = 0
    /// Story units that have been consumed (furthest position ≥ threshold) this session.
    @Published private(set) var consumedStoryHashes: Set<String> = []
    /// Index of the story unit the playhead is currently in (updated on transition entry too).
    @Published private(set) var currentUnitIndex: Int = 0
    /// Loader fill (0–1) shown while `playerState == .preparing`. Smooth + asymptotic;
    /// only reaches 1.0 when audio actually starts. See LoadProgressModel.
    @Published private(set) var loadProgress: Double = 0
    /// Set true for a beat when the briefing is READY, so the loader bar does its final
    /// raise to 100% (see LoaderTiming.barFill / nearFullCeiling) before the loader
    /// dismisses — the justified final jump. Reset per load.
    @Published private(set) var loaderComplete = false

    /// STREAMING: per-segment-index audio readiness (from /readiness), driving the Netflix-style
    /// running-order rings + grey→black rows. Bumps trigger the list to re-render.
    @Published private(set) var segmentReady: [Int: Bool] = [:]

    // MARK: Accessors

    /// THE single displayed play/pause truth, on every surface (in-app icon, lock-screen
    /// playback rate / MPNowPlayingInfoCenter state). It is a PURE FUNCTION OF INTENT, not
    /// of transient engine state: it flips ONLY when the user presses play/pause (or the
    /// briefing ends). A skip/seek's `.buffering` rebuild window and a `.stalled` beat do
    /// NOT flip it, so the icon never diverges from the audio the user asked for — playing
    /// stays PAUSE through a skip, paused stays PLAY through a skip.
    var isPlaying: Bool { intendedPlaying }

    private(set) var manifest: BulletinManifest?
    var storySegments: [ManifestSegment] { _storySegments }
    var storyUnits: [StoryUnit] { _storyUnits }
    var totalStoryDurationSeconds: Double { _totalStoryDurationSeconds }
    /// Every segment in play order (intro, transitions, stories, outro) — the full audio
    /// timeline the scrubber renders so progress moves from the very first second.
    var allSegments: [ManifestSegment] { segments }
    /// Total duration of the FULL audio (all segments), for the podcast-style timer/bar.
    var totalDurationSeconds: Double { _totalDurationSeconds }
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
    // Full audio timeline (all segments, in play order): total duration + each segment's
    // cumulative start, keyed by segment.index. Used for the podcast-style elapsed/bar.
    private var _totalDurationSeconds: Double = 0
    private var _fullStartByIndex: [Int: Double] = [:]

    // MARK: Private — play intent + navigation serialization

    /// The user's DESIRED play/pause state — and THE displayed play/pause truth (see
    /// `isPlaying`). It changes ONLY when the user presses play/pause (or the briefing
    /// genuinely ends); next/previous/skip/seek/segment-transition NEVER touch it. So it
    /// survives queue rebuilds (a seek/skip drops to `.buffering` transiently) and the icon
    /// holds through them — a skip while playing keeps the PAUSE icon and audio just
    /// continues. `@Published` so every surface (in-app icon + lock screen) re-renders the
    /// instant intent flips; its didSet refreshes now-playing even when `playerState`
    /// didn't change (e.g. a pause that lands mid-rebuild).
    @Published private(set) var intendedPlaying = false {
        didSet { if intendedPlaying != oldValue { updateNowPlaying() } }
    }

    /// Monotonic token bumped on every user navigation (skip / skipBack / seek). A
    /// seek's async completion captures the token and no-ops if a newer navigation has
    /// superseded it — so an in-flight rebuild can never clobber a later one (no stale
    /// resume, no double-advance) when taps come fast.
    private var navGeneration = 0

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
    // STREAMING: enqueue hit a not-yet-synthesised (nil-url) segment and is HOLDING; the
    // readiness poll resumes it once that segment's audio arrives.
    private var _heldEnqueue     = false
    private var _readinessPollTask: Task<Void, Never>?
    private var _allReady        = false

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
        [cc.playCommand, cc.pauseCommand, cc.togglePlayPauseCommand,
         cc.nextTrackCommand, cc.previousTrackCommand,
         cc.changePlaybackPositionCommand].forEach { $0.removeTarget(nil) }
    }

    // MARK: - Public API

    func load(profileId: Int) async {
        // Stop any current playback (sends summary if mid-session).
        stopInternal(sendEvents: true)

        _profileId = profileId
        loadProgress = 0
        loaderComplete = false
        playerState = .loadingManifest

        do {
            let m: BulletinManifest = try await client.post(
                "data/profiles/\(profileId)/manifest",
                body: ManifestRequest()
            )
            manifest = m
            _bulletinId = m.bulletinId
            // STREAMING skeleton: fill nil durations with per-type ESTIMATES so the scrubber +
            // timeline read sensibly from the first frame; real durations overwrite them as
            // /readiness reports each synthesised segment. `url` stays nil (isReady is url-based)
            // until that segment is actually ready.
            segments = m.segments.map { seg in
                var s = seg
                if s.durationMs == nil { s.durationMs = Int(Self.estimatedSeconds(seg) * 1000) }
                return s
            }
            segmentReady = [:]
            _allReady = false
            rebuildTimeline()
            playbackElapsedSeconds = 0
            storyIndex = 0
            currentUnitIndex = 0
            globalPositionPct = 0

            #if DEBUG
            print("🎙 BulletinPlayer: manifest ok — \(segments.count) segments, \(storyCount) stories")
            for seg in segments {
                print("   [\(seg.index)] \(seg.type.padding(toLength: 12, withPad: " ", startingAt: 0))  \(seg.url?.split(separator: "/").last.map(String.init) ?? "pending")")
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
        let gateStart = Date()
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
                    applyReadiness(r)               // STREAMING: fill ready segments' urls/durations
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

        // Fast/cached-path floor: when a briefing is cached, safe_to_start fires on the
        // first poll and the loader would dismiss mid-stage-1. Keep it up until at least
        // `minLoaderSeconds` (= visibleStages × stageSeconds) so all four visual stages
        // get their equal quarter. A slow prepare already exceeds this (it's still waiting
        // on safe_to_start above), so this only floors the fast path. The visible stages
        // themselves are paced by a fixed timer in the view (LoaderTiming.stageIndex),
        // NOT by loadProgress — so stage 1 never freezes on a slow manifest.
        while Date().timeIntervalSince(gateStart) < LoaderTiming.minLoaderSeconds {
            model.tick(dt: tickInterval, ratePerSec: LoadProgressModel.creepRate)
            loadProgress = model.progress
            try? await Task.sleep(nanoseconds: UInt64(tickInterval * 1_000_000_000))
        }

        // READY: the justified final raise — complete the bar from `nearFullCeiling` to
        // 100% and hold a beat so it's visibly seen finishing before the loader dismisses.
        loaderComplete = true
        try? await Task.sleep(nanoseconds: 500_000_000)   // ~0.5s — bar animates 90%→100%

        // Ready (or gave up): enqueue and start. Hold near-complete but < 1.0 — audio
        // actually starting is what snaps loadProgress to 1.0 (handleTimeControlStatus).
        model.reachedMilestone(floor: LoadProgressModel.firstStoryFloor,
                               nextCap: LoadProgressModel.preStartCap)
        loadProgress = model.progress
        setupQueuePlayer()
        activateAudioSession()
        intendedPlaying = true          // first autoplay — the user's intent is "playing"
        player?.play()
        // Reflect playback in state directly — audio is gated-ready, so don't wait for
        // a timeControlStatus(.playing) edge (AVQueuePlayer may not emit one). This snaps
        // the loader to complete and shows the transport with the correct PAUSE icon on
        // first load. handleTimeControlStatus still handles genuine stalls/resumes.
        loadProgress = 1.0
        playerState = .playing
        // Keep polling readiness during playback so later segments' urls arrive (the gate above
        // only polled until safe_to_start), the running-order rings fill, and any held enqueue
        // resumes ahead of the playhead.
        startPlaybackReadinessPoll()
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

    // MARK: - Streaming readiness (skeleton manifest → per-segment audio arrives incrementally)

    private struct AckResponse: Decodable {}

    private static func estimatedSeconds(_ seg: ManifestSegment) -> Double {
        if let ms = seg.durationMs, ms > 0 { return Double(ms) / 1000.0 }
        switch seg.type {
        case "story": return 45
        case "intro": return 18
        case "outro": return 14
        default:      return 4      // transition
        }
    }

    /// Rebuild the derived timeline (story units, per-segment starts, totals) from the current
    /// segments — on load and whenever /readiness overwrites an estimated duration with the real.
    private func rebuildTimeline() {
        _storySegments = segments.filter { $0.isStory }
        _storyUnits = Self.buildStoryUnits(from: segments)
        _totalStoryDurationSeconds = _storyUnits.reduce(0) { $0 + $1.totalDurationSeconds }
        _fullStartByIndex = [:]
        var acc = 0.0
        for seg in segments {
            _fullStartByIndex[seg.index] = acc
            acc += seg.durationSeconds
        }
        _totalDurationSeconds = acc
        storyCount = _storySegments.count
    }

    /// Merge a readiness snapshot: fill ready segments' url + real duration (matched by FULL
    /// index), flag them ready for the running-order list, rebuild the timeline, and resume a
    /// held enqueue. Returns whether everything is now ready (so the poll can stop).
    @discardableResult
    private func applyReadiness(_ r: BulletinReadiness) -> Bool {
        var changed = false
        for rs in r.segments where rs.index >= 0 && rs.index < segments.count && rs.isReady {
            if segments[rs.index].url == nil, let u = rs.url, !u.isEmpty {
                segments[rs.index].url = u
                changed = true
            }
            if let d = rs.durationMs, d > 0, segments[rs.index].durationMs != d {
                segments[rs.index].durationMs = d
                changed = true
            }
            if segmentReady[rs.index] != true { segmentReady[rs.index] = true }
        }
        if changed { rebuildTimeline() }
        if _heldEnqueue { _heldEnqueue = false; enqueueNext() }   // a held segment may now be ready
        _allReady = (r.allReady == true)
        return _allReady
    }

    /// Poll /readiness during PLAYBACK (the gate stops polling at safe_to_start) so later
    /// segments' urls arrive, the rings fill, and any held enqueue resumes. Stops when all ready.
    private func startPlaybackReadinessPoll() {
        _readinessPollTask?.cancel()
        guard let bid = _bulletinId, !_allReady else { return }
        _readinessPollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)   // ~1s
                guard let self else { return }
                guard let r: BulletinReadiness = try? await self.client.get("data/bulletins/\(bid)/readiness")
                else { continue }
                let done = await MainActor.run { self.applyReadiness(r) }
                if done { return }
            }
        }
    }

    /// Tap/skip to a not-yet-ready story: bump it to the FRONT of the background synthesis queue
    /// (the server applies forward-then-backfill). Best-effort; the readiness poll fills the rest.
    private func prioritise(storyId: String) {
        guard let bid = _bulletinId else { return }
        Task { [weak self] in
            struct Body: Encodable { let story_id: String }
            _ = try? await self?.client.post("data/bulletins/\(bid)/prioritise", body: Body(story_id: storyId)) as AckResponse?
        }
    }

    /// Is this story unit's audio ready to play? (drives the list ring + grey→black).
    func isUnitReady(_ unit: StoryUnit) -> Bool {
        segmentReady[unit.storySegment.index] == true || unit.storySegment.url != nil
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
            intendedPlaying = true
            activateAudioSession()
            player?.play()
            playerState = .playing   // reflect immediately (incl. stall-resume) — don't wait for a KVO edge
        case .ended:
            // Convention: PLAY at the end REPLAYS the whole briefing from the top (intro
            // → stories → outro). No dead button — in-app and lock-screen play both land
            // here (same path via performRemote → play()).
            restartFromBeginning()
        default:
            break
        }
    }

    /// Rebuild the queue from the very first segment and play — the end-of-briefing replay.
    /// Sets intent to playing (a user PLAY press) and resets the full-timeline position to 0
    /// so the bar/timer restart cleanly on both surfaces.
    private func restartFromBeginning() {
        guard !segments.isEmpty else { return }
        navGeneration &+= 1                 // invalidate any in-flight seek completion
        intendedPlaying = true
        storyIndex = 0
        currentUnitIndex = 0
        globalPositionPct = 0
        playbackElapsedSeconds = 0
        prevOutcome = .natural
        playerState = .buffering            // satisfies the currentItemChanged(nil) guard during rebuild
        if player == nil {
            setupQueuePlayer()              // fresh player, enqueues from index 0
        } else {
            player?.pause()
            player?.removeAllItems()
            itemObservations.removeAll()
            itemSegmentMap.removeAll()
            nextEnqueueIdx = 0
            enqueueNext()
            enqueueNext()
        }
        activateAudioSession()
        player?.play()
        playerState = .playing
    }

    func pause() {
        // Record intent even if we can't act this instant (e.g. mid-rebuild `.buffering`):
        // a pause tap must stick so an in-flight rebuild doesn't auto-resume against it.
        intendedPlaying = false
        guard playerState == .playing || playerState == .stalled else { return }
        player?.pause()
        cancelStallTimer()
        playerState = .paused
    }

    func togglePlayPause() {
        if isPlaying { pause() } else { play() }
    }

    /// Next → the next story's content. Routed through the SAME index-based navigation
    /// primitive as Previous/seek (not a raw advanceToNextItem on the live queue), so it
    /// stays correct even mid-rebuild and rapid taps serialize through the nav token —
    /// no dropped taps, no double-advance, no advancing into a half-built queue. Starts
    /// at the story part (offset = the bridge length) so it skips the transition, exactly
    /// as the old queue-based skip landed. seekToStoryUnit fires the "skipped" event for
    /// the current story and sets prevOutcome = .userSkip.
    func skip() {
        guard !_storyUnits.isEmpty else { return }
        let next = currentUnitIndex + 1
        guard next < _storyUnits.count else { return }   // already on the last story
        seekToStoryUnit(at: next, offsetSeconds: _storyUnits[next].transitionDurationSeconds)
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
                // Pause the ENGINE but keep `intendedPlaying` — we still want to be
                // playing, so we resume on .ended unless the user pauses meanwhile.
                if self.playerState == .playing || self.playerState == .stalled {
                    self.player?.pause(); self.cancelStallTimer(); self.playerState = .paused
                }
            case .ended:
                // Resume only if the system says so AND the user still intends to play
                // (a manual pause during the interruption clears the intent → stay paused).
                if shouldResume && self.intendedPlaying { self.play() }
            @unknown default: break
            }
        }
    }

    @objc private func handleRouteChange(_ note: Notification) {
        guard let ui = note.userInfo,
              let raw = ui[AVAudioSessionRouteChangeReasonKey] as? UInt,
              AVAudioSession.RouteChangeReason(rawValue: raw) == .oldDeviceUnavailable else { return }
        Task { @MainActor [weak self] in
            guard let self, self.playerState == .playing || self.playerState == .stalled else { return }
            // Headphones unplugged: pause and DON'T auto-resume when re-plugged — clear
            // the intent so the user has to press play again (standard behaviour).
            self.intendedPlaying = false
            self.player?.pause()
            self.cancelStallTimer()
            self.playerState = .paused
        }
    }

    // MARK: - Remote command center

    private func setupRemoteCommands() {
        let cc = MPRemoteCommandCenter.shared()
        // Clear any prior handlers first. Block-based addTarget can't be removed by
        // `self` (it registers an internal target), so `removeTarget(nil)` is the only
        // way to guarantee EXACTLY ONE handler set is live — otherwise a replaced player
        // would leave stale handlers on the shared command center.
        let commands = [cc.playCommand, cc.pauseCommand, cc.togglePlayPauseCommand,
                        cc.nextTrackCommand, cc.previousTrackCommand]
        commands.forEach { $0.removeTarget(nil) }

        // All four transport controls ENABLED on the lock screen / Control Center — none
        // greyed out — and wired to the SAME methods as the in-app buttons. Each hops to
        // the main actor before touching @Published state, so a lock-screen tap never
        // mutates it off-main (which silently breaks SwiftUI observation on device and
        // strands the in-app icon). togglePlayPause covers headset/CarPlay centre-button.
        cc.playCommand.isEnabled            = true
        cc.pauseCommand.isEnabled           = true
        cc.togglePlayPauseCommand.isEnabled = true
        cc.nextTrackCommand.isEnabled       = true
        cc.previousTrackCommand.isEnabled   = true
        // Lock-screen / CarPlay scrubbing — the draggable progress bar, on the FULL-briefing
        // timeline (see updateNowPlaying), seeking the real audio like a real player.
        cc.changePlaybackPositionCommand.isEnabled = true

        // Drive each command through the SAME synchronous main-actor path the in-app
        // buttons use (see `performRemote`) — NOT a detached Task that returns .success
        // before the action runs. The old Task approach let iOS optimistically flip the
        // lock-screen button while the real player call + source-of-truth update happened
        // asynchronously afterwards, so the audio kept playing while the icon toggled and
        // the in-app state desynced. Running synchronously means the AVPlayer actually
        // pauses/resumes and playerState / intendedPlaying / now-playing are all updated
        // before we return, so every surface agrees.
        cc.playCommand.addTarget            { [weak self] _ in self?.performRemote { $0.play() }            ?? .noSuchContent }
        cc.pauseCommand.addTarget           { [weak self] _ in self?.performRemote { $0.pause() }           ?? .noSuchContent }
        cc.togglePlayPauseCommand.addTarget { [weak self] _ in self?.performRemote { $0.togglePlayPause() } ?? .noSuchContent }
        cc.nextTrackCommand.addTarget       { [weak self] _ in self?.performRemote { $0.skip() }            ?? .noSuchContent }
        cc.previousTrackCommand.addTarget   { [weak self] _ in self?.performRemote { $0.skipBack() }        ?? .noSuchContent }
        // Scrub: the event's positionTime is in the full-briefing timeline (we publish
        // duration = totalDurationSeconds), so seek to that absolute time. Same synchronous
        // main-actor path as every other remote command; resumes in the intended state.
        cc.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let self, let e = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            let t = e.positionTime
            return self.performRemote { $0.seekToFullTime(t) }
        }
    }

    /// Run a remote (lock-screen / Control-Center / headset) transport action on the main
    /// actor, SYNCHRONOUSLY, so it drives the exact same path as the in-app buttons and
    /// iOS reads the REAL post-action now-playing state before we return. These commands
    /// are delivered on the main thread in practice; if ever off-main we hop synchronously
    /// so the semantics are identical. `nonisolated` so the (non-isolated) command closure
    /// can call it.
    nonisolated private func performRemote(_ action: @escaping @MainActor (BulletinPlayer) -> Void) -> MPRemoteCommandHandlerStatus {
        if Thread.isMainThread {
            MainActor.assumeIsolated { action(self) }
        } else {
            DispatchQueue.main.sync { MainActor.assumeIsolated { action(self) } }
        }
        return .success
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
        _readinessPollTask?.cancel()
        _readinessPollTask = nil
        _heldEnqueue = false
        _allReady = false
        segmentReady = [:]
        teardownQueuePlayer()
        manifest       = nil
        segments       = []
        _storySegments = []
        _storyUnits    = []
        _totalStoryDurationSeconds = 0
        _totalDurationSeconds = 0
        _fullStartByIndex = [:]
        playbackElapsedSeconds = 0
        loaderComplete = false
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
        intendedPlaying      = false
        navGeneration       &+= 1        // invalidate any in-flight seek completion
        _profileId           = nil
        _bulletinId          = nil
        playerState          = .idle
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        MPNowPlayingInfoCenter.default().playbackState = .stopped
    }

    // MARK: - Sliding window enqueue

    private func enqueueNext() {
        guard let p = player, nextEnqueueIdx < segments.count else { return }
        let seg = segments[nextEnqueueIdx]

        // STREAMING: a not-yet-synthesised segment has no url — HOLD here (don't advance/skip).
        // The readiness poll calls enqueueNext again the moment this segment's url arrives.
        guard let urlStr = seg.url else {
            _heldEnqueue = true
            return
        }
        nextEnqueueIdx += 1
        _heldEnqueue = false

        guard let url = URL(string: urlStr) else {
            enqueueNext()   // malformed URL → skip
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
            // Distinguish a GENUINE queue-exhausted nil from a SPURIOUS one that must
            // not be read as ".ended":
            //   • .buffering        — a seek is mid-rebuild (removeAllItems).
            //   • prevSeg == nil     — the FRESH queue's INITIAL currentItem KVO nil.
            //     Because the observer is `.receive(on: RunLoop.main)`, that initial nil
            //     is delivered AFTER the synchronous first-load block set
            //     playerState = .playing (gateOnReadinessThenPlay). Reading it as .ended
            //     would strand the first-play transport on a PLAY icon forever — the
            //     timeControlStatus(.playing) edge only recovers from
            //     .preparing/.buffering/.stalled, never from .ended. This was the LAST
            //     stranded first-play path (seek/skip already set state directly).
            // A real end-of-bulletin nil always follows a played segment (prevSeg != nil).
            //
            // ROOT-CAUSE GUARD (#109 — skip/prev false end-of-briefing): a nil reported here
            // is often TRANSIENT, not a real end. skip()/skipBack()/seekToStoryUnit rebuild
            // the queue (removeAllItems → re-enqueue), driving currentItem → nil → newItem,
            // and because this observer is DEFERRED (.receive(on: RunLoop.main)) the
            // removeAllItems nil arrives a runloop LATER — after resumeAfterSeekIfIntended
            // already set playerState back to .playing (defeating the `.buffering` guard) and
            // while prevSeg is still the pre-skip segment. That falsely flipped intent→paused
            // (icon→PLAY) mid-briefing while audio kept playing, and set .ended (so play()
            // restarted). The reliable test is the LIVE queue: a genuine end has NO current
            // item now; a rebuild nil has already been superseded by a fresh currentItem.
            if player?.currentItem != nil { return }   // stale rebuild nil — not a real end
            guard playerState != .buffering, prevSeg != nil else { return }
            // Genuine end of the briefing: playback is over, so intent is no longer
            // "playing" — this is the one non-user event allowed to flip intent, so the
            // icon shows PLAY (ready to replay) and matches the silent audio. play() at
            // .ended replays from the top (see restartFromBeginning).
            intendedPlaying = false
            playerState    = .ended
            updateNowPlaying()
            return
        }

        let seg = itemSegmentMap[ObjectIdentifier(item)]
        currentSegment     = seg
        positionSeconds    = 0
        currentPositionPct = 0
        // Snap full-timeline elapsed to this segment's start so the bar tracks segment
        // advances immediately (not one periodic tick later).
        if let seg { playbackElapsedSeconds = _fullStartByIndex[seg.index] ?? playbackElapsedSeconds }

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

        // Full-timeline elapsed = this segment's start (intro included) + position within it.
        if let seg = currentSegment {
            playbackElapsedSeconds = (_fullStartByIndex[seg.index] ?? 0) + secs
        }

        if let seg = currentSegment, seg.isStory {
            currentPositionPct = seg.durationSeconds > 0 ? min(1.0, secs / seg.durationSeconds) : 0
        }

        updateGlobalPosition(positionWithinSegment: secs)

        // Keep the lock-screen elapsed on the FULL-briefing timeline (matches the in-app
        // bar), not the per-segment position — so it advances continuously, never resets.
        if var info = MPNowPlayingInfoCenter.default().nowPlayingInfo {
            info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = playbackElapsedSeconds
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

        // STREAMING: jumping to a not-yet-synthesised story bumps it to the FRONT of the
        // background synthesis queue (server: forward-then-backfill). Playback then holds
        // (buffering) on the pending segment until the readiness poll fills its url.
        if unit.storySegment.url == nil, let sid = unit.storySegment.storyId {
            prioritise(storyId: sid)
        }

        // Bump the navigation token: any earlier in-flight seek's async completion will
        // now no-op, so rapid taps can't clobber each other (no stale resume / double
        // advance). Resume is decided by the LIVE `intendedPlaying`, not a snapshot — so
        // a pause landing mid-rebuild sticks, and a rapid tap while playing still resumes.
        navGeneration &+= 1
        let gen = navGeneration

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
                    guard let self, gen == self.navGeneration else { return }   // superseded → drop
                    self.resumeAfterSeekIfIntended()
                }
            }
        } else {
            resumeAfterSeekIfIntended()
        }
    }

    /// Resume playback after a seek/rebuild IFF the user still intends to be playing.
    /// Reads `intendedPlaying` live (not a snapshot) so a pause that landed during the
    /// rebuild wins. Sets `.playing` directly — the removeAllItems()→re-enqueue and the
    /// in-buffer seek can leave timeControlStatus already `.playing`, so the KVO edge may
    /// never fire and the state would otherwise strand at `.buffering`. If not intended,
    /// we leave `.buffering` (paused audio at the new position, PLAY icon) — the
    /// currentItemChanged(nil) guard relies on it not being a played state yet.
    private func resumeAfterSeekIfIntended() {
        guard intendedPlaying else { return }
        activateAudioSession()
        player?.play()
        playerState = .playing
    }

    /// Seek to a fraction (0–1) across the story-unit timeline.
    /// Seek to a fraction (0–1) across the FULL audio timeline (the scrubber's timeline,
    /// intro → stories → outro). Maps the target to the segment it lands in; a story/
    /// transition seeks to that story unit at the right offset, a leading wrapper (intro/
    /// sting) seeks to the first story, a trailing wrapper (outro) to the last.
    /// Seek to an absolute time (seconds) on the full-briefing timeline — the lock-screen /
    /// CarPlay scrubber's `positionTime`. Thin wrapper over `seekToFullFraction`.
    func seekToFullTime(_ seconds: Double) {
        guard _totalDurationSeconds > 0 else { return }
        seekToFullFraction(seconds / _totalDurationSeconds)
    }

    func seekToFullFraction(_ fraction: Double) {
        guard _totalDurationSeconds > 0, !segments.isEmpty, !_storyUnits.isEmpty else { return }
        let target = max(0, min(1, fraction)) * _totalDurationSeconds

        // Find the segment containing `target`.
        var hit: ManifestSegment = segments[segments.count - 1]
        var offsetInSeg = target - (_fullStartByIndex[hit.index] ?? 0)
        for seg in segments {
            let start = _fullStartByIndex[seg.index] ?? 0
            if target < start + seg.durationSeconds {
                hit = seg
                offsetInSeg = target - start
                break
            }
        }

        if let unit = storyUnit(forSegment: hit) {
            let offsetInUnit: Double
            if let trans = unit.transitionSegment, hit.index == trans.index {
                offsetInUnit = max(0, offsetInSeg)                                  // inside the bridge
            } else {
                offsetInUnit = unit.transitionDurationSeconds + max(0, offsetInSeg) // inside the story
            }
            seekToStoryUnit(at: unit.index, offsetSeconds: offsetInUnit)
        } else {
            // Wrapper (intro/sting/outro): jump to the nearest story unit.
            let firstStoryStart = _storyUnits.first.map { _fullStartByIndex[$0.storySegment.index] ?? 0 } ?? 0
            seekToStoryUnit(at: target < firstStoryStart ? 0 : _storyUnits.count - 1)
        }
    }

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

    /// The story unit the playhead is in (clamped) — stays the current story even during
    /// its bridge/intro, mirroring NowPlayingView's board. nil when there are no stories.
    private var currentStoryUnit: StoryUnit? {
        guard !_storyUnits.isEmpty else { return nil }
        let idx = min(max(0, currentUnitIndex), _storyUnits.count - 1)
        return _storyUnits[idx]
    }

    private func updateNowPlaying() {
        let center = MPNowPlayingInfoCenter.default()
        // Show now-playing whenever there is playable content — during playback, AND for a
        // finished briefing (`.ended`) so the lock screen keeps a PLAY button that replays.
        // Clear only when nothing is loaded (teardown / stop).
        let ended = playerState == .ended
        guard !segments.isEmpty, currentSegment != nil || ended else {
            center.nowPlayingInfo = nil
            center.playbackState = .stopped
            return
        }
        // Title = the current story's headline; subtitle = its ranked SOURCES (same data +
        // format as the in-app card's row). Derived from the current story UNIT so it stays
        // the story's headline through its bridge/intro. No sources → "Headlines".
        let unit = currentStoryUnit
        let title = unit?.title ?? "Headlines"
        let srcs = (unit?.storySegment.sources ?? []).prefix(3)
        let artist = srcs.isEmpty ? "Headlines" : srcs.joined(separator: " · ")
        // FULL-BRIEFING timeline (identical to the in-app scrubber): ONE continuous
        // elapsed/duration across intro → stories → outro, so the lock-screen bar advances
        // smoothly and NEVER resets per segment. At the end it reads full/full, paused.
        let elapsed = ended ? totalDurationSeconds : playbackElapsedSeconds
        var info: [String: Any] = [
            MPMediaItemPropertyTitle:                    title,
            MPMediaItemPropertyArtist:                   artist,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: elapsed,
            MPMediaItemPropertyPlaybackDuration:         totalDurationSeconds,
            // Rate + playbackState derive from the SAME `isPlaying` (= intent) as the in-app
            // icon, so the lock screen can never disagree with the app.
            MPNowPlayingInfoPropertyPlaybackRate:        isPlaying ? 1.0 : 0.0,
        ]
        if let art = Self.lockScreenArtwork {
            info[MPMediaItemPropertyArtwork] = art
        }
        // Chapter markers (story N / total) ride on the continuous timeline. N tracks the
        // unit the playhead is in — matching the in-app "NOW PLAYING N / total".
        if storyCount > 0 {
            info[MPNowPlayingInfoPropertyChapterNumber] = min(max(0, currentUnitIndex), storyCount - 1) + 1
            info[MPNowPlayingInfoPropertyChapterCount]  = storyCount
        }
        center.nowPlayingInfo = info
        center.playbackState = isPlaying ? .playing : .paused
    }

    /// Lock-screen / Control-Center artwork — the REAL app icon (Headlines_Icon_1024),
    /// bundled as a standalone image set ("LockScreenArtwork") because asset-catalog APP
    /// ICONS aren't reliably loadable via UIImage(named:) at runtime. The provider closure
    /// renders the icon at whatever size the lock screen requests, so it's crisp at every
    /// scale. nil (artwork omitted) only if the bundled asset is somehow missing.
    private static let lockScreenArtwork: MPMediaItemArtwork? = {
        guard let icon = UIImage(named: "LockScreenArtwork") else { return nil }
        return MPMediaItemArtwork(boundsSize: icon.size) { size in
            UIGraphicsImageRenderer(size: size).image { _ in
                icon.draw(in: CGRect(origin: .zero, size: size))
            }
        }
    }()
}

// MARK: - Loader stage pacing

/// Even, fixed-timer pacing for the generation loader's visible stages.
///
/// The visible stages are a FIXED VISUAL SEQUENCE, decoupled from backend progress —
/// this is the whole point. The loader has `visibleStages` (4) stages; stages 1→3 each
/// last exactly `stageSeconds` on a plain timer, so stage 1 can never freeze while a
/// slow (~5–6s) manifest loads. The last (4th) stage is the only one allowed to dwell:
/// it holds until the briefing is actually ready (`safe_to_start`), absorbing any
/// remaining wait. Real readiness governs ONLY when the loader dismisses (and how long
/// stage 4 lasts) — never the 1→2→3 transitions.
///
/// `minLoaderSeconds` = the fast/cached-path floor: hold long enough for all four stages
/// to get their equal quarter (25% each) so a ready briefing still walks evenly through
/// them; a slow prepare exceeds it and extends stage 4. Consumed by the gate (hold) and
/// NowPlayingView (stage word + determinate bar).
enum LoaderTiming {
    // The loader lays its stages out EVENLY over the expected-generation window:
    // `pacedStages` stages, one every `stageSeconds`, so the sequence reads as steady,
    // unhurried progress (every paced stage is exactly the same length — none is
    // disproportionately long). `visibleStages` adds one final DWELL stage after the
    // window — it's the only stage allowed to hold, absorbing the wait when generation
    // runs long. If generation is faster, the gate dismisses on readiness (no forced hold
    // beyond `minLoaderSeconds`).
    static let stageSeconds: Double = 2.0        // each paced stage — brisk + readable. Streaming starts
                                                 // playback at 2 stories (~5–10s), so short stages flick
                                                 // past and the loader stays alive rather than freezing.
    static let pacedStages: Int = 4              // evenly-timed stages covering ~0–8s
    static var visibleStages: Int { pacedStages + 1 }   // + a final dwell stage for overrun
    static var expectedSeconds: Double { stageSeconds * Double(pacedStages) }   // ~8s
    // Small floor (~4s ≈ two stages) so a cached/instant briefing shows a readable stage
    // or two rather than flashing — but NOT the full window (a fast briefing dismisses on ready).
    static var minLoaderSeconds: Double { stageSeconds * 2 }
    /// The bar climbs on the timer to this ceiling across the stages; the FINAL raise to
    /// 1.0 is done on ready (BulletinPlayer.loaderComplete) — a justified final jump.
    static let nearFullCeiling: Double = 0.9

    /// Which stage (0-based) is showing at `elapsed` seconds — PURELY from the timer, not
    /// backend progress. Advances one stage every `stageSeconds` (evenly), clamped at the
    /// final dwell stage, which then holds until the gate dismisses the loader on readiness.
    static func stageIndex(elapsed: Double) -> Int {
        max(0, min(visibleStages - 1, Int(elapsed / stageSeconds)))
    }

    /// Determinate bar fill (0–1) at `elapsed` seconds. EVEN CHUNK PER STAGE: each stage
    /// bumps the bar by an equal step (≈ ceiling / visibleStages), so it climbs steadily
    /// and evenly with the status words — no long freeze at the start OR the end. Reaches
    /// `nearFullCeiling` at the final (dwell) stage; the gate raises it to 1.0 on ready.
    /// A view-side `.animation` ramps each step so the bumps read as a smooth climb.
    static func barFill(elapsed: Double) -> Double {
        let stage = stageIndex(elapsed: elapsed)            // 0 … visibleStages-1
        return nearFullCeiling * Double(stage + 1) / Double(visibleStages)
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
