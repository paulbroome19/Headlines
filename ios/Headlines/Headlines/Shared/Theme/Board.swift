//
//  Board.swift
//  Headlines
//
//  Shared split-flap board material extracted from SplitFlapLoadingView.
//  Every board-style screen (loader, home, future detail) references these
//  types so the rendered output is pixel-identical everywhere.
//

import SwiftUI
import UIKit

// MARK: - Hex colour helper

extension Color {
    init(hex: UInt32) {
        self.init(.sRGB,
                  red:   Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8)  & 0xFF) / 255,
                  blue:  Double( hex        & 0xFF) / 255,
                  opacity: 1)
    }
}

// MARK: - Design tokens

enum BoardColors {
    // Locked monochrome board — no warmth anywhere.
    static let background    = Color(hex: 0x0B0B0B)   // near-black neutral

    // Per-cell vertical gradient #2E2E2E → #151515, split across the two flaps.
    static let topFlapTop    = Color(hex: 0x2E2E2E)
    static let topFlapBottom = Color(hex: 0x232323)
    static let botFlapTop    = Color(hex: 0x1E1E1E)
    static let botFlapBottom = Color(hex: 0x151515)

    static let seam          = Color(hex: 0x0A0A0A)   // hinge seam / shadows
    static let character     = Color(hex: 0xEDEDEA)   // bone off-white (lit type)

    static let panelTopGradient = LinearGradient(
        colors: [topFlapTop, topFlapBottom], startPoint: .top, endPoint: .bottom)
    static let panelBottomGradient = LinearGradient(
        colors: [botFlapTop, botFlapBottom], startPoint: .top, endPoint: .bottom)
}

// MARK: - Light register tokens

/// The light "page" world the dark board sits ON — the locked register shared by
/// the new home, onboarding (Create Profile) and the filters screen. The flap
/// board is always a contained dark instrument on this near-white page.
enum LightColors {
    static let page = Color(hex: 0xF7F7F5)   // near-white page
    static let ink  = Color(hex: 0x141414)   // near-black ink for labels/rules
}

extension View {
    /// The dark flap-board card surface that sits ON the light page — charcoal
    /// gradient, matte grain, a hairline top-edge highlight and a soft drop
    /// shadow. Shared by the Home greeting hero and the onboarding cards so the
    /// contained instrument is identical everywhere.
    /// `flush`: for a board that fills its region and reads as ONE continuous surface with the
    /// page (e.g. the Home tablet), use a soft, near-halo-free shadow so the board's edge
    /// doesn't cast a darker off-white band onto the page beside/below it. Default keeps the
    /// original floating-card shadow (onboarding cards).
    func boardCard(cornerRadius: CGFloat = 18, flush: Bool = false) -> some View {
        background {
            ZStack {
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(LinearGradient(
                        colors: [BoardColors.topFlapTop, BoardColors.botFlapBottom],
                        startPoint: .top, endPoint: .bottom))

                Image(uiImage: BoardGrain.image)
                    .resizable(resizingMode: .tile)
                    .opacity(0.02)
                    .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
                    .allowsHitTesting(false)

                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(Color.white.opacity(0.05), lineWidth: 1)
            }
            .shadow(color: .black.opacity(flush ? 0.10 : 0.22),
                    radius: flush ? 12 : 22, y: flush ? 4 : 12)
        }
    }
}

// MARK: - Layout metrics

enum BoardMetrics {
    static let glyphHeightRatio: CGFloat = 0.62
    static let cornerFraction:   CGFloat = 0.12
}

// MARK: - Grain texture

enum BoardGrain {
    /// A small, deterministic monochrome noise tile, generated once and tiled
    /// under a very low opacity so the board reads as coated material, not flat.
    static let image: UIImage = make(size: 128)

    private static func make(size: Int) -> UIImage {
        var pixels = [UInt8](repeating: 0, count: size * size * 4)
        var seed: UInt64 = 0x9E3779B97F4A7C15            // fixed seed → no per-launch flicker
        func next() -> UInt8 {
            seed = seed &* 6364136223846793005 &+ 1442695040888963407
            return UInt8((seed >> 56) & 0xFF)
        }
        for i in 0..<(size * size) {
            // Very low-amplitude noise: a faint deviation around mid-grey so the
            // tile reads as the subtlest matte texture, never visible static. The
            // mean is unchanged (neutral 128) so surfaces keep their tone; only
            // the contrast is cut hard (~8× quieter) so cells and the play button
            // stay smooth machined material at any normal viewing distance.
            let dev = (Int(next()) - 128) >> 3            // ≈ −16 … +15
            let v = UInt8(clamping: 128 + dev)
            pixels[i * 4] = v; pixels[i * 4 + 1] = v; pixels[i * 4 + 2] = v; pixels[i * 4 + 3] = 255
        }
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: &pixels, width: size, height: size,
                                  bitsPerComponent: 8, bytesPerRow: size * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
              let cg = ctx.makeImage() else {
            return UIImage()
        }
        return UIImage(cgImage: cg)
    }
}

// MARK: - Shape helpers

struct BoardRoundedCorner: Shape {
    var radius: CGFloat
    var corners: UIRectCorner
    func path(in rect: CGRect) -> Path {
        Path(UIBezierPath(roundedRect: rect,
                          byRoundingCorners: corners,
                          cornerRadii: CGSize(width: radius, height: radius)).cgPath)
    }
}

// MARK: - Flap half panel

enum FlapHalf { case top, bottom }

/// One half of a split-flap cell — gradient fill, glyph centred on the full
/// cell height (clipped to the half), and the correct material edges (top
/// sheen for the upper half; hinge shadow + hairline highlight for the lower).
struct FlapHalfPanel: View {
    let glyph: Character?
    let half:  FlapHalf
    let size:  CGSize
    var shade: Double = 0

    var body: some View {
        let corner = size.width * BoardMetrics.cornerFraction
        let halfH  = size.height / 2
        let shape  = BoardRoundedCorner(
            radius:  corner,
            corners: half == .top ? [.topLeft, .topRight] : [.bottomLeft, .bottomRight])

        ZStack {
            (half == .top ? BoardColors.panelTopGradient : BoardColors.panelBottomGradient)

            if let g = glyph, g != " " {
                Text(String(g))
                    .font(.board(size.height * BoardMetrics.glyphHeightRatio))
                    .foregroundColor(BoardColors.character)
                    .lineLimit(1)
                    .fixedSize()
                    .frame(width: size.width, height: size.height)
                    .offset(y: half == .top ? size.height / 4 : -size.height / 4)
            }

            if shade > 0 { Color.black.opacity(shade) }
        }
        .frame(width: size.width, height: halfH)
        .overlay(alignment: .top) {
            if half == .top {
                LinearGradient(colors: [.white.opacity(0.05), .clear],
                               startPoint: .top, endPoint: .bottom)
                    .frame(height: halfH * 0.16)
                    .allowsHitTesting(false)
            } else {
                ZStack(alignment: .top) {
                    LinearGradient(colors: [.black.opacity(0.5), .clear],
                                   startPoint: .top, endPoint: .bottom)
                        .frame(height: halfH * 0.22)
                    Color.white.opacity(0.06)
                        .frame(height: max(0.5, size.height * 0.006))
                }
                .allowsHitTesting(false)
            }
        }
        .clipShape(shape)
    }
}

// MARK: - Seam line

/// The thin hinge gap line drawn at the mid-line of a cell.
struct FlapSeam: View {
    let size: CGSize
    var body: some View {
        Rectangle()
            .fill(BoardColors.seam)
            .frame(width: size.width, height: max(1, size.height * 0.02))
            .frame(width: size.width, height: size.height) // centres the seam at mid-line
    }
}

// MARK: - Full static cell

/// A complete, non-animated flap tile showing one glyph.
/// Compose this on any board screen; the loader animates around these same
/// building blocks via `FlapHalfPanel` + `FlapSeam` directly.
struct FlapCell: View {
    let glyph: Character?
    let size:  CGSize

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                FlapHalfPanel(glyph: glyph, half: .top,    size: size)
                FlapHalfPanel(glyph: glyph, half: .bottom, size: size)
            }
            FlapSeam(size: size)
        }
        .frame(width: size.width, height: size.height)
        .shadow(color: .black.opacity(0.6), radius: 2, y: 1.5)
    }
}

// MARK: - Haptics

/// The app's single, sparing haptic vocabulary — physical and premium, never noisy.
/// Only four moments have feedback; everything else (scrolling, ordinary taps) stays
/// silent on purpose. Nothing uses `.heavy`.
///
/// System setting: UIKit feedback generators are gated by the OS "System Haptics" toggle
/// (Settings ▸ Sounds & Haptics), so when the user turns haptics off these produce nothing
/// — the system preference is respected with no extra code and no readable API needed.
///
/// Generators are long-lived and reused; `prepare*()` warms the Taptic Engine just before an
/// imminent event so the tap lands with zero perceptible latency. All calls are main-thread
/// (SwiftUI actions / main-queue timers), as feedback generators require.
enum Haptics {
    private static let lightImpact  = UIImpactFeedbackGenerator(style: .light)
    private static let softImpact   = UIImpactFeedbackGenerator(style: .soft)
    private static let selectionGen = UISelectionFeedbackGenerator()

    /// Light tap — user presses play/pause.
    static func play() { lightImpact.impactOccurred() }

    /// Soft tick — a Solari word settling into place. ONE per settle, never per flap.
    static func boardSettle() { softImpact.impactOccurred(intensity: 0.7) }

    /// Gentle success thud — the briefing is ready. A fuller soft landing than the tick.
    static func briefingReady() { softImpact.impactOccurred(intensity: 1.0) }

    /// Selection feedback — filter toggles and length cards. The crisp, quiet selection
    /// tick (UISelectionFeedbackGenerator is the canonical primitive for a changed choice).
    static func selection() { selectionGen.selectionChanged() }

    static func prepareTransport() { lightImpact.prepare() }
    static func prepareSelection() { selectionGen.prepare() }
    static func prepareBoard()     { softImpact.prepare() }
}
