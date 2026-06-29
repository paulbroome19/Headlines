//
//  CreateProfileView.swift
//  Headlines
//
//  Onboarding step 1 — capture the user's name. Persists `userName`; the
//  first-run-complete flag is NOT set here (that happens after topics, in
//  BuildBriefingView). Advances to step 2 via `onContinue`.
//

import SwiftUI

// MARK: - Shared two-step progress indicator

/// Two dots, the active step lit. Shared by both onboarding screens.
struct StepDots: View {
    let step: Int          // 1-based
    let total: Int

    var body: some View {
        HStack(spacing: 7) {
            ForEach(0..<total, id: \.self) { i in
                Circle()
                    .fill(BoardColors.character.opacity(i == step - 1 ? 0.9 : 0.22))
                    .frame(width: 6, height: 6)
            }
        }
    }
}

// MARK: - CreateProfileView

struct CreateProfileView: View {
    var onContinue: () -> Void = {}

    @AppStorage("userName") private var userName = ""
    @State private var draft = ""
    @FocusState private var focused: Bool

    private var trimmed: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var canSubmit: Bool { !trimmed.isEmpty }

    var body: some View {
        ZStack {
            BoardColors.background.ignoresSafeArea()

            // Progress indicator — top-right, step 1 lit.
            VStack {
                HStack {
                    Spacer()
                    StepDots(step: 1, total: 2)
                }
                Spacer()
            }
            .padding(.horizontal, 28)
            .padding(.top, 8)

            // Centred block: heading + full-width input line.
            VStack(alignment: .leading, spacing: 22) {
                Text("CREATE PROFILE")
                    .font(.board(20))
                    .foregroundColor(BoardColors.character)
                    .tracking(2)

                inputLine
            }
            .padding(.horizontal, 28)
        }
        .onAppear {
            // Light delay so the cross-dissolve settles before the keyboard rises.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) { focused = true }
        }
    }

    // MARK: Input line

    private var inputLine: some View {
        VStack(spacing: 10) {
            HStack(spacing: 12) {
                ZStack(alignment: .leading) {
                    if draft.isEmpty {
                        Text("WHAT IS YOUR NAME?")
                            .font(.board(17))
                            .foregroundColor(BoardColors.character.opacity(0.32))
                            .tracking(1)
                    }
                    TextField("", text: $draft)
                        .font(.board(17))
                        .foregroundColor(BoardColors.character)
                        .tint(BoardColors.character)
                        .tracking(1)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .submitLabel(.go)
                        .focused($focused)
                        .onSubmit(submit)
                }

                // Open-chevron "enter" — dimmed until a name is entered.
                Button(action: submit) {
                    OpenChevron()
                        .stroke(BoardColors.character.opacity(canSubmit ? 0.9 : 0.25),
                                style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        .frame(width: 11, height: 18)
                        .frame(width: 36, height: 36)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(!canSubmit)
                .animation(.easeInOut(duration: 0.15), value: canSubmit)
            }

            // Full-width hairline rule, edge-to-edge.
            Rectangle()
                .fill(BoardColors.character.opacity(0.25))
                .frame(height: 1)
        }
    }

    private func submit() {
        guard canSubmit else { return }
        userName = trimmed
        onContinue()
    }
}

// MARK: - Preview

#Preview {
    CreateProfileView()
}
