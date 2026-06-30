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
            // Tapping anywhere off the field dismisses the keyboard. This sits
            // behind the content, so the input line and chevron still receive
            // their own taps.
            BoardColors.background
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { focused = false }

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

    // Cell sizing for the typed name — same 1.5 aspect + gap as the Home
    // greeting so the name reads as the identical board tile.
    private let nameCellW: CGFloat = 22
    private var nameCellSize: CGSize { CGSize(width: nameCellW, height: nameCellW * 1.5) }
    private let nameGap: CGFloat = 3

    private var inputLine: some View {
        VStack(spacing: 12) {
            HStack(spacing: 12) {
                // The name renders on the shared FlapCell — identical material to
                // the loader and the "GOOD MORNING, PAUL" greeting. A transparent
                // TextField behind it captures keystrokes and owns focus.
                ZStack(alignment: .leading) {
                    TextField("", text: $draft)
                        .font(.board(17))
                        .foregroundColor(.clear)            // text shown on the flap cells
                        .tint(BoardColors.character)        // visible bone caret marks focus
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .submitLabel(.go)
                        .focused($focused)
                        .onSubmit(submit)
                        .frame(height: nameCellSize.height)

                    if draft.isEmpty {
                        Text("WHAT IS YOUR NAME?")
                            .font(.board(17))
                            .foregroundColor(BoardColors.character.opacity(0.32))
                            .tracking(1)
                    } else {
                        nameCells
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture { focused = true }            // tap the line to (re)focus

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

    /// The typed name on shared FlapCells — uppercased into the board's caps
    /// world, spaces rendered as a half-cell gap, matching the Home greeting.
    private var nameCells: some View {
        HStack(spacing: nameGap) {
            ForEach(Array(draft.uppercased().enumerated()), id: \.offset) { _, ch in
                if ch == " " {
                    Color.clear.frame(width: nameCellSize.width * 0.5, height: nameCellSize.height)
                } else {
                    FlapCell(glyph: ch, size: nameCellSize)
                }
            }
        }
        .fixedSize()
        .animation(.easeOut(duration: 0.12), value: draft)
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
