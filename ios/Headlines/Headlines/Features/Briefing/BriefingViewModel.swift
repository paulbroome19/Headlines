import Combine
import Foundation

@MainActor
final class BriefingViewModel: ObservableObject {

    // MARK: - State

    enum State: Equatable {
        case idle
        case loadingProfiles
        case noProfiles
        case profileSelected
        case generating       // fetching manifest
        case downloadingAudio // manifest received, first segment loading
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

    // MARK: - Dependencies

    let profileService: ProfileServicing
    let bulletinPlayer = BulletinPlayer()

    private var cancellables = Set<AnyCancellable>()
    private var lastBackgroundedAt: Date?

    // MARK: - Init

    init(profileService: ProfileServicing = ProfileService()) {
        self.profileService = profileService

        // Forward BulletinPlayer state changes into BriefingViewModel.State.
        bulletinPlayer.$playerState
            .receive(on: RunLoop.main)
            .sink { [weak self] ps in self?.syncPlayerState(ps) }
            .store(in: &cancellables)

        // Trigger UI refresh when story index or current segment changes.
        bulletinPlayer.$currentSegment
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        bulletinPlayer.$storyIndex
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        bulletinPlayer.$currentPositionPct
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        bulletinPlayer.$globalPositionPct
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        bulletinPlayer.$currentUnitIndex
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        bulletinPlayer.$consumedStoryHashes
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
    }

    // MARK: - Computed (mapped from BulletinPlayer)

    var progress: Double              { bulletinPlayer.currentPositionPct }
    var audioDuration: TimeInterval   { bulletinPlayer.currentSegment?.durationSeconds ?? 0 }
    var globalProgress: Double        { bulletinPlayer.globalPositionPct }
    var totalStoryDuration: TimeInterval { bulletinPlayer.totalStoryDurationSeconds }
    var currentStoryIndex: Int?       { activeStoryIndex }
    var hasStoryTimings: Bool         { bulletinPlayer.storyCount > 0 }
    var consumedStoryHashes: Set<String> { bulletinPlayer.consumedStoryHashes }

    /// The hero headline shown above the player controls.
    /// Returns a greeting before story 1 begins; the last story's title during the outro.
    var nowPlayingTitle: String {
        let units = bulletinPlayer.storyUnits
        switch bulletinPlayer.currentSegment?.type {
        case "transition", "story":
            let idx = bulletinPlayer.currentUnitIndex
            if idx < units.count, let title = units[idx].title { return title }
            return greetingText
        case "outro":
            if let title = units.last?.title { return title }
            return greetingText
        default:
            // nil (not loaded), "sting", or "intro" — show personalised greeting
            return greetingText
        }
    }

    /// "Morning Briefing" / "Afternoon Briefing" / "Evening Briefing" — top-left masthead.
    var briefingLabel: String {
        let tod = Self.timePeriod()
        return tod.prefix(1).uppercased() + tod.dropFirst() + " Briefing"
    }

    private var greetingText: String {
        let tod = Self.timePeriod()
        if let name = selectedProfile?.name, !name.isEmpty {
            return "Good \(tod), \(name)"
        }
        return "Good \(tod)"
    }

    /// Single source of truth for the time-of-day period used by both label and greeting.
    /// Morning: before 12:00 · Afternoon: 12:00–16:59 · Evening: 17:00 onward.
    private static func timePeriod() -> String {
        let hour = Calendar.current.component(.hour, from: Date())
        if hour < 12 { return "morning" }
        if hour < 17 { return "afternoon" }
        return "evening"
    }

    /// BulletinResult synthesised from the live manifest — keeps BriefingView unchanged.
    var bulletin: BulletinResult? {
        guard let m = bulletinPlayer.manifest else { return nil }
        let stories = bulletinPlayer.storySegments.map { seg in
            BulletinStory(
                id:        seg.storyHash ?? String(seg.index),
                headline:  seg.title ?? "",
                category:  nil,
                startTime: nil
            )
        }
        return BulletinResult(
            profileId:      bulletinPlayer.profileId ?? 0,
            profileName:    selectedProfile?.name ?? "",
            bulletinId:     m.bulletinId,
            storyCount:     bulletinPlayer.storyCount,
            bulletinCached: false,
            script:         nil,
            audioId:        nil,
            audioUrl:       nil,
            voice:          nil,
            audioCached:    false,
            stories:        stories
        )
    }

    var canGenerate: Bool {
        guard selectedProfile != nil else { return false }
        switch state {
        case .generating, .downloadingAudio, .loadingProfiles: return false
        default: return true
        }
    }

    var canTogglePlayPause: Bool {
        switch bulletinPlayer.playerState {
        case .buffering, .playing, .paused, .stalled: return true
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
        bulletinPlayer.stop()
        state = .profileSelected
    }

    // MARK: - Bulletin generation

    func generateBulletin() async {
        guard let profile = selectedProfile else { return }
        await bulletinPlayer.load(profileId: profile.id)
    }

    // MARK: - Playback controls

    func play()             { bulletinPlayer.play() }
    func pause()            { bulletinPlayer.pause() }
    func togglePlayPause()  { bulletinPlayer.togglePlayPause() }

    func nextStory()        { bulletinPlayer.skip() }
    func previousStory()    { bulletinPlayer.skipBack() }
    func seekToStory(at index: Int) { bulletinPlayer.seekToStoryUnit(at: index) }
    func seekToGlobalFraction(_ fraction: Double) { bulletinPlayer.seekToGlobalFraction(fraction) }

    // MARK: - App lifecycle

    func handleBackground() {
        lastBackgroundedAt = Date()
        bulletinPlayer.sendSummary()
    }

    func handleForeground() {
        guard let bg = lastBackgroundedAt, Date().timeIntervalSince(bg) > 300 else { return }
        lastBackgroundedAt = nil
        guard selectedProfile != nil else { return }
        Task { await generateBulletin() }
    }

    // MARK: - Private

    private var activeStoryIndex: Int? {
        switch bulletinPlayer.playerState {
        case .playing, .paused, .stalled, .buffering: return bulletinPlayer.currentUnitIndex
        default: return nil
        }
    }

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

    private func syncPlayerState(_ ps: BulletinPlayer.PlayerState) {
        switch ps {
        case .idle:
            // Don't clobber profile-selection states driven by fetchProfiles.
            break
        case .loadingManifest:
            state = .generating
        case .preparing:
            // Readiness gate (added in #64): manifest received, audio generating —
            // the generation loader is shown. Maps to the existing loader state.
            state = .downloadingAudio
        case .buffering:
            // Show PlayerView immediately so the user can see stories and tap play
            // while the first audio segment finishes loading.
            state = .readyToPlay
        case .playing:
            state = .playing
        case .paused:
            state = .paused
        case .stalled:
            state = .playing   // show as playing; buffering spinner handled at player level
        case .ended:
            state = .ended
        case .failed(let msg):
            state = .failed(msg)
        }
    }
}
