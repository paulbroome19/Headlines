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
    var onGenerate: () -> Void = {}
    var onProfile:  () -> Void = {}

    @AppStorage("hasSeenHomeHint") private var hasSeenHomeHint = false
    /// Local flag so the hint stays visible for the whole first session
    /// even though we set the AppStorage flag in onAppear.
    @State private var showHint = false

    // MARK: Body

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height

            // Layout constants
            let seamY       = h * 0.60          // hinge seam position
            let pinW: CGFloat  = 14
            let pinH: CGFloat  = 6
            let playDiam: CGFloat = min(w * 0.18, 72)

            // Greeting cell sizing: fit the longer of the two lines
            let line1 = "GOOD MORNING,"         // 13 chars
            let line2 = userName.uppercased()    // board world is caps
            let longerCount = max(line1.count, line2.count)
            let boardW = w * 0.88
            let cellGap: CGFloat = 3
            let cellW  = (boardW - cellGap * CGFloat(longerCount - 1)) / CGFloat(longerCount)
            let cellH  = cellW * 1.5
            let cellSz = CGSize(width: cellW, height: cellH)

            ZStack(alignment: .top) {

                // ── Two-tone board background ───────────────────────────
                VStack(spacing: 0) {
                    BoardColors.panelTopGradient
                        .frame(height: seamY)
                    LinearGradient(
                        colors: [BoardColors.botFlapTop, BoardColors.botFlapBottom],
                        startPoint: .top, endPoint: .bottom)
                    .frame(height: h - seamY)
                }
                .ignoresSafeArea()

                // ── Grain overlay ───────────────────────────────────────
                Image(uiImage: BoardGrain.image)
                    .resizable(resizingMode: .tile)
                    .opacity(0.012)
                    .ignoresSafeArea()
                    .allowsHitTesting(false)

                // ── Hinge seam line + notch pins ───────────────────────
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

                // ── Main content column ─────────────────────────────────
                VStack(alignment: .leading, spacing: 0) {

                    Spacer(minLength: h * 0.22) // calm empty top space

                    // Greeting row 1: GOOD MORNING,
                    flapRow(text: line1, cellSz: cellSz, gap: cellGap)

                    Spacer().frame(height: cellGap)

                    // Greeting row 2: user name
                    flapRow(text: line2, cellSz: cellSz, gap: cellGap)

                    Spacer().frame(height: 14)

                    // Dateline
                    Text(datelineText())
                        .font(.board(11))
                        .foregroundColor(BoardColors.character.opacity(0.55))
                        .tracking(2.5)
                        .padding(.leading, 2)

                    Spacer(minLength: 40)
                }
                .padding(.horizontal, (w - boardW) / 2)

                // ── Play button — pinned dead-centre on the hinge seam ──
                // This is the signature element: it must straddle the seam
                // identically on every device, so we anchor it explicitly to
                // seamY rather than letting the greeting column flow it.
                playButton(diameter: playDiam)
                    .position(x: w / 2, y: seamY)

                // First-run hint — hangs just beneath the button, also
                // anchored to the seam so it tracks the button on every size.
                if showHint {
                    Text("PRESS PLAY TO GENERATE YOUR BRIEFING")
                        .font(.board(9))
                        .foregroundColor(BoardColors.character.opacity(0.35))
                        .tracking(1.8)
                        .multilineTextAlignment(.center)
                        .fixedSize()
                        .position(x: w / 2, y: seamY + playDiam * 0.5 + 22)
                }

                // ── Profile button — top-right ──────────────────────────
                VStack {
                    HStack {
                        Spacer()
                        profileButton()
                            .padding(.top, geo.safeAreaInsets.top + 12)
                            .padding(.trailing, 20)
                    }
                    Spacer()
                }
            }
            .frame(width: w, height: h)
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

#Preview {
    HomeView()
}
