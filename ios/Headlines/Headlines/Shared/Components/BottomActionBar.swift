//
//  BottomActionBar.swift
//  Headlines
//
//  The locked footer pattern shared across the light register (Create Profile,
//  the filters screen, …): a thin INSET hairline rule (margins both sides,
//  never edge-to-edge) above a row with a CTA label on the left and the shared
//  machined forward-arrow disc on the right. Lit when `enabled`, dimmed when not.
//
//  Build the footer once, use it everywhere, so the two onboarding screens (and
//  the settings filters) can never drift apart.
//

import SwiftUI

struct BottomActionBar: View {
    let title: String
    var enabled: Bool = true
    let action: () -> Void

    /// The forward arrow is the SAME size everywhere it appears.
    static let arrowDiam: CGFloat = 56
    static let pageMargin: CGFloat = 20

    var body: some View {
        VStack(spacing: 0) {
            // Inset hairline rule — margins both sides, never touches the edges.
            Rectangle()
                .fill(LightColors.ink.opacity(0.16))
                .frame(height: 1)
                .padding(.horizontal, Self.pageMargin)

            HStack(alignment: .center) {
                Text(title)
                    .font(.label(13))
                    .tracking(2)
                    .foregroundColor(LightColors.ink.opacity(enabled ? 0.9 : 0.32))

                Spacer()

                Button(action: action) {
                    MachinedDisc(diameter: Self.arrowDiam, dimmed: !enabled) {
                        ForwardArrow()
                            .stroke(BoardColors.character,
                                    style: StrokeStyle(lineWidth: Self.arrowDiam * 0.06,
                                                       lineCap: .round, lineJoin: .round))
                            .frame(width: Self.arrowDiam * 0.40, height: Self.arrowDiam * 0.34)
                            .offset(x: Self.arrowDiam * 0.02)    // optical centring
                    }
                }
                .buttonStyle(MachinedDiscButtonStyle())
                .frame(width: Self.arrowDiam, height: Self.arrowDiam)
                .disabled(!enabled)
            }
            .padding(.horizontal, Self.pageMargin)
            .padding(.top, 18)
        }
        .padding(.bottom, 10)
        .animation(.easeInOut(duration: 0.15), value: enabled)
    }
}
