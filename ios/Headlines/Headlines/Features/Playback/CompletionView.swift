//
//  CompletionView.swift
//  Headlines
//
//  End-of-briefing completion state (playerState == .ended). "YOU'RE ALL / CAUGHT UP" rendered on
//  the app's real split-flap board — the SAME Solari treatment as the Home greeting (SolariBoardText
//  + FlapCell on a boardCard tablet, on the light page) — with Replay + Home as the app's dark
//  board buttons UNDER the board. No bespoke typography; reuses Board.swift / BoardColors.
//

import SwiftUI

struct CompletionView: View {
    let onHome: () -> Void
    let onReplay: () -> Void

    private let rows = ["YOU'RE ALL", "CAUGHT UP"]
    private let pageMargin: CGFloat = 20
    private let boardPad: CGFloat = 26
    private let cellGap: CGFloat = 3

    var body: some View {
        GeometryReader { geo in
            let boardInnerW = geo.size.width - pageMargin * 2 - boardPad * 2
            let cellSz = SolariBoardText.cellSize(rows: rows, boardWidth: boardInnerW, gap: cellGap)

            ZStack {
                LightColors.page.ignoresSafeArea()

                VStack(spacing: 0) {
                    Spacer()

                    // ── The dark flap-board tablet (same material as the Home greeting) ──
                    VStack(spacing: 16) {
                        SolariBoardText(rows: rows, cellSz: cellSz, gap: cellGap)
                        Text("THAT'S THE BRIEFING")
                            .font(.label(11)).tracking(2.5)
                            .foregroundColor(BoardColors.character.opacity(0.5))
                    }
                    .padding(boardPad)
                    .frame(maxWidth: .infinity)
                    .boardCard(cornerRadius: 18)
                    .padding(.horizontal, pageMargin)

                    Spacer()

                    // ── Actions UNDER the board — the app's dark board buttons ──
                    HStack(spacing: 12) {
                        actionButton("REPLAY", system: "arrow.counterclockwise", action: onReplay)
                        actionButton("HOME", system: "house", action: onHome)
                    }
                    .padding(.horizontal, pageMargin)
                    .padding(.bottom, 44)
                }
            }
        }
    }

    /// A dark board button: charcoal flap-tile fill, cream (character) label in the app's mono
    /// label font, hairline seam edge — consistent with the board surfaces used elsewhere.
    private func actionButton(_ title: String, system: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: system).font(.system(size: 14, weight: .semibold))
                Text(title).font(.label(12)).tracking(2)
            }
            .foregroundColor(BoardColors.character)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .background(
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .fill(LinearGradient(colors: [BoardColors.topFlapTop, BoardColors.botFlapBottom],
                                         startPoint: .top, endPoint: .bottom))
                    .overlay(
                        RoundedRectangle(cornerRadius: 13, style: .continuous)
                            .stroke(BoardColors.seam, lineWidth: 1)
                    )
                    .shadow(color: .black.opacity(0.28), radius: 5, y: 3)
            )
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    CompletionView(onHome: {}, onReplay: {})
}
