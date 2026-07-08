//
//  CompletionView.swift
//  Headlines
//
//  End-of-briefing completion state (playerState == .ended). "YOU'RE ALL / CAUGHT UP" rendered on
//  the app's real split-flap board — the SAME Solari treatment as the Home greeting (SolariBoardText
//  + FlapCell on a boardCard tablet, on the light page) — with Replay + Home as icon-only round
//  discs directly under the board, echoing the app's play disc. Reuses Board.swift / BoardColors /
//  MachinedDisc; no bespoke typography.
//

import SwiftUI

struct CompletionView: View {
    let onHome: () -> Void
    let onReplay: () -> Void

    private let rows = ["YOU'RE ALL", "CAUGHT UP"]
    private let pageMargin: CGFloat = 20
    private let boardPad: CGFloat = 26
    private let cellGap: CGFloat = 3
    private let discDiam: CGFloat = 58

    var body: some View {
        GeometryReader { geo in
            let boardInnerW = geo.size.width - pageMargin * 2 - boardPad * 2
            let cellSz = SolariBoardText.cellSize(rows: rows, boardWidth: boardInnerW, gap: cellGap)

            ZStack {
                LightColors.page.ignoresSafeArea()

                // Board + the two discs read as ONE centred composition (Spacers top and bottom),
                // not pinned to the screen edge.
                VStack(spacing: 28) {
                    Spacer()

                    // ── The dark flap-board tablet (same material as the Home greeting) ──
                    SolariBoardText(rows: rows, cellSz: cellSz, gap: cellGap)
                        .padding(boardPad)
                        .frame(maxWidth: .infinity)
                        .boardCard(cornerRadius: 18)
                        .padding(.horizontal, pageMargin)

                    // ── Icon-only round discs directly under the board ──
                    HStack(spacing: 24) {
                        disc(system: "arrow.counterclockwise", label: "Replay", action: onReplay)
                        disc(system: "house", label: "Home", action: onHome)
                    }

                    Spacer()
                }
            }
        }
    }

    /// A round dark disc echoing the app's play disc (MachinedDisc + MachinedDiscButtonStyle), with
    /// a cream (character) SF Symbol. Icon-only; the tap target is the full disc (≥44pt).
    private func disc(system: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            MachinedDisc(diameter: discDiam) {
                Image(systemName: system)
                    .font(.system(size: discDiam * 0.34, weight: .semibold))
                    .foregroundColor(BoardColors.character)
            }
        }
        .buttonStyle(MachinedDiscButtonStyle())
        .frame(width: discDiam, height: discDiam)
        .accessibilityLabel(label)
    }
}

#Preview {
    CompletionView(onHome: {}, onReplay: {})
}
