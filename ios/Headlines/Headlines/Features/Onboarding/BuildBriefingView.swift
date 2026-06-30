//
//  BuildBriefingView.swift
//  Headlines
//
//  Onboarding step 2 — pick the topics that shape the briefing, using the
//  reusable FilterTreeView. Persists the selection (JSON) and sets the
//  first-run-complete flag, then routes to Home via `onContinue`.
//

import SwiftUI

struct BuildBriefingView: View {
    var onContinue: () -> Void = {}

    @AppStorage("didOnboard")     private var didOnboard = false
    @AppStorage("selectedTopics") private var selectedTopicsJSON = ""

    @StateObject private var model = FilterTreeModel(roots: TopicSeed.defaultTree())

    var body: some View {
        ZStack {
            BoardColors.background.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                // The tree scrolls between the fixed heading and fixed footer.
                ScrollView(showsIndicators: false) {
                    FilterTreeView(model: model)
                        .padding(.top, 4)
                        .padding(.bottom, 16)
                }
                footer
            }
        }
    }

    // MARK: Fixed heading

    private var header: some View {
        VStack(spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("BUILD YOUR BRIEFING")
                    .font(.board(20))
                    .foregroundColor(BoardColors.character)
                    .tracking(2)
                Spacer()
                StepDots(step: 2, total: 2)
            }
            .padding(.horizontal, 24)
            .padding(.top, 12)

            Rectangle()
                .fill(BoardColors.character.opacity(0.18))
                .frame(height: 1)
        }
        .background(BoardColors.background)
    }

    // MARK: Fixed footer

    private var footer: some View {
        VStack(spacing: 0) {
            // Soft divider above the footer.
            LinearGradient(colors: [.clear, BoardColors.seam.opacity(0.7)],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: 14)
                .allowsHitTesting(false)

            Button(action: continueTapped) {
                HStack {
                    Text("CONTINUE")
                        .font(.board(18))
                        .foregroundColor(BoardColors.character)
                        .tracking(2)
                    Spacer()
                    OpenChevron()
                        .stroke(BoardColors.character.opacity(0.9),
                                style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        .frame(width: 11, height: 18)
                }
                .padding(.horizontal, 24)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.top, 10)
            .padding(.bottom, 28)
        }
        .background(BoardColors.background)
    }

    // MARK: Persist + advance

    private func continueTapped() {
        // Encode the selected leaf ids to JSON for restore (Filters screen / next launch).
        if let data = try? JSONEncoder().encode(model.selectedLeafIDs()),
           let json = String(data: data, encoding: .utf8) {
            selectedTopicsJSON = json
        }
        didOnboard = true
        onContinue()
    }
}

// MARK: - Preview

#Preview {
    BuildBriefingView()
}
