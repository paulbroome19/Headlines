//
//  SolariBoardText.swift
//  Headlines
//
//  Split-flap board text that flips open once on appear — the SAME Solari treatment as the Home
//  greeting (HomeView.SolariGreeting), reusing the app's real FlapCell. Extracted as a shared
//  component so the completion screen renders "YOU'RE ALL / CAUGHT UP" in exactly the greeting's
//  style. Reduce-motion shows the settled text immediately.
//
//  Typography is the app's flap tile (FlapCell) — no bespoke fonts.
//

import SwiftUI

struct SolariBoardText: View {
    /// One word (or short phrase) per row — rendered as a grid of flap cells.
    let rows: [String]
    let cellSz: CGSize
    var gap: CGFloat = 3

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var start = Date()
    @State private var settled = false

    private static let flick = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    private static let stepInterval = 0.05

    private func settleAt(row: Int, col: Int) -> Double {
        0.18 + Double(row) * 0.11 + Double(col) * 0.03
    }
    private var totalSettle: Double {
        let maxCols = rows.map { $0.count }.max() ?? 0
        return settleAt(row: max(0, rows.count - 1), col: max(0, maxCols - 1)) + 0.12
    }

    private func glyph(_ target: Character, row: Int, col: Int, elapsed: Double) -> Character {
        if elapsed >= settleAt(row: row, col: col) { return target }
        let step = Int(elapsed / Self.stepInterval)
        let idx = abs((row &* 53) &+ (col &* 31) &+ (step &* 17) &+ 7) % Self.flick.count
        return Self.flick[idx]
    }

    var body: some View {
        Group {
            if reduceMotion || settled {
                grid { _, _, ch in ch }
            } else {
                TimelineView(.animation) { ctx in
                    let elapsed = ctx.date.timeIntervalSince(start)
                    grid { row, col, ch in glyph(ch, row: row, col: col, elapsed: elapsed) }
                }
            }
        }
        .onAppear {
            start = Date()
            settled = false
            DispatchQueue.main.asyncAfter(deadline: .now() + totalSettle + 0.05) { settled = true }
        }
    }

    private func grid(_ transform: @escaping (_ row: Int, _ col: Int, _ ch: Character) -> Character) -> some View {
        VStack(alignment: .center, spacing: gap) {
            ForEach(Array(rows.enumerated()), id: \.offset) { r, word in
                HStack(spacing: gap) {
                    ForEach(Array(word.enumerated()), id: \.offset) { c, ch in
                        if ch == " " {
                            Color.clear.frame(width: cellSz.width * 0.5, height: cellSz.height)
                        } else {
                            FlapCell(glyph: transform(r, c, ch), size: cellSz)
                        }
                    }
                }
            }
        }
    }

    /// Largest square-ish cell width at which every row fits within `boardWidth` (glyph = 1 cell,
    /// space = half). Mirrors the Home greeting's fitting so the two boards read identically.
    static func cellSize(rows: [String], boardWidth: CGFloat, gap: CGFloat = 3,
                         minWidth: CGFloat = 16, maxWidth: CGFloat = 34) -> CGSize {
        func units(_ s: String) -> (u: CGFloat, c: Int) {
            let chars = Array(s); let spaces = chars.filter { $0 == " " }.count
            return (CGFloat(chars.count - spaces) + 0.5 * CGFloat(spaces), chars.count)
        }
        let fitted = rows.map { r -> CGFloat in
            let (u, c) = units(r)
            guard u > 0 else { return .greatestFiniteMagnitude }
            return (boardWidth - gap * CGFloat(max(0, c - 1))) / u
        }.min() ?? maxWidth
        let w = max(minWidth, min(maxWidth, fitted))
        return CGSize(width: w, height: w * 1.5)
    }
}
