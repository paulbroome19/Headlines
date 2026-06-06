import SwiftUI

/// Single-line text that scrolls horizontally when its natural width exceeds the container.
/// Sits static and truncates when it fits. Resets on text change.
///
/// Layout contract: wrap in .frame(height:) at the call site.
/// GeometryReader (the root view) is greedy — its size comes from the parent, never
/// from its children, so wide scrolling content never expands the layout footprint.
struct MarqueeText: View {
    let text: String
    let font: Font
    var speed: Double = 48        // pts / second
    var pauseDuration: Double = 1.5

    @State private var textWidth: CGFloat = 0
    @State private var startDate = Date()
    private let gap: CGFloat = 48

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            if textWidth > w && w > 0 {
                scrollingContent
                    .clipped()
                    .mask(edgeFade)
                    .onAppear { startDate = .now }
            } else {
                Text(text)
                    .font(font)
                    .lineLimit(1)
                    .frame(width: w, alignment: .leading)
            }
        }
        // Hidden text in an overlay measures natural width without touching layout.
        // .overlay never changes the base view's frame; .hidden() keeps rendering off
        // but layout still fires so the GeometryReader background sees the real size.
        .overlay(alignment: .leading) {
            Text(text)
                .font(font)
                .fixedSize(horizontal: true, vertical: false)
                .hidden()
                .background(GeometryReader { geo in
                    Color.clear
                        .onAppear { textWidth = geo.size.width }
                        .onChange(of: geo.size.width) { _, w in textWidth = w }
                })
        }
        .onChange(of: text) { _, _ in startDate = .now }
    }

    // MARK: - Scrolling content

    private var scrollingContent: some View {
        TimelineView(.animation) { ctx in
            let scrollable = max(1, textWidth + gap)
            let elapsed = max(0, ctx.date.timeIntervalSince(startDate) - pauseDuration)
            let raw = CGFloat(speed * elapsed).truncatingRemainder(dividingBy: scrollable)
            HStack(spacing: gap) {
                Text(text).font(font).lineLimit(1).fixedSize()
                Text(text).font(font).lineLimit(1).fixedSize()
            }
            .offset(x: -raw)
        }
    }

    // Right-edge fade so the loop point is invisible.
    private var edgeFade: some View {
        LinearGradient(
            stops: [
                .init(color: .black, location: 0.0),
                .init(color: .black, location: 0.88),
                .init(color: .clear, location: 1.0),
            ],
            startPoint: .leading,
            endPoint: .trailing
        )
    }
}
