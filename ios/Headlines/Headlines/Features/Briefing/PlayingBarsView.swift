import SwiftUI

/// Three animated bars indicating the currently-playing story in the story list.
struct PlayingBarsView: View {
    @State private var phase = false

    private let barCount = 3
    private let barWidth: CGFloat = 2.5
    private let maxHeight: CGFloat = 11
    private let minHeight: CGFloat = 3
    private let spacing: CGFloat = 1.5

    var body: some View {
        HStack(alignment: .bottom, spacing: spacing) {
            ForEach(0..<barCount, id: \.self) { i in
                RoundedRectangle(cornerRadius: 1)
                    .fill(Color.accentColor)
                    .frame(width: barWidth, height: barHeight(for: i))
                    .animation(
                        .easeInOut(duration: 0.45 + Double(i) * 0.1)
                            .repeatForever(autoreverses: true)
                            .delay(Double(i) * 0.12),
                        value: phase
                    )
            }
        }
        .onAppear { phase = true }
    }

    private func barHeight(for index: Int) -> CGFloat {
        // Each bar animates to a different height so they look independent.
        let heights: [CGFloat] = [maxHeight, minHeight + 2, maxHeight - 3]
        return phase ? heights[index % heights.count] : minHeight
    }
}
