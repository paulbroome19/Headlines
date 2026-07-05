//
//  NowPlayingView.swift
//  Headlines
//
//  The playback ("now playing") screen — the LIGHT reading page with the dark
//  flap board embedded as the now-playing instrument. Built to the locked
//  monochrome playback spec, driven live by BulletinPlayer.
//
//  Phase-B degradations (data not yet in the manifest — flagged, not faked):
//   • category chip ("· TECH") — omitted until the manifest exposes per-story
//     category (the data exists in the backend stories_list).
//   • SOURCES — hidden until the manifest exposes per-story source names; a
//     credibility ranking is a further backend addition. No fake sources.
//

import SwiftUI

struct NowPlayingView: View {
    @ObservedObject var player: BulletinPlayer
    var onClose: () -> Void = {}
    var onRetry: () -> Void = {}
    var now: Date = Date()

    // Light page palette (locked monochrome — no warmth).
    private let pageBG  = Color(hex: 0xF7F7F5)
    private let ink     = Color(hex: 0x141414)
    private var inkMuted: Color { ink.opacity(0.45) }

    // Generation loader: five cosmetic status lines (one picked per band, per
    // generation → 5⁵ combos). Purely visual; NOT tied to backend stages.
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var loaderLines: [String] = []
    /// When the loader first appeared — anchors the per-stage minimum dwell (#7).
    @State private var loaderStart = Date()

    var body: some View {
        ZStack {
            pageBG.ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                Group {
                    switch player.playerState {
                    case .idle, .loadingManifest, .preparing:
                        // Solari loader is the IMMEDIATE state on tap — no default
                        // spinner beforehand (it renders from .idle through readiness).
                        preparingState
                    case .empty:
                        emptyState
                    case .failed:
                        failedState
                    default:
                        playerBody
                    }
                }
            }
            .padding(.horizontal, 20)
        }
        .onAppear { Haptics.prepareTransport() }
        // Gentle success thud the instant the briefing becomes ready to play.
        .onChange(of: player.loaderComplete) { _, ready in
            if ready { Haptics.briefingReady() }
        }
    }

    // MARK: - Top bar (back + masthead)

    private var topBar: some View {
        VStack(spacing: 12) {
            HStack {
                Button(action: onClose) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(ink)
                        .frame(width: 40, height: 40, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                Spacer()
            }

            HStack(alignment: .firstTextBaseline) {
                Text(TimeOfDay.current(now).briefingEdition)
                    .font(.label(12)).tracking(2)
                    .foregroundColor(ink)
                Spacer()
                Text(dateText)
                    .font(.label(12)).tracking(2)
                    .foregroundColor(inkMuted)
            }

            Rectangle().fill(ink.opacity(0.12)).frame(height: 1)
        }
        .padding(.top, 8)
    }

    private var dateText: String {
        let f = DateFormatter()
        f.dateFormat = "EEE d MMM"
        return f.string(from: now).uppercased()
    }

    // MARK: - Loading / failed states

    /// Graceful empty state (fix 4): the filters matched no stories right now — a
    /// normal result, not an error. On-brand, calm, no raw JSON. Both the top-bar
    /// chevron and the HOME machined disc return Home (`onClose` → `closePlayer`).
    private var emptyState: some View {
        VStack(spacing: 20) {
            Spacer()
            Text("YOU'RE ALL CAUGHT UP")
                .font(.label(15)).tracking(2)
                .foregroundColor(ink)
            Text("Check back a little later.")
                .font(.label(12))
                .foregroundColor(inkMuted)
                .multilineTextAlignment(.center)
                .lineSpacing(3)
                .padding(.horizontal, 24)
            Button(action: onClose) {
                MachinedDisc(diameter: 64) {
                    Image(systemName: "house.fill")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundColor(BoardColors.character)
                }
            }
            .buttonStyle(MachinedDiscButtonStyle())
            .accessibilityLabel("Home")
            .padding(.top, 4)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// Generation loader — mirrors the Playback layout: the dark Solari board sits in the
    /// now-playing card's position (churning through status lines) and a determinate
    /// 0→100% bar sits in the scrubber's position.
    ///
    /// PACING IS A FIXED, EVEN VISUAL SEQUENCE — decoupled from backend progress. The paced
    /// stages advance purely on a timer (`LoaderTiming.stageIndex`), each exactly
    /// `stageSeconds`, so no stage can freeze while a slow manifest loads. The bar is
    /// elapsed-driven too, so it moves at a steady rate. Real readiness governs only
    /// DISMISSAL: the gate holds the loader (and thus the final DWELL stage) until
    /// `safe_to_start`, then leaves `.preparing` → the loader is replaced by the player. So
    /// the paced stages are evenly timed and only the last stage dwells to absorb the wait.
    private var preparingState: some View {
        VStack(spacing: 0) {
            // Board + progress bar are ONE coupled card unit, PINNED TO THE TOP at the same
            // vertical level as the playback now-playing card (Spacer(16) → card), with the
            // bar 18pt beneath — the same rhythm as the scrubber under that card. So the
            // loader "transforms" seamlessly into the playback screen: board where the card
            // appears, bar where the scrubber appears. The rest of the screen is empty below.
            VStack(spacing: 18) {
                // STAGE WORD — one stage per `stageSeconds` on a plain timer (coarse cadence
                // is enough; the word only changes at stage boundaries). NOT loadProgress.
                TimelineView(.periodic(from: loaderStart, by: 0.2)) { ctx in
                    let elapsed = max(0, ctx.date.timeIntervalSince(loaderStart))
                    loaderBoard(lineIndex: LoaderTiming.stageIndex(elapsed: elapsed))
                }
                // DETERMINATE BAR — even step per stage (LoaderTiming.barFill); the final
                // raise to 100% happens on ready (player.loaderComplete). The bar's own
                // .animation ramps each step so the climb reads smooth + steady.
                TimelineView(.periodic(from: loaderStart, by: 0.05)) { ctx in
                    let elapsed = max(0, ctx.date.timeIntervalSince(loaderStart))
                    loaderProgressBar(fill: player.loaderComplete ? 1.0 : LoaderTiming.barFill(elapsed: elapsed))
                }
            }
            .padding(.top, 16)   // pin to the top — same level as the playback card
            Spacer(minLength: 0) // empty space below
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear {
            // Anchor the timer to when the loader appears (fresh each load, incl. retry).
            loaderStart = Date()
            loaderLines = LoaderStatusBoard.pickWords()
        }
    }

    /// The dark Solari board — same position/material as `nowPlayingBoard`.
    private func loaderBoard(lineIndex: Int) -> some View {
        let text = loaderLines.indices.contains(lineIndex) ? loaderLines[lineIndex] : "PREPARING"
        return VStack(spacing: 0) {
            HStack(spacing: 8) {
                Circle()
                    .fill(BoardColors.character.opacity(0.75))
                    .frame(width: 6, height: 6)
                Text("ASSEMBLING")
                    .font(.label(11)).tracking(2)
                    .foregroundColor(BoardColors.character.opacity(0.65))
                Spacer()
            }
            .padding(.horizontal, 18)
            .padding(.top, 16)
            .padding(.bottom, 14)

            LoaderStatusBoard(text: text, band: lineIndex, reduceMotion: reduceMotion)
                .padding(.horizontal, 16)
                .padding(.bottom, 18)
        }
        .background(boardMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: .black.opacity(0.18), radius: 16, y: 8)
    }

    /// Determinate progress bar in the scrubber's position. `fill` (0–1) is the
    /// elapsed-driven, evenly-paced value from LoaderTiming — NOT backend progress — so it
    /// advances smoothly in quarters and never sits frozen while the manifest loads.
    /// No numeric counter: the split-flap board flicker is the hero motion; the bar just
    /// fills quietly as reassurance — one moving thing, not a competing percentage.
    private func loaderProgressBar(fill: Double) -> some View {
        VStack(spacing: 10) {
            HStack {
                Text("ASSEMBLING YOUR BRIEFING")
                    .font(.label(11)).tracking(2)
                    .foregroundColor(inkMuted)
                Spacer()
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(ink.opacity(0.12))
                    Capsule().fill(ink)
                        .frame(width: max(0, geo.size.width * fill))
                        // Ramp each per-stage step (and the final raise to 100%) so the bar
                        // reads as a smooth, steady climb rather than a hard jump.
                        .animation(.easeInOut(duration: 0.55), value: fill)
                }
            }
            .frame(height: 4)
        }
        .padding(.horizontal, 4)
    }

    /// Calm, human fail state — never a status code or raw error. The ↻ machined
    /// disc re-triggers the real briefing load (`onRetry` → `startBriefing`), which
    /// shows the loader again and lands on playback / empty / this state.
    private var failedState: some View {
        VStack(spacing: 20) {
            Spacer()
            Text("COULDN'T LOAD BRIEFING")
                .font(.label(13)).tracking(2)
                .foregroundColor(ink)
            Text("Something went wrong on our end. Try again in a moment.")
                .font(.label(12))
                .foregroundColor(inkMuted)
                .multilineTextAlignment(.center)
                .lineSpacing(3)
                .padding(.horizontal, 32)
            Button(action: onRetry) {
                MachinedDisc(diameter: 64) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundColor(BoardColors.character)
                }
            }
            .buttonStyle(MachinedDiscButtonStyle())
            .accessibilityLabel("Try again")
            .padding(.top, 4)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Player body

    private var playerBody: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 16)
            nowPlayingBoard
            Spacer(minLength: 18)
            scrubberSection
            Spacer(minLength: 20)
            transport
            Spacer(minLength: 18)
            tracklist
        }
    }

    // MARK: - The dark instrument (embedded flap board)

    private var nowPlayingBoard: some View {
        VStack(spacing: 0) {
            // Header strip
            HStack(spacing: 8) {
                Circle()
                    .fill(BoardColors.character.opacity(player.isPlaying ? 0.9 : 0.4))
                    .frame(width: 6, height: 6)
                Text(boardHeader)
                    .font(.label(11)).tracking(2)
                    .foregroundColor(BoardColors.character.opacity(0.65))
                Spacer()
            }
            .padding(.horizontal, 18)
            .padding(.top, 16)
            .padding(.bottom, 14)

            // Headline on flap cells, off-white, wrapping rows.
            headlineFlapCells
                .padding(.horizontal, 16)
                .padding(.bottom, 16)

            // Hinge seam + notch pins.
            seam.padding(.vertical, 2)

            // Bottom flap: SOURCES (ranked top 3) — the whole-bulletin timer in the
            // scrubber replaces the old per-story "STORY LEFT" that lived here.
            // Hidden gracefully (no placeholder) until the backend surfaces sources.
            if !sources.isEmpty {
                HStack(spacing: 0) {
                    Text(sources.prefix(3).joined(separator: " · ").uppercased())
                        .font(.label(11)).tracking(1.5)
                        .foregroundColor(BoardColors.character.opacity(0.55))
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 18)
                .padding(.top, 14)
                .padding(.bottom, 18)
            } else {
                // No sources yet — keep a slim inset so the board stays balanced.
                Color.clear.frame(height: 16)
            }
        }
        .background(boardMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: .black.opacity(0.18), radius: 16, y: 8)
    }

    /// The flap-material fill for the embedded board — same charcoal gradient +
    /// faint grain as the rest of the app's board world.
    private var boardMaterial: some View {
        ZStack {
            LinearGradient(colors: [BoardColors.topFlapTop, BoardColors.botFlapBottom],
                           startPoint: .top, endPoint: .bottom)
            Image(uiImage: BoardGrain.image)
                .resizable(resizingMode: .tile)
                .opacity(0.012)
                .allowsHitTesting(false)
        }
    }

    private var seam: some View {
        ZStack {
            Rectangle().fill(BoardColors.seam).frame(height: 1.5)
            HStack {
                Capsule().fill(BoardColors.seam).frame(width: 14, height: 6).padding(.leading, 18)
                Spacer()
                Capsule().fill(BoardColors.seam).frame(width: 14, height: 6).padding(.trailing, 18)
            }
        }
    }

    // MARK: Headline flap cells (auto-sized + word-wrapped)

    private var headlineFlapCells: some View {
        GeometryReader { geo in
            let layout = headlineLayout(currentHeadline.uppercased(), width: geo.size.width)
            VStack(alignment: .leading, spacing: layout.gap) {
                ForEach(Array(layout.rows.enumerated()), id: \.offset) { _, row in
                    HStack(spacing: layout.gap) {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, ch in
                            if ch == " " {
                                Color.clear.frame(width: layout.cell.width * 0.5, height: layout.cell.height)
                            } else {
                                FlapCell(glyph: ch, size: layout.cell)
                            }
                        }
                    }
                }
            }
            .frame(width: geo.size.width, alignment: .leading)
        }
        .frame(height: headlineHeight)
    }

    /// Word-wrap the headline into flap-cell rows, auto-sizing the cell so the
    /// longest word fits and the whole headline stays within a row cap.
    private func headlineLayout(_ text: String, width: CGFloat) -> (cell: CGSize, gap: CGFloat, rows: [[Character]]) {
        let gap: CGFloat = 2.5
        let maxCellW: CGFloat = 22
        let minCellW: CGFloat = 11
        let maxRows = 4
        let words = text.split(separator: " ").map(String.init)

        func rowsAt(_ cellW: CGFloat) -> [[Character]] {
            var rows: [[Character]] = []
            var current = ""
            for w in words {
                let candidate = current.isEmpty ? w : current + " " + w
                let cWidth = CGFloat(candidate.count) * cellW + CGFloat(max(0, candidate.count - 1)) * gap
                if current.isEmpty || cWidth <= width { current = candidate }
                else { rows.append(Array(current)); current = w }
            }
            if !current.isEmpty { rows.append(Array(current)) }
            return rows
        }

        // Largest cell that fits the longest single word AND keeps rows ≤ cap.
        var cellW = maxCellW
        if let longest = words.map(\.count).max(), longest > 0 {
            cellW = min(maxCellW, (width - CGFloat(max(0, longest - 1)) * gap) / CGFloat(longest))
        }
        cellW = max(minCellW, cellW)
        while cellW > minCellW && rowsAt(cellW).count > maxRows {
            cellW -= 1
        }
        return (CGSize(width: cellW, height: cellW * 1.5), gap, rowsAt(cellW))
    }

    private var headlineHeight: CGFloat {
        // Reserve up to ~3 rows of headline; flap cells size to fit within.
        110
    }

    // MARK: - Scrubber + bulletin timers

    private var scrubberSection: some View {
        VStack(spacing: 8) {
            SegmentedScrubber(
                segments: player.allSegments,
                total: player.totalDurationSeconds,
                elapsed: player.playbackElapsedSeconds,
                ink: ink,
                onSeek: { frac in player.seekToFullFraction(frac) }
            )
            .frame(height: 6)

            HStack {
                Text("\(timeString(bulletinElapsedSeconds)) PLAYED")
                    .font(.label(11)).tracking(1.5)
                    .foregroundColor(inkMuted)
                Spacer()
                Text("\(timeString(bulletinLeftSeconds)) LEFT")
                    .font(.label(11)).tracking(1.5)
                    .foregroundColor(ink)
            }
        }
    }

    // MARK: - Transport

    private var transport: some View {
        HStack(spacing: 44) {
            transportButton(system: "backward.end.fill") { player.skipBack() }
            playPauseButton
            transportButton(system: "forward.end.fill") { player.skip() }
        }
    }

    private func transportButton(system: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: system)
                .font(.system(size: 22, weight: .medium))
                .foregroundColor(ink.opacity(0.8))
                .frame(width: 48, height: 48)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var playPauseButton: some View {
        let isPlaying = player.isPlaying   // single source of truth (matches lock screen)
        let diameter: CGFloat = 76
        return Button(action: { Haptics.play(); player.togglePlayPause() }) {
            ZStack {
                Circle()
                    .fill(RadialGradient(
                        colors: [BoardColors.topFlapTop, BoardColors.botFlapBottom],
                        center: .init(x: 0.5, y: 0.35), startRadius: 0, endRadius: diameter * 0.6))
                Circle()
                    .fill(LinearGradient(colors: [.white.opacity(0.08), .clear],
                                         startPoint: .top, endPoint: .center))
                Image(uiImage: BoardGrain.image)
                    .resizable(resizingMode: .tile).opacity(0.018).clipShape(Circle())
                Group {
                    if isPlaying {
                        Image(systemName: "pause.fill")
                            .font(.system(size: 26, weight: .medium))
                            .foregroundColor(BoardColors.character)
                    } else {
                        PlayTriangle()
                            .fill(BoardColors.character)
                            .frame(width: diameter * 0.26, height: diameter * 0.3)
                            .offset(x: diameter * 0.03)
                    }
                }
            }
            .frame(width: diameter, height: diameter)
            .shadow(color: .black.opacity(0.25), radius: 10, y: 5)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Running order (full interactive tracklist)

    /// The whole bulletin as a tappable list. The playing story lives IN the list
    /// (highlighted + animated), played stories are dimmed but still replayable,
    /// upcoming stories are normal. Tapping any row seeks to its start and plays.
    private var tracklist: some View {
        VStack(alignment: .leading, spacing: 0) {
            Rectangle().fill(ink.opacity(0.12)).frame(height: 1)
            Text("RUNNING ORDER")
                .font(.label(11)).tracking(2.5)
                .foregroundColor(inkMuted)
                .padding(.top, 14)
                .padding(.bottom, 4)
            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {
                    ForEach(Array(player.storyUnits.enumerated()), id: \.offset) { idx, unit in
                        trackRow(idx: idx, unit: unit)
                        if idx < player.storyUnits.count - 1 {
                            Rectangle().fill(ink.opacity(0.06)).frame(height: 1)
                                .padding(.horizontal, 10)
                        }
                    }
                }
                .padding(.bottom, 8)
            }
        }
        .frame(maxHeight: .infinity)
    }

    private func trackRow(idx: Int, unit: StoryUnit) -> some View {
        let isCurrent = idx == currentUnitIndexClamped
        let isPlayed = !isCurrent && (unit.storyHash.map { player.consumedStoryHashes.contains($0) } ?? false)
        // Grey = "you've heard this" (still tappable), NOT disabled.
        let titleColor = isPlayed ? ink.opacity(0.32) : ink
        let numColor = isCurrent ? ink : inkMuted
        return Button {
            player.playStoryUnit(at: idx)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Text(String(format: "%02d", idx + 1))
                    .font(.label(12)).tracking(1)
                    .foregroundColor(numColor)
                    .frame(width: 22, alignment: .leading)
                    .padding(.top, 3)
                Text(unit.title ?? "")
                    .font(.editorial(18))
                    .foregroundColor(titleColor)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if isCurrent {
                    EqualizerIndicator(color: ink)
                        .frame(width: 15, height: 13)
                        .padding(.top, 4)
                }
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 10)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(isCurrent ? ink.opacity(0.05) : Color.clear)  // subtle now-playing highlight
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: - Derived now-playing state (from BulletinPlayer)

    private var boardHeader: String {
        let n = currentStoryNumber
        let total = player.storyUnits.count
        let base = "NOW PLAYING — \(String(format: "%02d", n)) / \(String(format: "%02d", total))"
        // category degraded (Phase B) — appended automatically once exposed:
        return category.map { "\(base) · \($0.uppercased())" } ?? base
    }

    private var currentUnitIndexClamped: Int {
        guard !player.storyUnits.isEmpty else { return 0 }
        return min(max(0, player.currentUnitIndex), player.storyUnits.count - 1)
    }

    private var currentStoryNumber: Int { player.storyUnits.isEmpty ? 0 : currentUnitIndexClamped + 1 }

    private var currentHeadline: String {
        guard !player.storyUnits.isEmpty else { return "HEADLINES" }
        return player.storyUnits[currentUnitIndexClamped].title ?? "HEADLINES"
    }

    /// Phase-B degradation: not in the manifest yet.
    private var category: String? { nil }

    /// The current story's ranked outlets (credibility-ordered by the backend).
    private var sources: [String] {
        guard !player.storyUnits.isEmpty else { return [] }
        return player.storyUnits[currentUnitIndexClamped].storySegment.sources ?? []
    }

    // Full audio timeline (intro → stories → outro), so the timer counts from the very
    // first second of the greeting — not 0:00 until the first story.
    private var bulletinElapsedSeconds: Double { player.playbackElapsedSeconds }
    private var bulletinLeftSeconds: Double {
        max(0, player.totalDurationSeconds - player.playbackElapsedSeconds)
    }

    private func timeString(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded()))
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

// MARK: - Segmented scrubber

/// Progress over the FULL audio timeline: one cell per segment (intro, transitions,
/// stories, outro), so the fill advances continuously from the very first second — like a
/// podcast player — while still showing segment boundaries. Story cells read at full ink;
/// wrapper cells (intro/transition/outro) are dimmer so the stories still dominate.
private struct SegmentedScrubber: View {
    let segments: [ManifestSegment]
    let total: Double
    let elapsed: Double
    let ink: Color
    let onSeek: (Double) -> Void

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let gap: CGFloat = 2
            let count = max(segments.count, 1)
            let usable = w - gap * CGFloat(count - 1)
            HStack(spacing: gap) {
                ForEach(Array(segments.enumerated()), id: \.offset) { i, seg in
                    let frac = total > 0 ? seg.durationSeconds / total : 1.0 / Double(count)
                    let segW = max(2, usable * CGFloat(frac))
                    // Cumulative start of this cell in the full timeline.
                    let start = segments.prefix(i).reduce(0.0) { $0 + $1.durationSeconds }
                    SegmentBar(start: start, duration: seg.durationSeconds,
                               isStory: seg.isStory, elapsed: elapsed, ink: ink)
                        .frame(width: segW)
                }
            }
            .frame(width: w, height: geo.size.height)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onEnded { value in
                        let frac = min(1, max(0, value.location.x / w))
                        onSeek(Double(frac))
                    }
            )
        }
    }

    private struct SegmentBar: View {
        let start: Double
        let duration: Double
        let isStory: Bool
        let elapsed: Double
        let ink: Color

        var body: some View {
            let dur = max(0.0001, duration)
            let progress = min(1, max(0, (elapsed - start) / dur))  // 0 not started … 1 done
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(ink.opacity(isStory ? 0.15 : 0.10))       // not-yet
                    Capsule().fill(ink.opacity(isStory ? 1.0 : 0.5))         // played portion
                        .frame(width: g.size.width * CGFloat(progress))
                }
            }
        }
    }
}

// MARK: - Equalizer indicator (now-playing row)

/// Small animated equalizer beside the currently-playing track — the bars
/// oscillate on a shared TimelineView clock (pure UI, no audio taps). Under
/// reduce-motion it collapses to a static `waveform` glyph.
private struct EqualizerIndicator: View {
    let color: Color
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private let bars = 4

    var body: some View {
        if reduceMotion {
            Image(systemName: "waveform")
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(color)
        } else {
            TimelineView(.animation) { ctx in
                let t = ctx.date.timeIntervalSinceReferenceDate
                GeometryReader { geo in
                    let barW = (geo.size.width - CGFloat(bars - 1) * 2) / CGFloat(bars)
                    HStack(alignment: .bottom, spacing: 2) {
                        ForEach(0..<bars, id: \.self) { i in
                            // Each bar on its own phase so they never move in lockstep.
                            let n = 0.30 + 0.70 * (0.5 + 0.5 * sin(t * 5.5 + Double(i) * 1.3))
                            Capsule()
                                .fill(color)
                                .frame(width: max(1.5, barW), height: max(2, geo.size.height * CGFloat(n)))
                        }
                    }
                    .frame(width: geo.size.width, height: geo.size.height, alignment: .bottom)
                }
            }
        }
    }
}

// MARK: - Play triangle

private struct PlayTriangle: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}

// MARK: - Generation loader Solari board

/// A ~4-row split-flap board that churns through random glyphs and settles on a
/// status line, reshuffling the whole board whenever `band` changes. Built from
/// the shared `FlapCell` / `BoardColors` tokens so it's identical material to the
/// playback board. Driven by one TimelineView clock — cell glyph is a pure
/// function of elapsed time since the last churn, so it's cheap and stateless.
/// Reduce-motion → the target text is shown immediately, no flicker.
private struct LoaderStatusBoard: View {
    let text: String
    let band: Int
    let reduceMotion: Bool

    private let rows = 4
    private let cols = 15
    private let gap: CGFloat = 3
    private let cellAspect: CGFloat = 1.4   // height / width

    @State private var churnStart: Date = Date()

    // One cosmetic status-line group per visible loader stage (LoaderTiming.visibleStages).
    // The first four are the evenly-paced stages across the expected window; the last is the
    // "almost ready" DWELL copy that holds until the briefing is ready. One line is picked
    // per group at load and held for the whole generation.
    static let loaderWordSets: [[String]] = [
        ["ON THE BEAT", "SCANNING THE WIRES", "WORKING THE SOURCES", "COMBING THE HEADLINES", "CHASING THE STORIES"],
        ["READING THE ROOM", "TAKING THE TEMPERATURE", "GAUGING THE MOOD", "SIZING IT UP", "WEIGHING IT UP"],
        ["CONNECTING THE DOTS", "JOINING THE THREADS", "SEPARATING THE NOISE", "FOLLOWING THE THREAD", "PIECING IT TOGETHER"],
        ["PUTTING PEN TO PAPER", "SETTING THE TYPE", "FINDING THE WORDS", "GOING TO PRESS", "INK IS FLOWING"],
        ["ALMOST READY", "NEARLY THERE", "FINAL CHECKS", "TIDYING UP", "ANY SECOND NOW"],
    ]
    /// Pick one line per band at load, held for the whole generation.
    static func pickWords() -> [String] { loaderWordSets.map { $0.randomElement() ?? "" } }

    private static let flicker = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&")

    var body: some View {
        let grid = Self.layout(text, rows: rows, cols: cols)
        GeometryReader { geo in
            let cW = (geo.size.width - CGFloat(cols - 1) * gap) / CGFloat(cols)
            let cH = (geo.size.height - CGFloat(rows - 1) * gap) / CGFloat(rows)
            let size = CGSize(width: cW, height: cH)
            TimelineView(.animation(minimumInterval: 0.055, paused: reduceMotion)) { ctx in
                let elapsed = reduceMotion ? 999.0 : ctx.date.timeIntervalSince(churnStart)
                VStack(spacing: gap) {
                    ForEach(0..<rows, id: \.self) { r in
                        HStack(spacing: gap) {
                            ForEach(0..<cols, id: \.self) { c in
                                FlapCell(glyph: Self.glyph(target: grid[r][c], col: c, elapsed: elapsed),
                                         size: size)
                            }
                        }
                    }
                }
            }
        }
        // Fixed aspect so the board fits the card width without a circular layout.
        .aspectRatio(CGFloat(cols) / (CGFloat(rows) * cellAspect), contentMode: .fit)
        .onAppear { churnStart = Date() }
        .onChange(of: band) { _ in churnStart = Date() }   // reshuffle whole board on stage change
    }

    /// Glyph for a cell at `elapsed` seconds into the churn. Every cell flickers
    /// (the whole board reshuffles) then settles on its target — staggered by
    /// column for a left→right ripple. `nil` target settles to a blank flap.
    private static func glyph(target: Character?, col: Int, elapsed: Double) -> Character? {
        let settle = 0.34 + Double(col) * 0.028   // ~0.34–0.73s, ripples left→right
        if elapsed >= settle { return target }
        let step = Int(elapsed / 0.055)
        let idx = abs((col &* 31) &+ (step &* 17) &+ 7) % flicker.count
        return flicker[idx]
    }

    /// Word-wrap `text` into ≤`cols`-wide lines (max `rows`), centred both ways.
    static func layout(_ text: String, rows: Int, cols: Int) -> [[Character?]] {
        var lines: [String] = []
        var cur = ""
        for word in text.split(separator: " ") {
            let w = String(word)
            if cur.isEmpty { cur = w }
            else if cur.count + 1 + w.count <= cols { cur += " " + w }
            else { lines.append(cur); cur = w }
        }
        if !cur.isEmpty { lines.append(cur) }
        lines = Array(lines.prefix(rows))

        var grid = Array(repeating: Array(repeating: Character?.none, count: cols), count: rows)
        let topPad = max(0, (rows - lines.count) / 2)
        for (i, ln) in lines.enumerated() {
            let r = topPad + i
            guard r < rows else { break }
            let chars = Array(ln.prefix(cols))
            let left = max(0, (cols - chars.count) / 2)
            for (j, ch) in chars.enumerated() where left + j < cols {
                grid[r][left + j] = ch
            }
        }
        return grid
    }
}
