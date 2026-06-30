//
//  MachinedDisc.swift
//  Headlines
//
//  The machined black disc — the single forward control material shared across
//  the app so it renders pixel-identically everywhere:
//   • Home  → the Play disc (a triangle on the face)
//   • Onboarding / Filters → the forward "arrow" disc (an arrow on the face)
//
//  The face is board material (radial charcoal gradient + top-edge highlight +
//  matte grain); `MachinedDiscButtonStyle` adds the real depth (soft drop
//  shadow + a small press-in). Compose `MachinedDisc` inside a `Button` and
//  apply the style — see HomeView.playButton and CreateProfileView.
//

import SwiftUI

// MARK: - Glyph shapes

/// Solid play triangle (▶) — used on the Home Play disc.
struct Triangle: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to:    CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}

/// An open forward arrow (→) — a shaft plus a chevron head, stroked (never
/// filled). Used on the onboarding / filters forward disc.
struct ForwardArrow: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let midY = rect.midY
        let head = rect.height / 2          // arrowhead reaches the full height
        // shaft
        p.move(to:    CGPoint(x: rect.minX, y: midY))
        p.addLine(to: CGPoint(x: rect.maxX, y: midY))
        // head
        p.move(to:    CGPoint(x: rect.maxX - head, y: midY - head))
        p.addLine(to: CGPoint(x: rect.maxX,        y: midY))
        p.addLine(to: CGPoint(x: rect.maxX - head, y: midY + head))
        return p
    }
}

// MARK: - Disc button style (depth)

/// The real depth: a soft drop shadow that tightens on press, plus a small
/// scale-in. Apply to the `Button` wrapping a `MachinedDisc`.
struct MachinedDiscButtonStyle: ButtonStyle {
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

// MARK: - Disc face

/// The machined disc face. Pass the glyph (triangle / arrow) as `content`; it is
/// centred on the face. `dimmed` drops the whole control back for a disabled
/// state (e.g. the forward arrow before a name is entered).
struct MachinedDisc<Content: View>: View {
    let diameter: CGFloat
    var dimmed: Bool = false
    @ViewBuilder var content: () -> Content

    var body: some View {
        ZStack {
            // Board-material circle background.
            Circle()
                .fill(
                    RadialGradient(
                        colors: [BoardColors.topFlapTop, BoardColors.botFlapBottom],
                        center: .init(x: 0.5, y: 0.35),
                        startRadius: 0,
                        endRadius: diameter * 0.6
                    )
                )

            // Subtle top-edge highlight (raised feel).
            Circle()
                .fill(
                    LinearGradient(
                        colors: [.white.opacity(0.08), .clear],
                        startPoint: .top, endPoint: .center
                    )
                )

            // Grain on the face.
            Image(uiImage: BoardGrain.image)
                .resizable(resizingMode: .tile)
                .opacity(0.018)
                .clipShape(Circle())

            content()
        }
        .frame(width: diameter, height: diameter)
        .opacity(dimmed ? 0.4 : 1.0)
        .animation(.easeInOut(duration: 0.15), value: dimmed)
    }
}
