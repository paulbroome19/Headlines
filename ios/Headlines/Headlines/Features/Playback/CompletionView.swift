//
//  CompletionView.swift
//  Headlines
//
//  End-of-briefing completion state. Shown when playerState == .ended (NowPlayingView switch).
//  Also the clean destination for the "dead air after the last story" fix: audio ends → this,
//  zero dead air. Flip-board dark aesthetic (BoardColors), minimal, no new dependencies.
//
//  // UNVERIFIED — styling only (colours/spacing/type). Logic-free: two closures. Tune the look
//  against FlapCell/the now-playing card if you want the full split-flap treatment; kept plain
//  here to avoid coupling.
//

import SwiftUI

struct CompletionView: View {
    let onHome: () -> Void
    let onReplay: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 6) {
                Text("YOU'RE ALL")
                Text("CAUGHT UP")
            }
            .font(.system(.largeTitle, design: .monospaced).weight(.bold))
            .tracking(3)
            .foregroundColor(BoardColors.character)
            .multilineTextAlignment(.center)

            Text("That's the briefing.")
                .font(.system(.subheadline, design: .monospaced))
                .foregroundColor(BoardColors.character.opacity(0.55))
                .padding(.top, 14)

            Spacer()

            HStack(spacing: 14) {
                actionButton("Replay", system: "arrow.counterclockwise", action: onReplay)
                actionButton("Home", system: "house", action: onHome)
            }
            .padding(.bottom, 44)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(BoardColors.background.ignoresSafeArea())
    }

    private func actionButton(_ title: String, system: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: system)
                .font(.system(.body, design: .monospaced).weight(.semibold))
                .foregroundColor(BoardColors.character)
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(BoardColors.topFlapTop)
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .stroke(BoardColors.seam, lineWidth: 1)
                        )
                )
        }
        .buttonStyle(.plain)
    }
}
