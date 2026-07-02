import SwiftUI

/// The ONE quiet, reusable loading indicator — three small flap tiles doing a
/// calm sequential-wave PULSE (a soft brightness travelling left→right, NOT the
/// hero flip/flicker), with a muted caption beneath. Drop it in place of any
/// default `ProgressView()` used as a loading state:
///
///     UtilityLoader()                          // "ONE MOMENT"
///     UtilityLoader(message: "LOADING TOPICS") // custom caption
///
/// Monochrome, built from the shared `FlapCell` / `BoardColors` / `LightColors`
/// tokens so it matches the app register everywhere. Reduce-motion → the tiles
/// hold a gentle static glow (no travelling animation).
struct UtilityLoader: View {
    var message: String = "ONE MOMENT"
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let tileCount = 3
    private let cell = CGSize(width: 13, height: 19)
    private let gap: CGFloat = 6

    var body: some View {
        VStack(spacing: 16) {
            if reduceMotion {
                tiles { _ in 0.55 }                       // static, calm
            } else {
                TimelineView(.animation) { ctx in
                    let t = ctx.date.timeIntervalSinceReferenceDate
                    tiles { i in Self.pulse(index: i, count: tileCount, t: t) }
                }
            }
            Text(message)
                .font(.label(11)).tracking(2.5)
                .foregroundColor(LightColors.ink.opacity(0.4))
        }
    }

    private func tiles(_ opacityFor: @escaping (Int) -> Double) -> some View {
        HStack(spacing: gap) {
            ForEach(0..<tileCount, id: \.self) { i in
                FlapCell(glyph: nil, size: cell)
                    .opacity(opacityFor(i))
            }
        }
    }

    /// A calm travelling pulse: each tile brightens in turn, left→right, on a
    /// ~1.5s cycle. Raised sine, sharpened, so a single soft peak glides across
    /// the three tiles rather than all three blinking together.
    static func pulse(index: Int, count: Int, t: Double) -> Double {
        let period = 1.5
        let phase = Double(index) / Double(count)
        let s = 0.5 + 0.5 * sin((t / period - phase) * 2 * .pi)   // 0…1
        return 0.28 + 0.72 * pow(s, 2.0)                          // 0.28…1.0, peaked
    }
}

/// Legacy shared loader — retained only for the (currently unreachable)
/// BriefingLoadingView. New code should use `UtilityLoader`.
struct LoadingStateView: View {
    var message: String = "ONE MOMENT"
    var body: some View { UtilityLoader(message: message) }
}
