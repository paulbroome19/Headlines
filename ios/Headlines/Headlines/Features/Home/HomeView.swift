//
//  HomeView.swift
//  Headlines
//
//  Home in the locked LIGHT register (C6): a near-white page with the dark
//  flap-board greeting as a contained hero instrument, the machined forward
//  arrow docked into its base (the generate action), and a quiet masthead
//  (HEADLINES · refresh · profile). Matches onboarding + playback exactly — one
//  product. The arrow keeps the existing generate→play wiring; the "P" opens the
//  settings filters (wired at the call-site in HomeContainerView).
//

import SwiftUI

// The flap material is the shared `FlapCell` / `boardCard` (Board.swift); the
// machined disc + its depth are the shared `MachinedDisc` /
// `MachinedDiscButtonStyle` (MachinedDisc.swift) — identical to onboarding.

// MARK: - HomeView

struct HomeView: View {

    var userName: String   = "PAUL"
    /// The clock used for the time-of-day greeting. Defaults to now; injectable
    /// for previews/tests. Computed on appear — fine that it doesn't live-update
    /// if the clock crosses a band boundary while Home is open.
    var now: Date = Date()
    var onGenerate: () -> Void = {}
    var onProfile:  () -> Void = {}
    /// Refresh = regenerate a fresh briefing, discarding already-heard stories.
    /// Behaviour is wired later; quiet/secondary affordance for now.
    var onRefresh:  () -> Void = {}

    // Layout constants — shared margins/disc size so Home reads as the same
    // product as onboarding/playback.
    private let pageMargin: CGFloat   = BottomActionBar.pageMargin
    private let cardInnerPad: CGFloat = 24
    private let cardCorner: CGFloat   = 18
    private let arrowDiam: CGFloat    = 64    // the hero control
    private let constantGap: CGFloat  = 24    // greeting → docked button (CONSTANT)
    private let cellGap: CGFloat      = 3
    private let maxCellW: CGFloat     = 40
    private let minCellW: CGFloat     = 18

    // MARK: Body

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let cardW  = w - pageMargin * 2
            let innerW = cardW - cardInnerPad * 2

            // Greeting stacks ONE WORD PER LINE on flap cells (GOOD / MORNING /
            // PAUL …), big and confident; the cell size is the largest (capped)
            // at which the longest single word still fits the card. The card then
            // hugs however many lines that is — no reserved max height.
            let greetingWords = TimeOfDay.current(now).greeting
                .split(separator: " ").map(String.init)
            let nameWords = userName.uppercased()
                .split(separator: " ").map { String($0) }
            let rows = greetingWords + nameWords
            let cellW = max(minCellW, min(maxCellW,
                rows.map { fitCellW($0, boardW: innerW, gap: cellGap) }.min() ?? maxCellW))
            let cellSz = CGSize(width: cellW, height: cellW * 1.5)

            VStack(spacing: 0) {
                masthead

                Spacer()

                heroBlock(rows: rows, cellSz: cellSz, cardW: cardW)

                Spacer()
                Spacer()   // bias the hero a touch above centre
            }
            .frame(width: w)
        }
        .background(LightColors.page.ignoresSafeArea())
    }

    // MARK: - Masthead

    private var masthead: some View {
        VStack(spacing: 14) {
            HStack(alignment: .center, spacing: 0) {
                Text("HEADLINES")
                    .font(.label(15))
                    .tracking(3)
                    .foregroundColor(LightColors.ink)

                Spacer()

                HStack(spacing: 14) {
                    // Refresh hidden until it does something (refreshBriefing is a
                    // stub) — a visible no-op button shouldn't ship. onRefresh +
                    // refreshBriefing() stay wired for when refresh lands.
                    profileButton
                }
            }
            .padding(.horizontal, pageMargin)
            .padding(.top, 6)

            // Inset hairline rule — most of the width, side margins.
            Rectangle()
                .fill(LightColors.ink.opacity(0.15))
                .frame(height: 1)
                .padding(.horizontal, pageMargin)
        }
    }

    private var profileButton: some View {
        let initial = String(userName.prefix(1)).uppercased()
        return Button(action: onProfile) {
            ZStack {
                Circle()
                    .strokeBorder(LightColors.ink.opacity(0.4), lineWidth: 1.2)
                    .frame(width: 34, height: 34)
                Text(initial)
                    .font(.label(14))
                    .foregroundColor(LightColors.ink)
            }
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Profile")
        #if DEBUG
        // DEBUG-only: long-press the P to clear first-run flags for testing.
        .simultaneousGesture(
            LongPressGesture(minimumDuration: 0.8).onEnded { _ in debugResetFirstRun() }
        )
        #endif
    }

    // MARK: - Hero (greeting card + docked arrow + caption)

    private func heroBlock(rows: [String], cellSz: CGSize, cardW: CGFloat) -> some View {
        VStack(spacing: 0) {
            greetingCard(rows: rows, cellSz: cellSz, cardW: cardW)
                // Dock the machined arrow centred on the card's BASE — half on the
                // card, half on the page, with its own soft depth (no ring).
                .overlay(alignment: .bottom) {
                    arrowButton.offset(y: arrowDiam / 2)
                }

            // Room for the arrow's lower half, then the caption beneath it.
            Spacer().frame(height: arrowDiam / 2 + 16)

            Text("ASSEMBLE YOUR BRIEFING")
                .font(.label(11))
                .tracking(2.5)
                .foregroundColor(LightColors.ink.opacity(0.45))
        }
        .frame(width: cardW)
    }

    private func greetingCard(rows: [String], cellSz: CGSize, cardW: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(datelineText())
                .font(.label(11))
                .tracking(2.5)
                .foregroundColor(BoardColors.character.opacity(0.5))

            Spacer().frame(height: 18)

            // Solari flip-open on app launch: the greeting churns split-flap glyphs
            // and settles in a top→bottom, left→right ripple (reduce-motion → static).
            SolariGreeting(rows: rows, cellSz: cellSz, gap: cellGap)
        }
        .padding(.horizontal, cardInnerPad)
        .padding(.top, cardInnerPad)
        // CONSTANT gap above the docked button: the button's upper half (radius)
        // plus a fixed gap — independent of how many greeting lines there are.
        .padding(.bottom, arrowDiam / 2 + constantGap)
        .frame(width: cardW, alignment: .leading)
        .boardCard(cornerRadius: cardCorner)
    }

    private var arrowButton: some View {
        Button(action: onGenerate) {
            MachinedDisc(diameter: arrowDiam) {
                ForwardArrow()
                    .stroke(BoardColors.character,
                            style: StrokeStyle(lineWidth: arrowDiam * 0.06,
                                               lineCap: .round, lineJoin: .round))
                    .frame(width: arrowDiam * 0.40, height: arrowDiam * 0.34)
                    .offset(x: arrowDiam * 0.02)
            }
        }
        .buttonStyle(MachinedDiscButtonStyle())
        .frame(width: arrowDiam, height: arrowDiam)
        .accessibilityLabel("Assemble your briefing")
    }

    // MARK: - Helpers

    private func datelineText() -> String {
        let f = DateFormatter()
        f.dateFormat = "EEEE d MMMM"
        return f.string(from: now).uppercased()
    }

    // MARK: - Greeting cell sizing

    /// Rendered width of a string in "cell units": each glyph is one cell, each
    /// space is half a cell — matching `flapRow`.
    private func widthUnits(_ s: String) -> (units: CGFloat, count: Int) {
        let chars = Array(s)
        let spaces = chars.filter { $0 == " " }.count
        let nonSpace = chars.count - spaces
        return (CGFloat(nonSpace) + 0.5 * CGFloat(spaces), chars.count)
    }

    /// The largest cell width at which `s` fits on one line within `boardW`.
    private func fitCellW(_ s: String, boardW: CGFloat, gap: CGFloat) -> CGFloat {
        let (u, c) = widthUnits(s)
        guard u > 0 else { return .greatestFiniteMagnitude }
        return (boardW - gap * CGFloat(max(0, c - 1))) / u
    }

    #if DEBUG
    private func debugResetFirstRun() {
        let d = UserDefaults.standard
        d.removeObject(forKey: "userName")
        d.removeObject(forKey: "didOnboard")
        d.removeObject(forKey: "hasSeenHomeHint")
    }
    #endif
}

// MARK: - Solari greeting (flip-open on launch)

/// The greeting rendered on split-flap cells that "flip open" once when Home
/// appears: every cell churns random glyphs then settles on its target in a
/// top→bottom, left→right ripple — the same Solari aesthetic as the loader board.
/// The animation is bounded (~0.9s): after it settles we swap to a static render
/// so the persistent Home screen isn't redrawing every frame. Reduce-motion shows
/// the final greeting immediately.
private struct SolariGreeting: View {
    let rows: [String]
    let cellSz: CGSize
    let gap: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var start = Date()
    @State private var settled = false

    private static let flick = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    private static let stepInterval = 0.05
    /// Per-cell settle time: base + row (top→bottom) + column (left→right) stagger.
    private func settleAt(row: Int, col: Int) -> Double {
        0.18 + Double(row) * 0.11 + Double(col) * 0.03
    }
    private var totalSettle: Double {
        let maxCols = rows.map { $0.count }.max() ?? 0
        return settleAt(row: max(0, rows.count - 1), col: max(0, maxCols - 1)) + 0.12
    }

    /// Glyph for a cell at `elapsed` seconds: flickers until its stagger point, then target.
    private func glyph(_ target: Character, row: Int, col: Int, elapsed: Double) -> Character {
        if elapsed >= settleAt(row: row, col: col) { return target }
        let step = Int(elapsed / Self.stepInterval)
        let idx = abs((row &* 53) &+ (col &* 31) &+ (step &* 17) &+ 7) % Self.flick.count
        return Self.flick[idx]
    }

    var body: some View {
        Group {
            if reduceMotion || settled {
                grid { _, _, ch in ch }
            } else {
                TimelineView(.animation) { ctx in
                    let elapsed = ctx.date.timeIntervalSince(start)
                    grid { row, col, ch in glyph(ch, row: row, col: col, elapsed: elapsed) }
                }
            }
        }
        .onAppear {
            start = Date()
            settled = false
            DispatchQueue.main.asyncAfter(deadline: .now() + totalSettle + 0.05) { settled = true }
        }
    }

    /// Lay the rows out as flap-cell lines, transforming each glyph via `transform`.
    private func grid(_ transform: @escaping (_ row: Int, _ col: Int, _ ch: Character) -> Character) -> some View {
        VStack(alignment: .leading, spacing: gap) {
            ForEach(Array(rows.enumerated()), id: \.offset) { r, word in
                HStack(spacing: gap) {
                    ForEach(Array(word.enumerated()), id: \.offset) { c, ch in
                        if ch == " " {
                            Color.clear.frame(width: cellSz.width * 0.5, height: cellSz.height)
                        } else {
                            FlapCell(glyph: transform(r, c, ch), size: cellSz)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Preview

/// Builds a date at a fixed hour today (local) to demonstrate a greeting band.
private func atHour(_ hour: Int) -> Date {
    var c = Calendar.current.dateComponents([.year, .month, .day], from: Date())
    c.hour = hour; c.minute = 0
    return Calendar.current.date(from: c) ?? Date()
}

#Preview("Morning · PAUL") {
    HomeView(userName: "PAUL", now: atHour(9))
}

#Preview("Afternoon · ISABELLA INDIA") {
    HomeView(userName: "ISABELLA INDIA", now: atHour(14))
}

#Preview("Evening · PAUL") {
    HomeView(userName: "PAUL", now: atHour(20))
}
