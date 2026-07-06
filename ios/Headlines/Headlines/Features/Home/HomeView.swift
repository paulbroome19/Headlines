//
//  HomeView.swift
//  Headlines
//
//  Home as a newspaper FRONT PAGE on a dark Solari board. The light-register page
//  (LightColors.page) holds a large dark board tablet (the shared `boardCard`
//  material/relief); inside, the app's REAL flap tiles greet the reader and the day's
//  REAL top stories (for the user's selected categories) are set in the editorial serif.
//  A bottom action, on the light page, assembles the full audio briefing.
//
//  Uses ONLY the app's fonts (Font.board flap · Font.editorial serif · Font.label mono)
//  and palette (LightColors / BoardColors) — no placeholders.
//

import SwiftUI

// MARK: - HomeView

struct HomeView: View {

    var userName: String        = "PAUL"
    /// Clock for the greeting + dateline (injectable for previews).
    var now: Date               = Date()
    /// Real top-stories + briefing length for the profile's selected categories (nil while loading).
    var preview: HomePreview?   = nil
    /// When `preview` was last fetched — drives the pull-to-refresh "LAST UPDATED" stamp.
    var lastUpdated: Date?      = nil
    var onGenerate: () -> Void  = {}
    var onProfile:  () -> Void  = {}
    /// Pull-to-refresh: reload the real top stories.
    var onRefresh:  () async -> Void = {}

    private let pageMargin: CGFloat  = 20
    private let boardCorner: CGFloat = 18
    private let boardPad: CGFloat    = 22
    private let discDiam: CGFloat    = 60
    private let cellGap: CGFloat     = 3

    private var cream: Color { BoardColors.character }   // #EDEDEA lit type

    // MARK: Body

    var body: some View {
        GeometryReader { geo in
            let boardInnerW = geo.size.width - pageMargin * 2 - boardPad * 2
            let cellSz = greetingCellSize(boardInnerW: boardInnerW)

            ZStack {
                LightColors.page.ignoresSafeArea()

                VStack(spacing: 0) {
                    lightTopBar

                    ScrollView {
                        VStack(spacing: 0) {
                            lastUpdatedStamp
                            boardTablet(cellSz: cellSz)
                                .padding(.bottom, 10)
                        }
                        .padding(.horizontal, pageMargin)
                    }
                    .refreshable { await onRefresh() }

                    assembleBar
                        .padding(.horizontal, pageMargin)
                        .padding(.top, 8)
                        .padding(.bottom, 6)
                }
            }
        }
    }

    // MARK: - Light-page top bar (settings)

    private var lightTopBar: some View {
        HStack {
            Spacer()
            settingsButton
        }
        .padding(.horizontal, pageMargin)
        .padding(.top, 8)
        .padding(.bottom, 4)
    }

    /// Three balanced, equal-length horizontal lines → the settings / filters screen.
    private var settingsButton: some View {
        Button(action: onProfile) {
            VStack(alignment: .trailing, spacing: 5) {
                ForEach(0..<3, id: \.self) { _ in
                    Capsule().fill(LightColors.ink).frame(width: 22, height: 1.6)
                }
            }
            .frame(width: 44, height: 44, alignment: .trailing)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Settings")
        #if DEBUG
        .simultaneousGesture(LongPressGesture(minimumDuration: 0.8).onEnded { _ in debugResetFirstRun() })
        #endif
    }

    // MARK: - Pull-to-refresh stamp (revealed at the top of the scroll)

    private var lastUpdatedStamp: some View {
        Text(lastUpdated.map { "LAST UPDATED \(timeStamp($0))" } ?? " ")
            .font(.label(10)).tracking(2)
            .foregroundColor(LightColors.ink.opacity(0.35))
            .frame(maxWidth: .infinity)
            .padding(.top, 4)
            .padding(.bottom, 10)
    }

    // MARK: - The dark board tablet (the front page)

    private func boardTablet(cellSz: CGSize) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            // Personalised greeting on the app's REAL flap tiles (flick + settle on entry).
            SolariGreeting(rows: greetingRows, cellSz: cellSz, gap: cellGap)

            Spacer().frame(height: 14)
            Text(datelineText())
                .font(.label(11)).tracking(2.5)
                .foregroundColor(cream.opacity(0.5))

            Spacer().frame(height: 18)
            creamRule
            Spacer().frame(height: 16)

            Text("YOUR TOP STORIES")
                .font(.label(11)).tracking(2.5)
                .foregroundColor(cream.opacity(0.5))
            Spacer().frame(height: 16)

            if let stories = preview?.stories, !stories.isEmpty {
                storyBlock(stories[0], lead: true)
                ForEach(Array(stories.dropFirst().prefix(3))) { story in
                    Spacer().frame(height: 18)
                    creamRule
                    Spacer().frame(height: 18)
                    storyBlock(story, lead: false)
                }
            } else {
                // Quiet, on-brand placeholder while the real top stories fetch — faint
                // cream hairline bars, laid out where the stories will land, with a slow
                // highlight sweeping across. No spinner; matches the dark board's calm.
                TopStoriesShimmer(cream: cream)
                    .padding(.vertical, 6)
            }
        }
        .padding(boardPad)
        .frame(maxWidth: .infinity, alignment: .leading)
        .boardCard(cornerRadius: boardCorner)
    }

    /// One front-page story: editorial serif headline, a standfirst (lead only), sources.
    private func storyBlock(_ story: HomeStory, lead: Bool) -> some View {
        VStack(alignment: .leading, spacing: lead ? 10 : 7) {
            Text(story.headline)
                .font(.editorial(lead ? 25 : 18))
                .foregroundColor(cream)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)

            if lead, let sf = story.standfirst, !sf.isEmpty {
                Text(sf)
                    .font(.editorial(14))
                    .foregroundColor(cream.opacity(0.68))
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if !story.sources.isEmpty {
                Text(story.sources.joined(separator: " · ").uppercased())
                    .font(.label(10)).tracking(1.5)
                    .foregroundColor(cream.opacity(0.5))
                    .lineLimit(1).minimumScaleFactor(0.75)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var creamRule: some View {
        Rectangle().fill(cream.opacity(0.12)).frame(height: 1)
    }

    // MARK: - Bottom action (light page, separate from the board)

    private var assembleBar: some View {
        VStack(spacing: 12) {
            greyRule

            VStack(spacing: 8) {
                // Meta first — "6 MIN · 5 STORIES" sits ABOVE the label (time first,
                // whole minutes, story count from the REAL selection; nil → placeholder).
                Text(metaLine)
                    .font(.label(11)).tracking(2.5)
                    .foregroundColor(LightColors.ink.opacity(0.5))

                // Centred mono label — the same register as "YOUR TOP STORIES" — with
                // the machined disc inline on the right. A disc-width spacer on the left
                // keeps the label optically centred in the bar and clear of the disc.
                HStack(spacing: 0) {
                    Color.clear.frame(width: discDiam, height: 1)
                    Text("ASSEMBLE YOUR BRIEFING")
                        .font(.label(13)).tracking(2.5)
                        .foregroundColor(LightColors.ink)
                        .frame(maxWidth: .infinity)
                        .lineLimit(1).minimumScaleFactor(0.7)
                    assembleDisc
                }
            }
        }
    }

    /// The machined "go" disc — the briefing assembly action.
    private var assembleDisc: some View {
        Button(action: onGenerate) {
            MachinedDisc(diameter: discDiam) {
                ForwardArrow()
                    .stroke(cream, style: StrokeStyle(lineWidth: discDiam * 0.06,
                                                      lineCap: .round, lineJoin: .round))
                    .frame(width: discDiam * 0.40, height: discDiam * 0.34)
                    .offset(x: discDiam * 0.02)
            }
        }
        .buttonStyle(MachinedDiscButtonStyle())
        .frame(width: discDiam, height: discDiam)
        .accessibilityLabel("Assemble your briefing")
    }

    private var greyRule: some View {
        Rectangle().fill(LightColors.ink.opacity(0.14)).frame(height: 1)
    }

    private var metaLine: String {
        guard let p = preview, p.storyCount > 0 else { return "·  ·  ·" }
        let stories = p.storyCount == 1 ? "STORY" : "STORIES"
        return "\(p.totalMinutes) MIN · \(p.storyCount) \(stories)"
    }

    // MARK: - Helpers

    /// Greeting rows: "GOOD MORNING," / "PAUL" (one word per flap line, comma after greeting).
    private var greetingRows: [String] {
        var g = TimeOfDay.current(now).greeting.split(separator: " ").map(String.init)
        let n = userName.uppercased().split(separator: " ").map(String.init)
        if !g.isEmpty && !n.isEmpty { g[g.count - 1] += "," }
        return g + n
    }

    private func greetingCellSize(boardInnerW: CGFloat) -> CGSize {
        let fitted = greetingRows.map { fitCellW($0, boardW: boardInnerW, gap: cellGap) }.min() ?? 26
        let cellW = max(18, min(26, fitted))   // header scale (not the old full-screen hero)
        return CGSize(width: cellW, height: cellW * 1.5)
    }

    private func datelineText() -> String {
        let f = DateFormatter()
        f.dateFormat = "EEEE d MMMM"
        return f.string(from: now).uppercased()
    }

    private func timeStamp(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "h:mma"          // 9:12AM
        return f.string(from: date).uppercased()
    }

    /// Rendered width of a string in "cell units": each glyph is one cell, each space half.
    private func widthUnits(_ s: String) -> (units: CGFloat, count: Int) {
        let chars = Array(s)
        let spaces = chars.filter { $0 == " " }.count
        return (CGFloat(chars.count - spaces) + 0.5 * CGFloat(spaces), chars.count)
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

// MARK: - Top-stories placeholder (quiet shimmer)

/// A quiet, on-brand stand-in while the real top stories fetch: faint cream hairline
/// bars laid out where the stories will land — a lead headline line, a couple of body
/// lines, a short source line — with a slow highlight sweeping across. No spinner.
/// Reduce-motion shows the static bars (no sweep).
private struct TopStoriesShimmer: View {
    let cream: Color
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var travel: CGFloat = 0

    // Relative bar widths → lead headline, two body lines, a short source line.
    private let widths: [CGFloat] = [0.92, 0.66, 0.80, 0.46]
    private let spacing: CGFloat = 13
    private func height(_ i: Int) -> CGFloat { i == 0 ? 18 : 11 }

    private var blockHeight: CGFloat {
        widths.indices.reduce(0) { $0 + height($1) } + spacing * CGFloat(widths.count - 1)
    }

    var body: some View {
        GeometryReader { geo in
            let W = geo.size.width
            bars(W: W, opacity: 0.10)
                .overlay(highlight(W: W))
                .mask(bars(W: W, opacity: 1))          // opaque shapes → clip the sweep to the bars
                .onAppear {
                    guard !reduceMotion else { return }
                    travel = -W * 0.6
                    withAnimation(.linear(duration: 2.1).repeatForever(autoreverses: false)) {
                        travel = W
                    }
                }
        }
        .frame(height: blockHeight)
    }

    private func bars(W: CGFloat, opacity: Double) -> some View {
        VStack(alignment: .leading, spacing: spacing) {
            ForEach(widths.indices, id: \.self) { i in
                Capsule()
                    .fill(cream.opacity(opacity))
                    .frame(width: W * widths[i], height: height(i))
            }
        }
        .frame(width: W, alignment: .leading)
    }

    @ViewBuilder
    private func highlight(W: CGFloat) -> some View {
        if !reduceMotion {
            LinearGradient(colors: [.clear, cream.opacity(0.16), .clear],
                           startPoint: .leading, endPoint: .trailing)
                .frame(width: W * 0.6)
                .offset(x: travel)
                .allowsHitTesting(false)
        }
    }
}

// MARK: - Solari greeting (flip-open on appear)

/// The greeting rendered on split-flap cells that "flip open" once when Home appears:
/// every cell churns random glyphs then settles on its target in a top→bottom,
/// left→right ripple — the same Solari aesthetic as the loader board. Bounded (~0.9s):
/// after it settles we swap to a static render so Home isn't redrawing every frame.
/// Reduce-motion shows the final greeting immediately.
private struct SolariGreeting: View {
    let rows: [String]
    let cellSz: CGSize
    let gap: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var start = Date()
    @State private var settled = false

    private static let flick = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    private static let stepInterval = 0.05
    private func settleAt(row: Int, col: Int) -> Double {
        0.18 + Double(row) * 0.11 + Double(col) * 0.03
    }
    private var totalSettle: Double {
        let maxCols = rows.map { $0.count }.max() ?? 0
        return settleAt(row: max(0, rows.count - 1), col: max(0, maxCols - 1)) + 0.12
    }

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

private func atHour(_ hour: Int) -> Date {
    var c = Calendar.current.dateComponents([.year, .month, .day], from: Date())
    c.hour = hour; c.minute = 12
    return Calendar.current.date(from: c) ?? Date()
}

private let samplePreview = HomePreview(
    profileId: 1, name: "Paul", storyCount: 5, totalMinutes: 6,
    stories: [
        HomeStory(storyId: "1", headline: "German chancellor pushes back at NATO summit over record defence spending",
                  category: "politics.europe", standfirst: "Friedrich Merz signalled resistance to President Trump's demands as leaders gathered in The Hague.",
                  sources: ["BBC", "The Guardian", "AP News"]),
        HomeStory(storyId: "2", headline: "Royal Surrey NHS Trust recognised for fast cancer diagnosis",
                  category: "health", standfirst: nil, sources: ["BBC", "Daily Express"]),
        HomeStory(storyId: "3", headline: "Taylor Swift and Travis Kelce marry in Madison Square Garden ceremony",
                  category: "culture.celebrity", standfirst: nil, sources: ["The New York Times", "BBC"]),
        HomeStory(storyId: "4", headline: "Mallory McMorrow suspends her Michigan Senate campaign",
                  category: "politics.us", standfirst: nil, sources: ["CBS News", "The Guardian"]),
    ]
)

#Preview("Morning · loaded") {
    HomeView(userName: "PAUL", now: atHour(9), preview: samplePreview, lastUpdated: atHour(9))
}

#Preview("Loading") {
    HomeView(userName: "PAUL", now: atHour(9))
}
