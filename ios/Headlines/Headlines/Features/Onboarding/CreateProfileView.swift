//
//  CreateProfileView.swift
//  Headlines
//
//  Onboarding step 1 — capture the user's name, in the locked LIGHT register
//  (matching the home): a contained dark flap-board card on a near-white page.
//  Persists `userName`; the first-run-complete flag is NOT set here (that
//  happens after topics, in BuildBriefingView). Advances via `onContinue`.
//

import SwiftUI

// MARK: - Shared two-step progress indicator

/// Two dots, the active step lit. Used by the filters screen (BuildBriefingView)
/// on the dark board; Create Profile uses its own light "1 OF 2" label.
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

    // Page margins + card geometry.
    private let pageMargin: CGFloat = BottomActionBar.pageMargin
    private let cardPadding: CGFloat = 24
    private let cardCorner:  CGFloat = 18
    private let inputSize:   CGFloat = 22     // placeholder == typed-name size

    private var trimmed: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var canSubmit: Bool { !trimmed.isEmpty }

    var body: some View {
        GeometryReader { geo in
            let cardWidth  = geo.size.width - pageMargin * 2
            let innerWidth = cardWidth - cardPadding * 2

            ZStack {
                // Light page. Tapping anywhere off the field dismisses the
                // keyboard; the card / field / buttons keep their own taps.
                LightColors.page
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture { focused = false }

                VStack(spacing: 0) {
                    // Step indicator — subtle, top-right.
                    HStack {
                        Spacer()
                        Text("1 OF 2")
                            .font(.label(12))
                            .tracking(2)
                            .foregroundColor(LightColors.ink.opacity(0.4))
                    }
                    .padding(.horizontal, pageMargin + 4)
                    .padding(.top, 8)

                    Spacer()

                    card(innerWidth: innerWidth)
                        .frame(width: cardWidth)

                    Spacer()

                    actionBar
                }
            }
        }
        .onAppear {
            // Light delay so the cross-dissolve settles before the keyboard rises.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) { focused = true }
        }
    }

    // MARK: - The dark flap-board card

    private func card(innerWidth: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 30) {
            // Heading on shared flap cells (the one flap moment on this screen).
            headingCells("CREATE PROFILE", maxWidth: innerWidth)

            // Name input — placeholder sits ON the line at the typed-name size.
            inputArea
        }
        .padding(.horizontal, cardPadding)
        .padding(.vertical, cardPadding + 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(cardBackground)
    }

    private var cardBackground: some View {
        ZStack {
            RoundedRectangle(cornerRadius: cardCorner, style: .continuous)
                .fill(LinearGradient(
                    colors: [BoardColors.topFlapTop, BoardColors.botFlapBottom],
                    startPoint: .top, endPoint: .bottom))

            Image(uiImage: BoardGrain.image)
                .resizable(resizingMode: .tile)
                .opacity(0.02)
                .clipShape(RoundedRectangle(cornerRadius: cardCorner, style: .continuous))
                .allowsHitTesting(false)

            // Hairline top-edge highlight — the same raised material language.
            RoundedRectangle(cornerRadius: cardCorner, style: .continuous)
                .strokeBorder(Color.white.opacity(0.05), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.22), radius: 22, y: 12)
    }

    // MARK: Heading flap cells

    /// "CREATE PROFILE" on shared FlapCells, auto-sized to fit the card width.
    private func headingCells(_ text: String, maxWidth: CGFloat) -> some View {
        let upper = text.uppercased()
        let chars = Array(upper)
        let gap: CGFloat = 3
        let spaces   = chars.filter { $0 == " " }.count
        let nonSpace = chars.count - spaces
        let units = CGFloat(nonSpace) + 0.5 * CGFloat(spaces)
        let maxCellW: CGFloat = 26
        let fitW = (maxWidth - gap * CGFloat(max(0, chars.count - 1))) / max(units, 1)
        let cellW = min(maxCellW, fitW)
        let size = CGSize(width: cellW, height: cellW * 1.5)

        return HStack(spacing: gap) {
            ForEach(Array(chars.enumerated()), id: \.offset) { _, ch in
                if ch == " " {
                    Color.clear.frame(width: cellW * 0.5, height: size.height)
                } else {
                    FlapCell(glyph: ch, size: size)
                }
            }
        }
    }

    // MARK: Name input

    private var inputArea: some View {
        VStack(spacing: 14) {
            ZStack(alignment: .leading) {
                if draft.isEmpty {
                    // Placeholder ON the line, at the same size the name will be.
                    Text("What is your name?")
                        .font(.board(inputSize))
                        .foregroundColor(BoardColors.character.opacity(0.32))
                }

                // Real text field — visible caret + off-white typed text (plain,
                // NOT flap cells). Standard placeholder behaviour: the overlay
                // above clears the moment text exists.
                TextField("", text: $draft)
                    .font(.board(inputSize))
                    .foregroundColor(BoardColors.character)
                    .tint(BoardColors.character)            // visible caret
                    .textInputAutocapitalization(.words)
                    .autocorrectionDisabled()
                    .submitLabel(.next)
                    .focused($focused)
                    .onSubmit(submit)
            }
            .frame(height: inputSize * 1.4)
            .contentShape(Rectangle())
            .onTapGesture { focused = true }                // tap the line to focus

            // Light hairline input line on the dark card.
            Rectangle()
                .fill(BoardColors.character.opacity(0.28))
                .frame(height: 1)
        }
    }

    // MARK: - Bottom action bar (locked footer pattern — shared component)

    private var actionBar: some View {
        BottomActionBar(title: "BUILD YOUR BRIEFING", enabled: canSubmit, action: submit)
    }

    // MARK: - Submit

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
