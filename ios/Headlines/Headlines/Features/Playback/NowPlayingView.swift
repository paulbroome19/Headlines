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

    var body: some View {
        ZStack {
            pageBG.ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                Group {
                    switch player.playerState {
                    case .idle, .loadingManifest:
                        loadingState
                    case .preparing:
                        preparingState
                    case .failed(let msg):
                        failedState(msg)
                    default:
                        playerBody
                    }
                }
            }
            .padding(.horizontal, 20)
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

    private var loadingState: some View {
        VStack(spacing: 14) {
            Spacer()
            ProgressView().tint(ink.opacity(0.5))
            Text("PREPARING YOUR BRIEFING")
                .font(.label(12)).tracking(2.5)
                .foregroundColor(inkMuted)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// Readiness-gated loader: a determinate bar driven by `player.loadProgress`
    /// (smooth, asymptotic — never full until audio starts). Generic UI for now;
    /// the mechanism is what matters. The `.animation` smooths the ~20fps updates
    /// so the fill reads as continuous and never "stuck".
    private var preparingState: some View {
        VStack(spacing: 16) {
            Spacer()
            Text("PREPARING YOUR BRIEFING")
                .font(.label(12)).tracking(2.5)
                .foregroundColor(inkMuted)
            ProgressView(value: player.loadProgress)
                .progressViewStyle(.linear)
                .tint(ink)
                .frame(maxWidth: 240)
                .animation(.easeOut(duration: 0.15), value: player.loadProgress)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func failedState(_ msg: String) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Text("COULDN'T LOAD BRIEFING")
                .font(.label(13)).tracking(2)
                .foregroundColor(ink)
            Text(msg)
                .font(.label(11))
                .foregroundColor(inkMuted)
                .multilineTextAlignment(.center)
            Button(action: onRetry) {
                Text("TRY AGAIN")
                    .font(.label(13)).tracking(2)
                    .foregroundColor(ink)
                    .padding(.horizontal, 22).padding(.vertical, 11)
                    .overlay(Capsule().strokeBorder(ink.opacity(0.3), lineWidth: 1))
            }
            .buttonStyle(.plain)
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
            upNext
            Spacer(minLength: 8)
        }
    }

    // MARK: - The dark instrument (embedded flap board)

    private var nowPlayingBoard: some View {
        VStack(spacing: 0) {
            // Header strip
            HStack(spacing: 8) {
                Circle()
                    .fill(BoardColors.character.opacity(player.playerState == .playing ? 0.9 : 0.4))
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

            // Bottom flap: SOURCES (left, degraded→hidden) | STORY LEFT (right)
            HStack(alignment: .top) {
                if !sources.isEmpty {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("SOURCES")
                            .font(.label(9)).tracking(2)
                            .foregroundColor(BoardColors.character.opacity(0.4))
                        Text(sources.prefix(3).joined(separator: " · "))
                            .font(.board(13))
                            .foregroundColor(BoardColors.character.opacity(0.85))
                    }
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 5) {
                    Text("STORY LEFT")
                        .font(.label(9)).tracking(2)
                        .foregroundColor(BoardColors.character.opacity(0.4))
                    Text(timeString(storyLeftSeconds))
                        .font(.board(15))
                        .foregroundColor(BoardColors.character)
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 14)
            .padding(.bottom, 18)
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
                units: player.storyUnits,
                total: player.totalStoryDurationSeconds,
                elapsed: bulletinElapsedSeconds,
                ink: ink,
                onSeek: { frac in player.seekToGlobalFraction(frac) }
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
        let isPlaying = player.playerState == .playing || player.playerState == .stalled
        let diameter: CGFloat = 76
        return Button(action: { player.togglePlayPause() }) {
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

    // MARK: - Up Next

    @ViewBuilder
    private var upNext: some View {
        if let next = upNextStory {
            VStack(alignment: .leading, spacing: 10) {
                Rectangle().fill(ink.opacity(0.12)).frame(height: 1)
                Text("UP NEXT")
                    .font(.label(11)).tracking(2.5)
                    .foregroundColor(inkMuted)
                Text("\(String(format: "%02d", next.number)) — \(next.title)")
                    .font(.editorial(19))
                    .foregroundColor(ink)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else {
            // Last story — keep the rule for visual stability.
            Rectangle().fill(ink.opacity(0.12)).frame(height: 1)
        }
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
    private var sources: [String] { [] }

    private var bulletinElapsedSeconds: Double {
        player.globalPositionPct * player.totalStoryDurationSeconds
    }
    private var bulletinLeftSeconds: Double {
        max(0, player.totalStoryDurationSeconds - bulletinElapsedSeconds)
    }
    private var storyLeftSeconds: Double {
        guard !player.storyUnits.isEmpty else { return 0 }
        let unit = player.storyUnits[currentUnitIndexClamped]
        let unitEnd = unit.cumulativeStartSeconds + unit.totalDurationSeconds
        return max(0, unitEnd - bulletinElapsedSeconds)
    }
    private var upNextStory: (number: Int, title: String)? {
        let next = currentUnitIndexClamped + 1
        guard next < player.storyUnits.count else { return nil }
        return (next + 1, player.storyUnits[next].title ?? "")
    }

    private func timeString(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded()))
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

// MARK: - Segmented scrubber

private struct SegmentedScrubber: View {
    let units: [StoryUnit]
    let total: Double
    let elapsed: Double
    let ink: Color
    let onSeek: (Double) -> Void

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let gap: CGFloat = 3
            let count = max(units.count, 1)
            let usable = w - gap * CGFloat(count - 1)
            HStack(spacing: gap) {
                ForEach(Array(units.enumerated()), id: \.offset) { _, unit in
                    let frac = total > 0 ? unit.totalDurationSeconds / total : 1.0 / Double(count)
                    let segW = max(2, usable * CGFloat(frac))
                    SegmentBar(unit: unit, elapsed: elapsed, ink: ink)
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
        let unit: StoryUnit
        let elapsed: Double
        let ink: Color

        var body: some View {
            let start = unit.cumulativeStartSeconds
            let dur = max(0.0001, unit.totalDurationSeconds)
            let progress = min(1, max(0, (elapsed - start) / dur))  // 0 not started … 1 done
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(ink.opacity(0.15))                       // not-yet
                    Capsule().fill(ink)                                     // played portion
                        .frame(width: g.size.width * CGFloat(progress))
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
