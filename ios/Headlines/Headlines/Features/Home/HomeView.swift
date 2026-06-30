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

            // The whole screen is ONE flap tile: the hinge seam is the exact
            // vertical CENTRE of the screen. In safe-area coordinates that point
            // is fullH/2 measured from the true top, i.e. shifted up by the top
            // inset. The two-tone split, the seam line and the play button are
            // all drawn on this one line.
            let seamY: CGFloat = fullH / 2 - insets.top

            let pinW: CGFloat = 14
            let pinH: CGFloat = 6
            let playDiam: CGFloat = min(w * 0.18, 72)

            // Greeting sizing — as large as the longer line allows across the
            // board width, so the greeting reads as the hero of the screen.
            let line1 = TimeOfDay.current(now).greeting + ","   // e.g. "GOOD AFTERNOON,"
            let line2 = userName.uppercased()                   // board world is caps
            let longerCount = max(line1.count, line2.count)
            let boardW  = w * 0.94
            let cellGap: CGFloat = 3
            let maxCellW: CGFloat = 38
            let fitW = (boardW - cellGap * CGFloat(longerCount - 1)) / CGFloat(longerCount)
            let cellW = min(maxCellW, fitW)
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
                    flapRow(text: line1, cellSz: cellSz, gap: cellGap)
                    Spacer().frame(height: cellGap)
                    flapRow(text: line2, cellSz: cellSz, gap: cellGap)
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
                            .frame(height: fullH / 2)
                        LinearGradient(
                            colors: [BoardColors.botFlapTop, BoardColors.botFlapBottom],
                            startPoint: .top, endPoint: .bottom)
                        .frame(height: fullH / 2)
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
    }
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
