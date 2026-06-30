//
//  HomeView.swift
//  Headlines
//
//  The dark "board at rest" the user lands on between briefings.
//  Callbacks (onGenerate, onProfile) are stubbed — wire them up at the
//  call-site when the generate flow and settings are built.
//

import SwiftUI

// MARK: - Triangle shape for the play button

private struct Triangle: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to:    CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}

// MARK: - Play button style

private struct PlayButtonStyle: ButtonStyle {
    let diameter: CGFloat

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.94 : 1.0)
            .shadow(
                color: .black.opacity(configuration.isPressed ? 0.85 : 0.6),
                radius: configuration.isPressed ? 6 : 10,
                y: configuration.isPressed ? 2 : 5
            )
            .animation(.easeInOut(duration: 0.12), value: configuration.isPressed)
    }
}

// MARK: - HomeView

struct HomeView: View {

    var userName: String   = "PAUL"
    /// The clock used for the time-of-day greeting. Defaults to now; injectable
    /// for previews/tests. Computed on appear — fine that it doesn't live-update
    /// if the clock crosses a band boundary while Home is open.
    var now: Date = Date()
    var onGenerate: () -> Void = {}
    var onProfile:  () -> Void = {}

    @AppStorage("hasSeenHomeHint") private var hasSeenHomeHint = false
    /// Local flag so the hint stays visible for the whole first session
    /// even though we set the AppStorage flag in onAppear.
    @State private var showHint = false

    // MARK: Body

    var body: some View {
        GeometryReader { geo in
            // Kept in the safe area so the insets are real. We derive the true
            // full-screen height from them; the seam is expressed in these
            // safe-area coordinates so every layer lands on the same line.
            let insets = geo.safeAreaInsets
            let w = geo.size.width
            let h = geo.size.height                          // safe-area height
            let fullH = h + insets.top + insets.bottom       // true screen height

            // The whole screen is ONE flap tile. The hinge seam sits LOWER than
            // centre (61% of screen height) so the lighter top half is bigger,
            // giving the greeting room. `seamScreenY` is in true full-screen
            // coordinates; `seamY` is the same line in this safe-area space.
            // The two-tone split, the seam line and the play button all live on
            // this one line.
            let seamFraction: CGFloat = 0.61
            let seamScreenY = fullH * seamFraction
            let seamY = seamScreenY - insets.top

            let pinW: CGFloat = 14
            let pinH: CGFloat = 6
            let playDiam: CGFloat = min(w * 0.18, 72)

            // Greeting sizing — auto-size the flap cells to the longest line so
            // the greeting fills the top half confidently but stays premium. We
            // pick the largest cell size (between min and max) at which every
            // unbreakable line fits; a long name wraps across rows rather than
            // shrinking the whole greeting below the floor.
            let line1 = TimeOfDay.current(now).greeting + ","   // e.g. "GOOD AFTERNOON,"
            let nameUpper = userName.uppercased()               // board world is caps
            let boardW  = w * 0.96
            let cellGap: CGFloat = 3
            let maxCellW: CGFloat = 38                          // premium cap — never enormous
            let minCellW: CGFloat = 18                          // floor before the name wraps

            let layout = greetingLayout(greeting: line1, name: nameUpper,
                                        boardW: boardW, gap: cellGap,
                                        minCellW: minCellW, maxCellW: maxCellW)
            let cellW = layout.cellW
            let nameRows = layout.nameRows
            let cellSz = CGSize(width: cellW, height: cellW * 1.5)

            ZStack {

                // ── Hinge seam line + notch pins — on the centre line, the same
                //    y as the tone-change and the play-button centre.
                ZStack {
                    Rectangle()
                        .fill(BoardColors.seam)
                        .frame(height: 1.5)

                    HStack {
                        Capsule()
                            .fill(BoardColors.seam)
                            .frame(width: pinW, height: pinH)
                            .padding(.leading, 20)
                        Spacer()
                        Capsule()
                            .fill(BoardColors.seam)
                            .frame(width: pinW, height: pinH)
                            .padding(.trailing, 20)
                    }
                }
                .frame(width: w)
                .position(x: w / 2, y: seamY)

                // ── Greeting + dateline — TOP half, vertically centred between
                //    the status bar (safe-area top, y = 0 here) and the seam,
                //    left-aligned.
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: cellGap) {
                        flapRow(text: line1, cellSz: cellSz, gap: cellGap)   // greeting word
                        ForEach(Array(nameRows.enumerated()), id: \.offset) { _, row in
                            flapRow(text: row, cellSz: cellSz, gap: cellGap) // name (may wrap)
                        }
                    }
                    Spacer().frame(height: 16)
                    Text(datelineText())
                        .font(.board(11))
                        .foregroundColor(BoardColors.character.opacity(0.55))
                        .tracking(2.5)
                        .padding(.leading, 2)
                }
                .frame(width: boardW, alignment: .leading)
                .position(x: w / 2, y: seamY / 2)

                // ── Play button — centred exactly on the seam (screen centre),
                //    straddling the fold like the H in the app icon.
                playButton(diameter: playDiam)
                    .position(x: w / 2, y: seamY)

                // ── First-run hint — BOTTOM half, just below the button.
                if showHint {
                    Text("PRESS PLAY TO GENERATE YOUR BRIEFING")
                        .font(.board(9))
                        .foregroundColor(BoardColors.character.opacity(0.35))
                        .tracking(1.8)
                        .multilineTextAlignment(.center)
                        .fixedSize()
                        .position(x: w / 2, y: seamY + playDiam * 0.5 + 26)
                }

                // ── Profile "P" — top-right, just below the status bar
                //    (≈ standard nav-bar height: a clear gap under the inset).
                profileButton()
                    .position(x: w - 20 - 17, y: 16 + 17)
            }
            .frame(width: w, height: h)
            // ── Two-tone board — ONE tile split at the centre seam. Drawn as a
            //    full-bleed background (kept OUT of the positioned ZStack so its
            //    safe-area bleed can't shift the seam/button) — it fills edge to
            //    edge top to bottom with the split at the true screen centre,
            //    coinciding with `seamY`. No third zone, no bottom band.
            .background {
                ZStack {
                    VStack(spacing: 0) {
                        BoardColors.panelTopGradient
                            .frame(height: seamScreenY)
                        LinearGradient(
                            colors: [BoardColors.botFlapTop, BoardColors.botFlapBottom],
                            startPoint: .top, endPoint: .bottom)
                        .frame(height: fullH - seamScreenY)
                    }
                    Image(uiImage: BoardGrain.image)
                        .resizable(resizingMode: .tile)
                        .opacity(0.012)
                        .allowsHitTesting(false)
                }
                .frame(height: fullH)
                .ignoresSafeArea()
            }
        }
        .background(BoardColors.background.ignoresSafeArea())
        .onAppear {
            // Mark as seen immediately (persisted) so the hint never shows
            // again on a future launch; use local showHint so it stays
            // visible for the rest of this session.
            if !hasSeenHomeHint {
                showHint = true
                hasSeenHomeHint = true
            }
        }
    }

    // MARK: - Helpers

    /// A horizontal row of FlapCells, one per non-space character.
    @ViewBuilder
    private func flapRow(text: String, cellSz: CGSize, gap: CGFloat) -> some View {
        HStack(spacing: gap) {
            ForEach(Array(text.enumerated()), id: \.offset) { _, ch in
                if ch == " " {
                    // Render a gap of the same width as a cell for readability.
                    Color.clear.frame(width: cellSz.width * 0.5, height: cellSz.height)
                } else {
                    FlapCell(glyph: ch, size: cellSz)
                }
            }
        }
    }

    private func datelineText() -> String {
        let f = DateFormatter()
        f.dateFormat = "EEEE d MMMM"
        return f.string(from: Date()).uppercased()
    }

    // MARK: - Greeting auto-sizing

    /// Rendered width of a string in "cell units": each glyph is one cell, each
    /// space is half a cell — matching `flapRow`. Returns the unit total and the
    /// item count (for inter-item gaps).
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

    /// Pick the cell size and name layout: the largest size (between min and
    /// max) at which the greeting word and the whole name each fit one line;
    /// if the name can't fit at the floor, size by the greeting + longest single
    /// word and wrap the name across rows.
    private func greetingLayout(greeting: String, name: String,
                                boardW: CGFloat, gap: CGFloat,
                                minCellW: CGFloat, maxCellW: CGFloat) -> (cellW: CGFloat, nameRows: [String]) {
        let oneLineCellW = min(fitCellW(greeting, boardW: boardW, gap: gap),
                               fitCellW(name, boardW: boardW, gap: gap))
        if oneLineCellW >= minCellW {
            return (min(maxCellW, oneLineCellW), [name])
        }
        // Long name: a single word can't break, so size by the greeting word +
        // the longest name word, then wrap the name across rows.
        let longestWordFit = name.split(separator: " ")
            .map { fitCellW(String($0), boardW: boardW, gap: gap) }.min() ?? maxCellW
        let cellW = max(minCellW, min(maxCellW, min(fitCellW(greeting, boardW: boardW, gap: gap), longestWordFit)))
        return (cellW, wrapName(name, cellW: cellW, boardW: boardW, gap: gap))
    }

    /// Pack the name's words into as few rows as fit `boardW` at `cellW`, so a
    /// long name uses the vertical room instead of shrinking the whole greeting.
    private func wrapName(_ name: String, cellW: CGFloat, boardW: CGFloat, gap: CGFloat) -> [String] {
        let words = name.split(separator: " ").map(String.init)
        guard !words.isEmpty else { return [name] }
        func rowWidth(_ s: String) -> CGFloat {
            let (u, c) = widthUnits(s)
            return u * cellW + gap * CGFloat(max(0, c - 1))
        }
        var rows: [String] = []
        var current = ""
        for word in words {
            let candidate = current.isEmpty ? word : current + " " + word
            if current.isEmpty || rowWidth(candidate) <= boardW {
                current = candidate
            } else {
                rows.append(current)
                current = word
            }
        }
        if !current.isEmpty { rows.append(current) }
        return rows
    }

    // MARK: - Play button

    @ViewBuilder
    private func playButton(diameter: CGFloat) -> some View {
        Button(action: onGenerate) {
            ZStack {
                // Board-material circle background
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [BoardColors.topFlapTop, BoardColors.botFlapBottom],
                            center: .init(x: 0.5, y: 0.35),
                            startRadius: 0,
                            endRadius: diameter * 0.6
                        )
                    )
                    .frame(width: diameter, height: diameter)

                // Subtle top-edge highlight (raised feel)
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [.white.opacity(0.08), .clear],
                            startPoint: .top, endPoint: .center
                        )
                    )
                    .frame(width: diameter, height: diameter)

                // Grain on the button face
                Image(uiImage: BoardGrain.image)
                    .resizable(resizingMode: .tile)
                    .opacity(0.018)
                    .clipShape(Circle())
                    .frame(width: diameter, height: diameter)

                // Play triangle — bone off-white, slightly right of centre for
                // optical balance.
                Triangle()
                    .fill(BoardColors.character)
                    .frame(width: diameter * 0.28, height: diameter * 0.32)
                    .offset(x: diameter * 0.03)
            }
        }
        .buttonStyle(PlayButtonStyle(diameter: diameter))
        .frame(width: diameter, height: diameter)
    }

    // MARK: - Profile button

    @ViewBuilder
    private func profileButton() -> some View {
        let initial = String(userName.prefix(1)).uppercased()
        Button(action: onProfile) {
            ZStack {
                Circle()
                    .strokeBorder(BoardColors.character.opacity(0.45), lineWidth: 1.2)
                    .frame(width: 34, height: 34)
                Text(initial)
                    .font(.board(14))
                    .foregroundColor(BoardColors.character)
            }
        }
        #if DEBUG
        // DEBUG-only: long-press the P to re-trigger the first-run state for
        // testing (clears the persisted flags + re-shows the hint live). The
        // normal tap action (onProfile) is unaffected. Production gating is
        // unchanged — real users still see the hint first-run only.
        .simultaneousGesture(
            LongPressGesture(minimumDuration: 0.8).onEnded { _ in debugResetFirstRun() }
        )
        #endif
    }

    #if DEBUG
    private func debugResetFirstRun() {
        let d = UserDefaults.standard
        d.removeObject(forKey: "userName")
        d.removeObject(forKey: "didOnboard")
        d.removeObject(forKey: "hasSeenHomeHint")
        hasSeenHomeHint = false
        withAnimation { showHint = true }   // re-show immediately, no relaunch
    }
    #endif
}

// MARK: - Preview

/// Builds a date at a fixed hour today (local) to demonstrate a greeting band.
private func atHour(_ hour: Int) -> Date {
    var c = Calendar.current.dateComponents([.year, .month, .day], from: Date())
    c.hour = hour; c.minute = 0
    return Calendar.current.date(from: c) ?? Date()
}

#Preview("Now") {
    HomeView()
}

#Preview("2pm · Afternoon") {
    HomeView(now: atHour(14))
}

#Preview("8pm · Evening") {
    HomeView(now: atHour(20))
}

#Preview("2am · Night") {
    HomeView(now: atHour(2))
}
