import AVFoundation
import Combine
import Foundation
import MediaPlayer
import os
import UIKit

/// Play-event flush chain logging — subsystem "flush". Filter in Console.app / `log stream` with
/// `subsystem:com.headlines.flush` to see every flush (event count, bulletin) and its outcome, so
/// a broken flush is never invisible again.
private let flushLog = Logger(subsystem: "com.headlines.flush", category: "summary")
/// L-C load-gate logging — same `com.headlines.flush` subsystem, category "gate". Filter to see
/// every time the client refused to start audio because the opener didn't match the board lead.
private let gateLog = Logger(subsystem: "com.headlines.flush", category: "gate")

// MARK: - Request / response types

private struct ManifestRequest: Encodable {
    var includeTopStories: Bool = true
    var selectionId: String? = nil        // L-D: pin the tapped selection through to the served bulletin
    enum CodingKeys: String, CodingKey {
        case includeTopStories = "include_top_stories"
        case selectionId = "selection_id"
    }
}

private struct EventRequest: Encodable {
    let profileId: Int
    let storyHash: String
    let action: String
    let positionPct: Double
    var selectionId: String? = nil        // L-D: echo the selection; server rejects a stale one (409)
    enum CodingKeys: String, CodingKey {
        case profileId   = "profile_id"
        case storyHash   = "story_hash"
        case action
        case positionPct = "position_pct"
        case selectionId = "selection_id"
    }
}

private struct SummaryRequest: Encodable {
    let profileId: Int
    let events: [EventItem]
    var selectionId: String? = nil        // L-D: echo the selection on the batch flush
    struct EventItem: Encodable {
        let storyHash: String
        let storyId: String?
        let profileId: Int          // belt-and-braces: also send profile_id per event; the server
        let action: String          // treats the top-level profile_id as authoritative and ignores it
        let positionPct: Double
        enum CodingKeys: String, CodingKey {
            case storyHash   = "story_hash"
            case storyId     = "story_id"
            case profileId   = "profile_id"
            case action
            case positionPct = "position_pct"
        }
    }
    enum CodingKeys: String, CodingKey {
        case profileId = "profile_id"
        case events
        case selectionId = "selection_id"
    }
}

// Accepts {"status": "...", "updated": N?} — used for fire-and-forget endpoints.
private struct StatusResponse: Decodable {
    let status: String
    let updated: Int?
}

private struct StoryEvent {
    let storyHash: String
    let storyId: String?
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
    /// Set true for a beat when the briefing is READY, so the loader board crossfades to the
    /// real first-story headline (pre-resolving the morph) before the loader dismisses into
    /// playback. Also fires the "briefing ready" haptic. Reset per load.
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
    /// L-D: the selection this play session belongs to (run:hash from the manifest). Echoed on every
    /// event so the server rejects a stale-selection flush loudly (409). nil on an older backend.
    private var _selectionId: String?
    private var _bulletinId: Int?
    private var segments: [ManifestSegment] = []
    private var _storySegments: [ManifestSegment] = []
    /// The running order the UI binds to — PUBLISHED so every rebuild (reconcile/readiness) re-renders
    /// the tracklist, board, and lock screen ATOMICALLY from one snapshot. No surface keeps its own
    /// copy; SwiftUI diffs by `StoryUnit.identity` (story id), so a renumber can't strand a stale row.
    @Published private(set) var storyUnits: [StoryUnit] = []
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
    // BUG 2/3 — segments that will NEVER play: a genuine/stall-reaped failure (readiness
    // state=="failed") or a client-ceiling timeout. enqueue skips them; the end gate ignores them.
    private var failedSegmentIdx: Set<Int> = []
    // Client-side ceiling on a single HOLD (belt-and-braces to the backend stall watchdog).
    private var holdStartedAt: Date?
    private var holdStartedIdx: Int?
    private let holdCeilingSeconds: TimeInterval = 45
    private var _readinessPollTask: Task<Void, Never>?
    private var _allReady        = false
    // The skeleton manifest iOS first receives is PRE-dedup; the backend overwrites the stored
    // segments with the deduped set a few seconds into playback (after safe_to_start). When we
    // detect that shrink via /readiness we rebuild our running order once to match what actually
    // plays (see reconcileEditionIfShrunk). This guards against re-running that rebuild.
    private var _editionReconciled = false
    // A tap on a PENDING (nil-url) story records the intent WITHOUT tearing down what's playing;
    // applyReadiness fulfils it (seek + play) the moment that story's start segment synthesises.
    // Stored by STORY IDENTITY, not array position: a dedup reconcile can renumber/remove rows
    // between the tap and its fulfilment, and a stale Int would fulfil the WRONG story (audit §1.4
    // seam #2). Every consumer resolves it to the CURRENT index live. PUBLISHED so the row's
    // buffering spinner (compared by identity) appears the instant a pending row is tapped.
    @Published private(set) var pendingTapStoryId: String?

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
    // Story hashes already recorded as skipped this briefing — dedups the skip event when a forward
    // tap lands on a still-synthesising story and the user taps again while it's pending. Separate
    // from the UI-facing consumedStoryHashes so a skip doesn't alter the "played" dimming.
    private var _skippedStoryHashes: Set<String> = []

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

    func load(profileId: Int, selectionId: String? = nil) async {
        // Stop any current playback (sends summary if mid-session).
        stopInternal(sendEvents: true)

        _profileId = profileId
        loadProgress = 0
        loaderComplete = false
        _editionReconciled = false
        playerState = .loadingManifest

        do {
            // L-D: carry the selection the board committed to (from the preview) so the server serves
            // EXACTLY that selection — a newer ranking run landing between render and tap can't swap it.
            let m: BulletinManifest = try await client.post(
                "data/profiles/\(profileId)/manifest",
                body: ManifestRequest(selectionId: selectionId)
            )
            manifest = m
            _bulletinId = m.bulletinId
            // The selection actually served — echoed on every event so a stale flush is rejected (409).
            _selectionId = m.selectionId ?? selectionId
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
                // L-C: pass profile_id so the SERVER gate can refuse safe_to_start if the opener
                // isn't the edition's committed lead (never green-lights wrong audio server-side).
                if let r: BulletinReadiness = try? await client.get("data/bulletins/\(bid)/readiness\(_readinessQuery)") {
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

        // READY: pre-resolve the morph — the loader board crossfades to the real first-story
        // headline and holds a beat, so the board content already matches its playback
        // destination before the state flips (seamless flip-board → now-playing-card morph).
        loaderComplete = true
        try? await Task.sleep(nanoseconds: 500_000_000)   // ~0.5s — board crossfades to the headline

        // L-C CLIENT GATE (never start wrong audio): the first audible story the player will open on
        // MUST be the board's lead — queue.items[0]. Both derive from the running order, so post
        // L-A/B/E they match by construction; this is the last-resort hard stop. On a mismatch, HOLD
        // (don't play) and log loudly on the flush subsystem, then surface rather than hang.
        if !openerMatchesQueueLead() {
            gateLog.error("L-C gate HELD: first audible story \(self.firstAudibleStoryId ?? "nil", privacy: .public) != board lead \(self.queue.items.first?.storyId ?? "nil", privacy: .public) (bulletin=\(self._bulletinId ?? -1, privacy: .public)) — refusing to start wrong audio")
            playerState = .failed("This briefing couldn't start cleanly. Pull to refresh.")
            return
        }

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
        storyUnits = Self.buildStoryUnits(from: segments)
        _totalStoryDurationSeconds = storyUnits.reduce(0) { $0 + $1.totalDurationSeconds }
        _fullStartByIndex = [:]
        var acc = 0.0
        for seg in segments {
            _fullStartByIndex[seg.index] = acc
            acc += seg.durationSeconds
        }
        _totalDurationSeconds = acc
        // Count STORIES (units), not story segments — a split lead is two segments, one story.
        storyCount = storyUnits.count
    }

    /// Merge a readiness snapshot: fill ready segments' url + real duration (matched by FULL
    /// index), flag them ready for the running-order list, rebuild the timeline, and resume a
    /// held enqueue. Returns whether everything is now ready (so the poll can stop).
    @discardableResult
    private func applyReadiness(_ r: BulletinReadiness) -> Bool {
        // COUNT RECONCILE: the backend overwrites its stored segments with the DEDUPED set a few
        // seconds into playback (same-event dedup / a dropped-because-unsummarised story). From
        // that point /readiness re-indexes a SHORTER list, so the by-index merge below would
        // misalign and our running order would show phantom stories that never get audio. Detect
        // that shrink and rebuild our list to match the authoritative set BEFORE merging by index.
        if reconcileEditionIfShrunk(r) { return _allReady }

        var changed = false
        for rs in r.segments where rs.isReady {
            // Seam β: match by the segment's stable backend .index IDENTITY, not the array position.
            // `segments[rs.index]` assumed array-position == .index; after a dedup reconcile leaves a
            // non-contiguous index set, that subscript misaligns (fills the wrong slot or is dropped
            // by the old bounds guard). firstIndex-by-.index is position-independent.
            guard let i = segments.firstIndex(where: { $0.index == rs.index }) else { continue }
            if segments[i].url == nil, let u = rs.url, !u.isEmpty {
                segments[i].url = u
                changed = true
            }
            if let d = rs.durationMs, d > 0, segments[i].durationMs != d {
                segments[i].durationMs = d
                changed = true
            }
            if segmentReady[rs.index] != true { segmentReady[rs.index] = true }
        }
        // BUG 2 — surface FAILED segments (genuine failures AND backend stall-watchdog reaps) so
        // enqueue skips them rather than holding forever.
        for rs in r.segments where rs.state == "failed" {
            if failedSegmentIdx.insert(rs.index).inserted { changed = true }
        }
        // Client-side ceiling: never HOLD on one segment past holdCeilingSeconds, even if the
        // backend hasn't yet marked it failed — mark it failed locally and let enqueue skip it.
        if _heldEnqueue, let started = holdStartedAt, let idx = holdStartedIdx,
           Date().timeIntervalSince(started) > holdCeilingSeconds {
            failedSegmentIdx.insert(idx)
            changed = true
        }
        if changed { rebuildTimeline() }
        if _heldEnqueue { _heldEnqueue = false; enqueueNext() }   // a held segment may now be ready or skippable
        fulfilPendingTapIfReady()                                 // a pending-tapped story may now be playable
        _allReady = (r.allReady == true)
        return _allReady
    }

    /// True once the backend's DEDUPED overwrite has landed — i.e. /readiness now reports FEWER
    /// distinct stories than our (pre-dedup skeleton) running order, and every remaining story was
    /// already in our list. Distinct story_ids, so a split lead (opener+remainder, same id) counts
    /// once. Never fires before the overwrite (the skeleton keeps all story_ids until then).
    private func editionShrank(_ r: BulletinReadiness) -> Bool {
        let ready = Set(r.segments.filter { $0.type == "story" }.compactMap { $0.storyId })
        guard !ready.isEmpty else { return false }
        let held = Set(_storySegments.compactMap { $0.storyId })
        return ready.count < held.count && ready.isSubset(of: held)
    }

    /// Rebuild the running order to the authoritative post-dedup set the moment we detect the
    /// shrink, so the story count/tracklist match the stories that actually play (and playback
    /// doesn't hang at the end waiting on a phantom slot that never gets audio).
    ///
    /// Built FROM the readiness snapshot — its indices are contiguous and are the SAME indices
    /// every later /readiness poll uses, so the by-index merge stays aligned afterwards. Titles /
    /// hashes / sources (which readiness doesn't carry) are backfilled by story_id from our held
    /// segments; the deduped set is always a subset of what we already had, so the lookup hits.
    ///
    /// Safe mid-playback: dedup never drops or reorders the lead (index 0), so the segment playing
    /// now is unaffected; enqueue is HELD on a not-yet-synthesised later segment, so resuming it on
    /// the corrected array picks up the right next story. Returns true if it reconciled this call.
    @discardableResult
    private func reconcileEditionIfShrunk(_ r: BulletinReadiness) -> Bool {
        // E: prefer the authoritative deduped order the backend now carries. Reconcile whenever it
        // differs from our current order (membership OR sequence) — an explicit identity diff, not a
        // one-shot inferred shrink, and self-limiting (after we match it, the next poll is a no-op).
        // Older backend (no story_order) → fall back to the story-id-set shrink heuristic (one-shot).
        if let authoritative = r.storyOrder, !authoritative.isEmpty {
            guard authoritative != storyUnits.compactMap({ $0.storyId }) else { return false }
        } else {
            guard !_editionReconciled, editionShrank(r) else { return false }
        }
        _editionReconciled = true

        // Backfill maps from the current (pre-dedup) held segments, keyed by story_id.
        var titleBy = [String: String](), hashBy = [String: String](), srcBy = [String: [String]]()
        for s in segments where s.type == "story" {
            guard let sid = s.storyId else { continue }
            if let t = s.title, titleBy[sid] == nil { titleBy[sid] = t }
            if let h = s.storyHash, hashBy[sid] == nil { hashBy[sid] = h }
            if let src = s.sources, srcBy[sid] == nil { srcBy[sid] = src }
        }
        // Bridge bindings come from the PAYLOAD (rs.prevStoryId/nextStoryId — E) so a transition is
        // re-bound by story IDENTITY, robust to any renumber. `bindBy` (keyed by segment index) is
        // now only a FALLBACK for an older backend whose readiness doesn't carry the pair.
        var bindBy = [Int: (String?, String?)]()
        for s in segments where s.type == "transition" {
            if s.prevStoryId != nil || s.nextStoryId != nil {
                bindBy[s.index] = (s.prevStoryId, s.nextStoryId)
            }
        }

        // Rebuild the segment list from the authoritative (deduped) readiness snapshot.
        segments = r.segments.sorted { $0.index < $1.index }.map { rs -> ManifestSegment in
            let sid = rs.storyId
            let realDur = (rs.durationMs ?? 0) > 0 ? rs.durationMs : nil
            let payloadBind: (String?, String?)? =
                (rs.prevStoryId != nil || rs.nextStoryId != nil) ? (rs.prevStoryId, rs.nextStoryId) : nil
            let bind = payloadBind ?? bindBy[rs.index]
            var ms = ManifestSegment(
                index:      rs.index,
                type:       rs.type,
                url:        (rs.url?.isEmpty == false) ? rs.url : nil,
                durationMs: realDur,
                storyHash:  sid.flatMap { hashBy[$0] },
                storyId:    sid,
                title:      sid.flatMap { titleBy[$0] },
                sources:    sid.flatMap { srcBy[$0] },
                prevStoryId: bind?.0,
                nextStoryId: bind?.1
            )
            if ms.durationMs == nil { ms.durationMs = Int(Self.estimatedSeconds(ms) * 1000) }
            return ms
        }

        segmentReady = [:]
        for s in segments where s.url != nil { segmentReady[s.index] = true }
        rebuildTimeline()

        // Re-anchor the enqueue frontier to just after what's ALREADY enqueued (the lead, which
        // dedup never touches) — NOT the stale old index, which could skip the surviving next
        // story once earlier indices shifted. Nothing enqueued yet (still in the gate) → start
        // from the top; the gate's own setup then enqueues normally.
        if let maxEnqueued = itemSegmentMap.values.map(\.index).max(),
           let pos = segments.firstIndex(where: { $0.index == maxEnqueued }) {
            nextEnqueueIdx = pos + 1
        } else {
            nextEnqueueIdx = 0
        }
        // Keep the current-unit highlight on the segment actually playing now.
        if let cur = currentSegment, let u = storyUnits.first(where: { $0.owns(segmentIndex: cur.index) }) {
            currentUnitIndex = u.index
        } else {
            currentUnitIndex = min(currentUnitIndex, max(0, storyUnits.count - 1))
        }
        _heldEnqueue = false
        enqueueNext()                 // no-op if no player yet, or re-holds on the next nil-url segment
        fulfilPendingTapIfReady()
        _allReady = (r.allReady == true)
        return true
    }

    /// If the user tapped a PENDING story earlier, play it now that its start segment's audio has
    /// arrived — the real seek + play (which the pending tap deliberately deferred to avoid
    /// tearing down what was playing). No-op while it's still pending or nothing is pending-tapped.
    private func fulfilPendingTapIfReady() {
        guard let id = pendingTapStoryId else { return }
        // Resolve the tapped story's CURRENT index every time — a dedup reconcile may have renumbered
        // or removed it since the tap. If it's gone, drop the intent (never fulfil onto a neighbour).
        guard let idx = storyUnits.firstIndex(where: { $0.storyId == id }) else {
            pendingTapStoryId = nil
            return
        }
        guard let start = startSegment(for: storyUnits[idx], offsetSeconds: 0),
              segments[start.arrayIndex].url != nil else { return }   // start segment still pending
        pendingTapStoryId = nil
        playStoryUnit(at: idx)
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
                // Capture the navigation generation BEFORE the network fetch. If a user gesture that
                // rebuilds the queue (seek/skip/restart/stop — each bumps navGeneration) lands while
                // this readiness request is in flight, the snapshot we get back predates the new
                // state and must not drive a reconcile/merge over it (audit: unguarded reconcile race).
                let gen = await MainActor.run { self.navGeneration }
                let q = await MainActor.run { self._readinessQuery }
                guard let r: BulletinReadiness = try? await self.client.get("data/bulletins/\(bid)/readiness\(q)")
                else { continue }
                let done = await MainActor.run { self.applyReadinessIfCurrent(r, fetchGen: gen) }
                if done { return }
            }
        }
    }

    /// Apply a readiness snapshot ONLY if no queue-rebuilding gesture superseded the fetch that
    /// produced it. `fetchGen` is navGeneration captured before the network fetch; if it no longer
    /// matches, a seek/skip/restart/stop raced this poll — DROP the stale snapshot (the next poll
    /// re-fetches against the new state ~1s later). This is the serialization guard that stops a
    /// poll-driven reconcile or by-index merge from clobbering a just-happened navigation. Pending
    /// taps do NOT bump navGeneration and are identity-resolved, so they are unaffected (and correct).
    @MainActor
    @discardableResult
    private func applyReadinessIfCurrent(_ r: BulletinReadiness, fetchGen: Int) -> Bool {
        guard fetchGen == navGeneration else { return false }
        let finished = applyReadiness(r)
        // Re-assert the story the user is waiting on: the one-shot prioritise POSTs are fire-and-forget,
        // so rapid skips could arrive out of order — re-sending the CURRENT pending target makes the
        // latest tap win cleanly (and keeps the hint fresh if the backend isn't reprioritisable yet).
        reassertPendingPriority()
        return finished
    }

    /// Re-send the priority for the story the user is currently waiting on (if any) — called once
    /// per readiness poll so the LATEST skip/tap wins cleanly even if the one-shot prioritise POSTs
    /// raced out of order. No-op when nothing is pending or its audio has already arrived.
    private func reassertPendingPriority() {
        // The pending story IS the identity to re-prioritise — no array lookup needed for the id.
        // Only re-send while it exists in the order and is still not ready.
        guard let id = pendingTapStoryId,
              let idx = storyUnits.firstIndex(where: { $0.storyId == id }),
              !isUnitReady(storyUnits[idx]) else { return }
        prioritise(storyId: id)
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

    /// The segments-array index (and in-segment seek offset) a tap on `unit` will START
    /// playback on at `offsetSeconds`: its transition if we begin within it, else the
    /// story/opener. This is the segment readiness + the pending check must reflect — the tap
    /// can start on a transition that's still nil-url even when the opener is ready.
    private func startSegment(for unit: StoryUnit, offsetSeconds: Double) -> (arrayIndex: Int, seekSeconds: Double)? {
        if offsetSeconds < unit.transitionDurationSeconds,
           let trans = unit.transitionSegment,
           let idx = segments.firstIndex(where: { $0.index == trans.index }) {
            return (idx, offsetSeconds)
        }
        if let idx = segments.firstIndex(where: { $0.index == unit.storySegment.index }) {
            return (idx, max(0, offsetSeconds - unit.transitionDurationSeconds))
        }
        return nil
    }

    /// Is this story unit ready to PLAY ON TAP? (drives the list ring + grey→black.) Reflects the
    /// segment the tap will actually start on (transition if present) — not just the opener — so a
    /// row never reads "ready" while the segment it would start on is still nil-url.
    func isUnitReady(_ unit: StoryUnit) -> Bool {
        guard let start = startSegment(for: unit, offsetSeconds: 0) else {
            return segmentReady[unit.storySegment.index] == true || unit.storySegment.url != nil
        }
        let seg = segments[start.arrayIndex]
        return seg.url != nil || segmentReady[seg.index] == true
    }

    /// The story unit the backend is currently SYNTHESISING — drives the ONE running-order spinner.
    ///
    /// It is NOT simply the first not-ready unit: after a skip/tap the backend reprioritises to
    /// FORWARD-THEN-BACKFILL from the user's position (the bumped story next, then N+1, N+2 …, then
    /// the earlier skipped gap). Modelling that here keeps the spinner on the row actually being
    /// worked on — otherwise it sat on the first grey row (an earlier story the backend had
    /// deprioritised) while the skipped-to story showed a second spinner, which read as "my story
    /// isn't being prioritised". Order of preference:
    ///   1. a pending skip/tap target — the story we asked the backend to prioritise (`pendingTapStoryId`
    ///      is this same row, so the two coincide → a single spinner, not two);
    ///   2. else the first not-ready unit at or after the current story (the forward frontier);
    ///   3. else the first not-ready unit anywhere (the backfilled earlier gap).
    /// nil when every unit is ready.
    var synthesisingUnitIndex: Int? {
        if let id = pendingTapStoryId,
           let p = storyUnits.firstIndex(where: { $0.storyId == id }),
           !isUnitReady(storyUnits[p]) {
            return p
        }
        let cur = min(max(0, currentUnitIndex), max(0, storyUnits.count - 1))
        if let forward = (cur..<storyUnits.count).first(where: { !isUnitReady(storyUnits[$0]) }) {
            return forward
        }
        return storyUnits.firstIndex { !isUnitReady($0) }
    }

    // MARK: - Identity-keyed highlight sources (the UI compares by story id, never by row offset)

    /// The story id of the row that is CURRENTLY playing (clamped current unit). The tracklist,
    /// board, and lock screen highlight the row whose `storyId` equals this — so a renumber can't
    /// leave the highlight on a stale offset.
    var currentStoryId: String? {
        guard !storyUnits.isEmpty else { return nil }
        return storyUnits[min(max(0, currentUnitIndex), storyUnits.count - 1)].storyId
    }

    /// The story id currently being synthesised (drives the single running-order spinner), resolved
    /// from `synthesisingUnitIndex` to identity so the view never compares a row offset.
    var synthesisingStoryId: String? {
        guard let i = synthesisingUnitIndex, storyUnits.indices.contains(i) else { return nil }
        return storyUnits[i].storyId
    }

    /// The first AUDIBLE story's id — the story the player will open on (first story segment in play
    /// order). L-C compares this to the board lead (queue.items[0]) before starting audio.
    var firstAudibleStoryId: String? { segments.first(where: { $0.isStory })?.storyId }

    /// L-C gate: is the first audible story the board's lead (queue.items[0])? True when either side
    /// is unknown (nothing to enforce). A false result HOLDS the gate — never start wrong audio.
    func openerMatchesQueueLead() -> Bool {
        guard let opener = firstAudibleStoryId, let lead = queue.items.first?.storyId else { return true }
        return opener == lead
    }

    /// Query suffix carrying the profile id to /readiness so the SERVER L-C gate can enforce
    /// opener == committed lead. Empty when no profile (older path) → server behaves as today.
    private var _readinessQuery: String { _profileId.map { "?profile_id=\($0)" } ?? "" }

    /// The single canonical running order the UI renders — the player OWNS it, the view CONSUMES it.
    /// Projects the published order (`storyUnits`) + the resolved current/consumed/ready/pending/
    /// synthesising state into one identity-keyed list, so no surface re-derives row state from
    /// scattered properties. Recomputed from @Published inputs, so it's always fresh on re-render.
    var queue: PlaybackQueue {
        let playing = currentStoryId
        let pending = pendingTapStoryId
        let synth   = synthesisingStoryId
        let items = storyUnits.compactMap { unit -> QueueItem? in
            guard let sid = unit.storyId else { return nil }
            let isPlaying = sid == playing
            let consumed  = unit.storyHash.map { consumedStoryHashes.contains($0) } ?? false
            return QueueItem(
                storyId:        sid,
                title:          unit.title ?? "",
                isPlaying:      isPlaying,
                isConsumed:     !isPlaying && consumed,
                isReady:        isUnitReady(unit),
                isBuffering:    sid == pending,
                isSynthesising: sid == synth
            )
        }
        return PlaybackQueue(items: items)
    }

    /// Seek to a story unit's start and begin playing it. Used by the tracklist so
    /// any row — including already-played ones — is replayable on tap, even when the
    /// player is paused (plain `seekToStoryUnit` only resumes if it was already playing).
    func playStoryUnit(at index: Int) {
        seekToStoryUnit(at: index)
        play()
    }

    /// Play the story with this IDENTITY — the tracklist tap boundary. Resolves the id to its
    /// CURRENT row at call time, so a tap always plays the story the row showed even if a dedup
    /// reconcile renumbered the running order between render and tap (audit §1.4 seam #1: a stale
    /// Int offset played a DIFFERENT story, or clamped to the last). No-op if the story is no longer
    /// in the order — never plays a neighbour.
    func playStory(id: String?) {
        guard let id, let idx = storyUnits.firstIndex(where: { $0.storyId == id }) else { return }
        playStoryUnit(at: idx)
    }

    /// Seek to the story with this identity WITHOUT forcing play (resume follows `intendedPlaying`).
    /// Same identity-resolution as `playStory`; used by scrubber/programmatic navigation.
    func seekToStory(id: String?) {
        guard let id, let idx = storyUnits.firstIndex(where: { $0.storyId == id }) else { return }
        seekToStoryUnit(at: idx)
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
        guard !storyUnits.isEmpty else { return }
        let next = currentUnitIndex + 1
        guard next < storyUnits.count else { return }   // already on the last story
        seekToStoryUnit(at: next, offsetSeconds: storyUnits[next].transitionDurationSeconds)
    }

    func stop() {
        stopInternal(sendEvents: true)
    }

    /// Stop audio and reset player state. Callers that need the play events MUST call
    /// detachSummary() FIRST (stopInternal nils _bulletinId/_profileId and clears the event buffer).
    func stopSilently() {
        stopInternal(sendEvents: false)
    }

    /// A self-contained /summary payload snapshotted BEFORE teardown so it survives stopSilently()
    /// (which nils the ids + clears the buffer) and can be POSTed from a Task the presenting view
    /// doesn't own (so dismissing the player can't cancel it mid-flight).
    struct SummaryPayload { let bulletinId: Int; fileprivate let request: SummaryRequest }

    /// Synchronously drain the accumulated events into a payload (MainActor-atomic). Call this on
    /// the main actor BEFORE stopSilently(), then hand the result to postSummary().
    func detachSummary() -> SummaryPayload? {
        guard let (bid, pid, events) = drainSummary() else {
            flushLog.log("detach: nothing to flush (bulletin=\(self._bulletinId ?? -1, privacy: .public))")
            return nil
        }
        flushLog.log("detach: \(events.count, privacy: .public) events bulletin=\(bid, privacy: .public) actions=\(events.map { $0.action }.joined(separator: ","), privacy: .public)")
        return SummaryPayload(bulletinId: bid, request: summaryRequest(pid: pid, events: events))
    }

    /// POST a previously-detached payload and AWAIT it — so an immediately-following home-preview
    /// fetch sees the committed consumed states. Owns no player state, so it's safe to run in a
    /// Task on the parent view after the player screen is dismissed. Best-effort: on failure the
    /// stories simply stay queued and re-appear until a later successful play/flush (no crash, no
    /// block). Returns whether the POST succeeded.
    @discardableResult
    func postSummary(_ payload: SummaryPayload?) async -> Bool {
        guard let payload else { return false }
        do {
            let resp = try await client.post("data/bulletins/\(payload.bulletinId)/summary",
                                             body: payload.request) as StatusResponse
            flushLog.log("post: OK bulletin=\(payload.bulletinId, privacy: .public) updated=\(resp.updated ?? -1, privacy: .public)")
            return true
        } catch {
            flushLog.error("post: FAILED bulletin=\(payload.bulletinId, privacy: .public) error=\(String(describing: error), privacy: .public)")
            return false
        }
    }

    #if DEBUG
    /// Test seam: seed a play event + the ids the flush needs, so a unit test can drive the real
    /// exit-path capture (detachSummary — what closePlayer calls on Home navigation).
    func _testSeedEvent(bulletinId: Int, profileId: Int, storyHash: String, storyId: String?, action: String) {
        _bulletinId = bulletinId
        _profileId = profileId
        accumulatedEvents.append(StoryEvent(storyHash: storyHash, storyId: storyId, action: action, positionPct: 0))
    }

    /// Test seam: the JSON the flush would POST, via the real detachSummary() path (or nil if empty).
    func _testDetachedSummaryJSON() -> Data? {
        guard let payload = detachSummary() else { return nil }
        return try? JSONEncoder().encode(payload.request)
    }

    /// Test seam: a 2-story briefing whose SECOND story is still synthesising (url == nil), currently
    /// playing the first — so a forward `seekToStoryUnit` hits the pending-tap path. Exercises the
    /// real skip → accumulate chain for the regression where the pending early-return dropped skips.
    func _testConfigurePendingSkip(currentHash: String, currentStoryId: String) {
        _bulletinId = 215
        _profileId = 27
        player = AVQueuePlayer()
        func seg(_ i: Int, _ h: String, _ sid: String, url: String?) -> ManifestSegment {
            ManifestSegment(index: i, type: "story", url: url, durationMs: 1000, storyHash: h,
                            storyId: sid, title: "T\(i)", sources: [], prevStoryId: nil, nextStoryId: nil)
        }
        let s0 = seg(1, currentHash, currentStoryId, url: "https://example.com/0.mp3")  // current, READY
        let s1 = seg(2, "hNext", "sNext", url: nil)                                     // target, PENDING
        segments = [s0, s1]
        storyUnits = [
            StoryUnit(index: 0, transitionSegment: nil, storySegments: [s0], cumulativeStartSeconds: 0),
            StoryUnit(index: 1, transitionSegment: nil, storySegments: [s1], cumulativeStartSeconds: 1),
        ]
        currentSegment = s0
        currentUnitIndex = 0
    }

    /// Test seam: a `storyCount`-story briefing with the playhead on story 0. Story 0 is READY;
    /// every later story is PENDING (url == nil) so a forward `seekToStoryUnit` early-returns after
    /// recording skips (no queue rebuild → hermetic, same shape as `_testConfigurePendingSkip`).
    /// When `liveIsBridge`, the live segment is story 0's BRIDGE (a non-story transition) — the
    /// build-47/48 state where `currentSegment.isStory` was false and the skip was silently dropped.
    /// Story `u` is seeded with hash "h\(u)" / id "s\(u)".
    func _testConfigureForwardSkip(storyCount: Int, liveIsBridge: Bool) {
        _bulletinId = 222
        _profileId = 28
        player = AVQueuePlayer()
        var segs: [ManifestSegment] = []
        var units: [StoryUnit] = []
        var idx = 0
        for u in 0..<storyCount {
            let ready = (u == 0)
            let trans = ManifestSegment(index: idx, type: "transition",
                                        url: ready ? "https://example.com/t\(u).mp3" : nil,
                                        durationMs: 500, storyHash: nil, storyId: nil,
                                        title: nil, sources: [], prevStoryId: nil, nextStoryId: nil)
            idx += 1
            let story = ManifestSegment(index: idx, type: "story",
                                        url: ready ? "https://example.com/s\(u).mp3" : nil,
                                        durationMs: 1000, storyHash: "h\(u)", storyId: "s\(u)",
                                        title: "T\(u)", sources: [], prevStoryId: nil, nextStoryId: nil)
            idx += 1
            segs.append(trans); segs.append(story)
            units.append(StoryUnit(index: u, transitionSegment: trans, storySegments: [story],
                                   cumulativeStartSeconds: Double(u)))
        }
        segments = segs
        storyUnits = units
        currentUnitIndex = 0
        currentSegment = liveIsBridge ? units[0].transitionSegment : units[0].storySegment
        // During a bridge, currentPositionPct is STALE (handlePeriodicTime only advances it while a
        // story segment is live) — seed a high value to prove the abandoned-position guard zeroes it.
        currentPositionPct = liveIsBridge ? 0.95 : 0
        playerState = .playing
    }

    /// Test seam: an N-story briefing in the given id order, playhead on story 0, NOT intending to
    /// play (so a ready seek doesn't touch audio). `pending` ids are left nil-url (not ready).
    func _testConfigureStories(ids: [String], pending: Set<String> = []) {
        _bulletinId = 500
        _profileId = 99
        player = AVQueuePlayer()
        _buildTestUnits(ids: ids, ready: Set(ids).subtracting(pending))
        currentUnitIndex = 0
        currentSegment = storyUnits.first?.storySegment
        intendedPlaying = false
        playerState = .paused
    }

    /// Test seam: rebuild the running order into a new id order (and readiness), simulating a dedup
    /// reconcile that renumbers/removes rows. Re-resolves the pending spinner by identity, as the
    /// real rebuildTimeline does.
    func _testReorderStories(ids: [String], pending: Set<String> = []) {
        let playingId = currentStoryId   // re-anchor the current index by identity, as reconcile does
        _buildTestUnits(ids: ids, ready: Set(ids).subtracting(pending))
        storyCount = storyUnits.count
        if let pid = playingId, let i = storyUnits.firstIndex(where: { $0.storyId == pid }) {
            currentUnitIndex = i
        }
    }

    private func _buildTestUnits(ids: [String], ready: Set<String>) {
        var segs: [ManifestSegment] = []
        var units: [StoryUnit] = []
        var idx = 0
        for (u, sid) in ids.enumerated() {
            let r = ready.contains(sid)
            let trans = ManifestSegment(index: idx, type: "transition", url: r ? "https://e/t\(u).mp3" : nil,
                                        durationMs: 500, storyHash: nil, storyId: nil, title: nil,
                                        sources: [], prevStoryId: nil, nextStoryId: nil)
            idx += 1
            let story = ManifestSegment(index: idx, type: "story", url: r ? "https://e/s\(u).mp3" : nil,
                                        durationMs: 1000, storyHash: "h\(sid)", storyId: sid, title: "T\(sid)",
                                        sources: [], prevStoryId: nil, nextStoryId: nil)
            idx += 1
            segs.append(trans); segs.append(story)
            units.append(StoryUnit(index: u, transitionSegment: trans, storySegments: [story],
                                   cumulativeStartSeconds: Double(u)))
        }
        segments = segs
        _storySegments = segs.filter { $0.isStory }
        storyUnits = units
        storyCount = units.count
    }

    /// Test seam: record a pending tap by identity (as the pending branch does) with no queue rebuild.
    func _testSetPendingTap(id: String) { pendingTapStoryId = id }
    var _testPendingStoryId: String? { pendingTapStoryId }
    func _testFulfilPendingTapIfReady() { fulfilPendingTapIfReady() }

    /// Test seam: build story segments with EXPLICIT backend indices (possibly NON-contiguous, as
    /// after a dedup reconcile removes a middle story) so a by-array-position subscript would
    /// misalign. Each spec: (backendIndex, storyId, ready). Transition-free, for the merge/guard tests.
    func _testConfigureExplicitIndices(_ specs: [(Int, String, Bool)]) {
        _bulletinId = 700
        _profileId = 99
        player = AVQueuePlayer()
        var segs: [ManifestSegment] = []
        var units: [StoryUnit] = []
        for (u, spec) in specs.enumerated() {
            let s = ManifestSegment(index: spec.0, type: "story", url: spec.2 ? "https://e/\(spec.1).mp3" : nil,
                                    durationMs: 1000, storyHash: "h\(spec.1)", storyId: spec.1, title: "T\(spec.1)",
                                    sources: [], prevStoryId: nil, nextStoryId: nil)
            segs.append(s)
            units.append(StoryUnit(index: u, transitionSegment: nil, storySegments: [s], cumulativeStartSeconds: Double(u)))
        }
        segments = segs
        _storySegments = segs
        storyUnits = units
        storyCount = units.count
        segmentReady = [:]
        for s in segs where s.url != nil { segmentReady[s.index] = true }
        currentUnitIndex = 0
        currentSegment = segs.first
        intendedPlaying = false
        playerState = .paused
    }

    var _testNavGeneration: Int { navGeneration }
    func _testBumpNav() { navGeneration &+= 1 }
    @discardableResult func _testApplyReadiness(_ r: BulletinReadiness) -> Bool { applyReadiness(r) }
    @discardableResult func _testApplyReadinessIfCurrent(_ r: BulletinReadiness, fetchGen: Int) -> Bool {
        applyReadinessIfCurrent(r, fetchGen: fetchGen)
    }
    func _testStorySegmentURL(storyId: String) -> String? { segments.first { $0.storyId == storyId }?.url }
    var _testFailedSegmentIndices: Set<Int> { failedSegmentIdx }
    var _testHoldCeilingSeconds: TimeInterval { holdCeilingSeconds }
    func _testSegmentBinding(atIndex idx: Int) -> (prev: String?, next: String?)? {
        segments.first { $0.index == idx }.map { ($0.prevStoryId, $0.nextStoryId) }
    }
    /// Test seam: force the board lead (queue.items[0]) to diverge from the first audible segment,
    /// by reversing storyUnits WITHOUT touching segments — the exact state the L-C gate must catch.
    func _testForceQueueLeadMismatch() { storyUnits = Array(storyUnits.reversed()) }
    func _testSetSelectionId(_ id: String?) { _selectionId = id }
    #endif

    /// Drain the accumulated play events (appending an abandoned event for a story mid-play) into a
    /// request, clearing the buffer. Returns nil if there's nothing to send. Callers re-queue on
    /// failure. MainActor-atomic, so concurrent flushes can't double-send (the second drain is empty).
    private func drainSummary() -> (bid: Int, pid: Int, events: [StoryEvent])? {
        guard let pid = _profileId, let bid = _bulletinId else { return nil }
        // Anchor the in-progress "abandoned" event on the LOGICAL current story unit (currentStoryUnit),
        // NOT the raw currentSegment, so closing while a bridge/transition is the live item still
        // records the story in progress (the currentSegment gate dropped it in the same states that
        // dropped skips). Suppressed once the story is already recorded skipped or completed this session.
        if let unit = currentStoryUnit, let hash = unit.storyHash,
           !_skippedStoryHashes.contains(hash), !consumedStoryHashes.contains(hash),
           playerState == .playing || playerState == .paused || playerState == .stalled {
            // Position within the CURRENT story. currentPositionPct only advances while a STORY
            // segment is live (handlePeriodicTime's isStory guard), so it goes STALE during the
            // bridge/intro LEADING INTO this story — holding the PREVIOUS story's value. Sending
            // that stale pct here would false-consume a story the user closed on before it even
            // started (abandoned ≥0.5 → consumed server-side). So report 0 unless this story's own
            // audio is actually the live item.
            let pct = (currentSegment?.isStory == true) ? currentPositionPct : 0
            accumulatedEvents.append(StoryEvent(storyHash: hash, storyId: unit.storyId,
                                                action: "abandoned", positionPct: pct))
        }
        guard !accumulatedEvents.isEmpty else { return nil }
        let events = accumulatedEvents
        accumulatedEvents = []
        return (bid, pid, events)
    }

    private func summaryRequest(pid: Int, events: [StoryEvent]) -> SummaryRequest {
        SummaryRequest(
            profileId: pid,
            events: events.map { .init(storyHash: $0.storyHash, storyId: $0.storyId, profileId: pid,
                                       action: $0.action, positionPct: $0.positionPct) },
            selectionId: _selectionId   // L-D: echo the selection so a stale batch is rejected
        )
    }

    /// Fire-and-forget flush — for background / terminate (never awaited). Re-queues on failure.
    func sendSummary() {
        guard let (bid, pid, events) = drainSummary() else { return }
        let req = summaryRequest(pid: pid, events: events)
        Task { [weak self] in
            guard let self else { return }
            do { _ = try await self.client.post("data/bulletins/\(bid)/summary", body: req) as StatusResponse }
            catch { self.accumulatedEvents.insert(contentsOf: events, at: 0) }   // retry next flush
        }
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
        _editionReconciled = false
        pendingTapStoryId = nil
        segmentReady = [:]
        teardownQueuePlayer()
        manifest       = nil
        segments       = []
        _storySegments = []
        storyUnits    = []
        _totalStoryDurationSeconds = 0
        _totalDurationSeconds = 0
        _fullStartByIndex = [:]
        playbackElapsedSeconds = 0
        loaderComplete = false
        storyCount     = 0
        currentUnitIndex     = 0
        currentSegment       = nil
        positionSeconds      = 0
        currentPositionPct   = 0
        globalPositionPct    = 0
        consumedStoryHashes  = []
        accumulatedEvents    = []
        _skippedStoryHashes  = []
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

        // BUG 1 — STALE BRIDGE: a transition bound to a (prev,next) story pair that no longer
        // matches its story neighbours in the running order must NOT play — it would name a story
        // out of place ("now onto Argentina" after Belgium). Drop it to a clean cut and continue.
        // (Mirror of backend bridge_guard. Decision: drop only — no regen; see docs/post-demo.md.)
        if seg.type == "transition", isStaleBridge(seg) {
            nextEnqueueIdx += 1
            enqueueNext()
            return
        }
        // BUG 2 — a FAILED/stalled segment (readiness state=="failed", incl. the backend stall
        // watchdog, or our client ceiling) is SKIPPED, never held: advance past it so playback
        // continues and the end gate reconciles.
        if failedSegmentIdx.contains(seg.index) {
            nextEnqueueIdx += 1
            enqueueNext()
            return
        }

        // STREAMING: a not-yet-synthesised segment has no url — HOLD here (don't advance/skip).
        // The readiness poll calls enqueueNext again the moment this segment's url arrives — or,
        // past the ceiling, marks it failed so the branch above skips it.
        guard let urlStr = seg.url else {
            if !_heldEnqueue || holdStartedIdx != seg.index {
                holdStartedAt = Date(); holdStartedIdx = seg.index
            }
            _heldEnqueue = true
            return
        }
        holdStartedAt = nil; holdStartedIdx = nil
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

    // MARK: - Bridge guard + end-gate helpers (bugs 1 & 3)

    /// Mirror of backend `bridge_guard`: a transition whose bound (prev,next) story ids no longer
    /// match the story segments actually adjacent to it is STALE and must not play. Compares
    /// against the segment-array order (catches dedup/reconcile shrink). Unbound (older) bulletins
    /// have nil ids → never stale.
    /// // UNVERIFIED — array-order check catches reconcile-shrink; a user-SKIP that reorders PLAY
    /// without shrinking the array may need a play-order variant. Verify the football/skip case.
    private func isStaleBridge(_ seg: ManifestSegment) -> Bool {
        guard seg.type == "transition" else { return false }
        guard seg.prevStoryId != nil || seg.nextStoryId != nil else { return false }
        guard let i = segments.firstIndex(where: { $0.index == seg.index }) else { return false }
        if let wantPrev = seg.prevStoryId, nearestStoryId(from: i, step: -1) != wantPrev { return true }
        if let wantNext = seg.nextStoryId, nearestStoryId(from: i, step: +1) != wantNext { return true }
        return false
    }

    private func nearestStoryId(from i: Int, step: Int) -> String? {
        var j = i + step
        while j >= 0 && j < segments.count {
            if segments[j].type == "story" { return segments[j].storyId }
            j += step
        }
        return nil
    }

    /// Any not-yet-enqueued segment that WILL actually play — excluding ones that are failed/
    /// stall-reaped or a stale bridge (both get skipped). When false, the briefing is genuinely
    /// over and `.ended` may fire.
    private func hasRemainingPlayableSegment() -> Bool {
        guard nextEnqueueIdx < segments.count else { return false }
        for i in nextEnqueueIdx..<segments.count {
            let s = segments[i]
            if failedSegmentIdx.contains(s.index) { continue }
            if s.type == "transition", isStaleBridge(s) { continue }
            return true
        }
        return false
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

        // Fire completion for the previous STORY (unit) on natural advancement — once, when its
        // LAST segment finishes, so a split lead (opener → remainder) doesn't complete twice.
        // Use the unit's hash (the seeded one), never the individual segment's.
        if let prev = prevSeg, prev.isStory, outcome == .natural,
           let unit = storyUnit(forSegment: prev), prev.index == unit.lastStorySegmentIndex,
           let hash = unit.storyHash {
            let ev = StoryEvent(storyHash: hash, storyId: unit.storyId, action: "completed", positionPct: 1.0)
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
            // HELD/EMPTY rebuild, not the end: the queue is empty because enqueue is HOLDING on a
            // not-yet-synthesised (nil-url) segment, or there are still segments left to enqueue.
            // A genuine end has enqueued EVERYTHING (nextEnqueueIdx == count) with nothing held —
            // so this guard stops a pending-tap / streaming hold from false-firing .ended (which
            // would clear intendedPlaying + null currentSegment and poison every later tap).
            // BUG 3 — end when no PLAYABLE segment remains, not merely when nextEnqueueIdx reaches
            // segments.count. Segments that will never play (failed/stall-reaped, or dropped stale
            // bridges) are never enqueued, so the old `nextEnqueueIdx < count` test blocked .ended
            // forever → ~20s dead air after the last real story. We still HOLD if a *real* (pending,
            // recoverable) segment remains — that's normal streaming, not the end.
            // // UNVERIFIED — this interacts with the false-.ended-on-skip guards above (09c76f6:
            // the currentItem/prevSeg checks). Verify on device that skip / seek / mid-stream hold
            // do NOT false-fire .ended, and that a genuine end (all real segments played) does.
            if hasRemainingPlayableSegment() { return }
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
        let clamped = max(0, min(index, storyUnits.count - 1))
        guard clamped < storyUnits.count, let p = player else { return }
        let unit = storyUnits[clamped]

        // Resolve the segment this tap will START on (transition if we begin within it, else the
        // story/opener) BEFORE touching the queue — the pending check must key off THAT segment,
        // not just the opener.
        guard let (startArrayIdx, seekSeconds) = startSegment(for: unit, offsetSeconds: offsetSeconds) else { return }

        // Record a "skipped" event for EVERY story the playhead moves forward PAST — anchored to the
        // LOGICAL current story unit(s) (currentUnitIndex), NOT the raw currentSegment. The live item
        // at skip time is frequently a bridge/transition — or a nil buffering gap while the briefing is
        // still synthesising — and the old `currentSegment.isStory` gate silently DROPPED the skip in
        // exactly those states, on BOTH the live /event and the batched /summary paths (the build-47/48
        // "next-button skips never registered" bug). currentUnitIndex "stays the current story even
        // during its bridge/intro", so the departing story is always identifiable. Runs BEFORE the
        // pending-tap early return so a skip onto a still-synthesising target still counts (#190). A
        // forward jump over several stories (scrubber) records each story it clears. Deduped via
        // _skippedStoryHashes (repeat taps onto a pending target can't double-fire) and never re-fires
        // for a story already completed.
        if clamped > currentUnitIndex {
            for i in max(0, currentUnitIndex)..<clamped where i < storyUnits.count {
                let leaving = storyUnits[i]
                guard let hash = leaving.storyHash,
                      !_skippedStoryHashes.contains(hash),
                      !consumedStoryHashes.contains(hash) else { continue }
                _skippedStoryHashes.insert(hash)
                let ev = StoryEvent(storyHash: hash, storyId: leaving.storyId, action: "skipped",
                                    positionPct: i == currentUnitIndex ? currentPositionPct : 0)
                fireEvent(ev)
                accumulatedEvents.append(ev)
            }
        }

        // PENDING TAP — the start segment isn't synthesised yet. Do NOT tear down what's playing:
        // bump the story to the front of the synthesis queue, mark the row buffering, and record
        // the intent. applyReadiness fulfils the tap (real seek + play) the moment its audio
        // arrives. Current playback keeps going meanwhile — no removeAllItems, so no empty-queue
        // rebuild and no false end-of-briefing.
        if segments[startArrayIdx].url == nil {
            pendingTapStoryId = unit.storyId
            if let sid = unit.storySegment.storyId { prioritise(storyId: sid) }
            return
        }

        // READY — clear any prior pending-tap intent and do the real rebuild.
        pendingTapStoryId = nil

        // Bump the navigation token: any earlier in-flight seek's async completion will
        // now no-op, so rapid taps can't clobber each other (no stale resume / double
        // advance). Resume is decided by the LIVE `intendedPlaying`, not a snapshot — so
        // a pause landing mid-rebuild sticks, and a rapid tap while playing still resumes.
        navGeneration &+= 1
        let gen = navGeneration

        prevOutcome = .userSkip

        // Update tracking immediately for responsive UI.
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
        guard _totalDurationSeconds > 0, !segments.isEmpty, !storyUnits.isEmpty else { return }
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
            let firstStoryStart = storyUnits.first.map { _fullStartByIndex[$0.storySegment.index] ?? 0 } ?? 0
            seekToStoryUnit(at: target < firstStoryStart ? 0 : storyUnits.count - 1)
        }
    }

    func seekToGlobalFraction(_ fraction: Double) {
        guard !storyUnits.isEmpty, _totalStoryDurationSeconds > 0 else { return }
        let targetSecs = max(0, min(1, fraction)) * _totalStoryDurationSeconds

        var unitIndex    = storyUnits.count - 1
        var offsetInUnit = storyUnits.last!.totalDurationSeconds

        for unit in storyUnits {
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
        storyUnits.first { $0.owns(segmentIndex: seg.index) }
    }

    /// Builds the ordered story-unit list with pre-computed cumulative start times.
    static func buildStoryUnits(from segments: [ManifestSegment]) -> [StoryUnit] {
        var units: [StoryUnit] = []
        var pendingTransition: ManifestSegment?
        var curTransition: ManifestSegment?
        var curStories: [ManifestSegment] = []   // accumulating same-story_id segments
        var cumulativeSecs = 0.0

        func flush() {
            guard !curStories.isEmpty else { return }
            let unit = StoryUnit(
                index: units.count,
                transitionSegment: curTransition,
                storySegments: curStories,
                cumulativeStartSeconds: cumulativeSecs
            )
            cumulativeSecs += unit.totalDurationSeconds
            units.append(unit)
            curStories = []
            curTransition = nil
        }

        for seg in segments {
            switch seg.type {
            case "transition":
                flush()                     // a transition ends the current story unit
                pendingTransition = seg
            case "story":
                if let last = curStories.last, seg.storyId != nil, last.storyId == seg.storyId {
                    curStories.append(seg)  // continuation (opener → remainder) of the same story
                } else {
                    flush()
                    curTransition = pendingTransition
                    curStories = [seg]
                    pendingTransition = nil
                }
            default:
                flush()
                pendingTransition = nil     // sting / intro / outro reset the pending transition
            }
        }
        flush()
        return units
    }

    // MARK: - Event firing

    private func fireEvent(_ ev: StoryEvent) {
        guard let pid = _profileId, let bid = _bulletinId else { return }
        let req = EventRequest(
            profileId:   pid,
            storyHash:   ev.storyHash,
            action:      ev.action,
            positionPct: ev.positionPct,
            selectionId: _selectionId       // L-D: echo the selection; server 409s a stale one
        )
        Task { _ = try? await self.client.post("data/bulletins/\(bid)/event", body: req) as StatusResponse }
    }

    // MARK: - Lock screen

    /// The story unit the playhead is in (clamped) — stays the current story even during
    /// its bridge/intro, mirroring NowPlayingView's board. nil when there are no stories.
    private var currentStoryUnit: StoryUnit? {
        guard !storyUnits.isEmpty else { return nil }
        let idx = min(max(0, currentUnitIndex), storyUnits.count - 1)
        return storyUnits[idx]
    }

    // Now-playing BOARD — the current story's display fields, resolved via the single live cursor so
    // the view holds no index into the running order (the last view-side index-addressed path).
    var currentStoryTitle: String? { currentStoryUnit?.title }
    var currentStorySources: [String] { currentStoryUnit?.storySegment.sources ?? [] }
    /// 1-based position of the playing story for the "N / total" header (0 when empty).
    var currentStoryNumber: Int {
        guard !storyUnits.isEmpty else { return 0 }
        return min(max(0, currentUnitIndex), storyUnits.count - 1) + 1
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
    static let stageSeconds: Double = 4.0        // each paced stage — a brisk-but-readable cadence (tuned
                                                 // down from 5s now the connective-split backend cuts
                                                 // time-to-first-play; streaming still starts playback on
                                                 // readiness, not this timer).
    static let pacedStages: Int = 4              // evenly-timed stages covering ~0–16s
    static var visibleStages: Int { pacedStages + 1 }   // + a final dwell stage for overrun
    static var expectedSeconds: Double { stageSeconds * Double(pacedStages) }   // ~16s
    // Small ABSOLUTE floor so a cached/instant briefing shows a readable stage or two rather
    // than flashing — DECOUPLED from stageSeconds so the paced stage cadence doesn't drag the
    // fast/cached path out (a `stageSeconds * 2` rule would force an 8s hold on already-ready
    // briefings). A fast briefing still dismisses on readiness above this floor.
    static let minLoaderSeconds: Double = 4.0

    /// Which stage (0-based) is showing at `elapsed` seconds — PURELY from the timer, not
    /// backend progress. Advances one stage every `stageSeconds` (evenly), clamped at the
    /// final dwell stage, which then holds until the gate dismisses the loader on readiness.
    static func stageIndex(elapsed: Double) -> Int {
        max(0, min(visibleStages - 1, Int(elapsed / stageSeconds)))
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
