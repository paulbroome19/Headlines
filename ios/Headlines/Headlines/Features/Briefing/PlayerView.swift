import SwiftUI

struct PlayerView: View {
    let nowPlayingTitle: String
    let progress: Double           // position within current segment (0–1)
    let duration: TimeInterval     // current segment duration
    let globalProgress: Double     // position across all story units (0–1)
    let totalDuration: TimeInterval
    let storyUnits: [StoryUnit]
    let canTogglePlayPause: Bool
    let isPlaying: Bool
    let canNavigate: Bool
    let onPrevious: () -> Void
    let onTogglePlayPause: () -> Void
    let onNext: () -> Void
    let onSeek: (Double) -> Void   // called with global fraction on drag release

    // Local drag state — drives the thumb position while dragging.
    @State private var isDragging = false
    @State private var dragFraction: Double = 0

    var body: some View {
        VStack(spacing: 24) {
            headline
            controls
            scrubber
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Now-playing headline

    private var headline: some View {
        Text(nowPlayingTitle)
            .font(.title3.bold())
            .lineLimit(1)
            .truncationMode(.tail)
            .frame(maxWidth: .infinity, alignment: .leading)
            .clipped()
    }

    // MARK: - Controls row

    private var controls: some View {
        ZStack {
            playButton

            HStack {
                Button(action: onPrevious) {
                    Image(systemName: "backward.end.fill")
                        .font(.system(size: 22))
                        .foregroundStyle(.primary)
                        .frame(width: 52, height: 52)
                        .contentShape(Rectangle())
                }
                .disabled(!canNavigate)
                .opacity(canNavigate ? 1 : 0)
                .animation(.easeInOut(duration: 0.2), value: canNavigate)

                Spacer()

                Button(action: onNext) {
                    Image(systemName: "forward.end.fill")
                        .font(.system(size: 22))
                        .foregroundStyle(.primary)
                        .frame(width: 52, height: 52)
                        .contentShape(Rectangle())
                }
                .disabled(!canNavigate)
                .opacity(canNavigate ? 1 : 0)
                .animation(.easeInOut(duration: 0.2), value: canNavigate)
            }
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Play button

    private var playButton: some View {
        Button(action: onTogglePlayPause) {
            Image(systemName: isPlaying ? "pause.circle.fill" : "play.circle.fill")
                .font(.system(size: 80))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(canTogglePlayPause ? Color.accentColor : Color(.tertiaryLabel))
        }
        .disabled(!canTogglePlayPause)
    }

    // MARK: - Scrubber

    private var displayFraction: Double { isDragging ? dragFraction : globalProgress }

    private var scrubber: some View {
        VStack(spacing: 8) {
            GeometryReader { geo in
                let width = geo.size.width
                ZStack(alignment: .leading) {
                    // Track background
                    Capsule()
                        .fill(Color(.systemFill))
                        .frame(height: isDragging ? 5 : 3)

                    // Filled portion
                    Capsule()
                        .fill(Color.accentColor)
                        .frame(width: max(0, width * CGFloat(displayFraction)),
                               height: isDragging ? 5 : 3)

                    // Story-unit boundary markers
                    ForEach(boundaryFractions, id: \.self) { frac in
                        Rectangle()
                            .fill(Color(.systemBackground))
                            .frame(width: 1.5, height: isDragging ? 7 : 5)
                            .offset(x: width * CGFloat(frac) - 0.75)
                    }

                    // Drag thumb — only shown while dragging
                    if isDragging {
                        Circle()
                            .fill(Color.accentColor)
                            .frame(width: 14, height: 14)
                            .offset(x: width * CGFloat(displayFraction) - 7)
                            .shadow(color: .black.opacity(0.15), radius: 2, x: 0, y: 1)
                    }
                }
                .frame(height: 20)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            isDragging = true
                            dragFraction = max(0, min(1, value.location.x / width))
                        }
                        .onEnded { value in
                            let fraction = max(0, min(1, value.location.x / width))
                            isDragging = false
                            onSeek(fraction)
                        }
                )
            }
            .frame(height: 20)
            .animation(.linear(duration: isDragging ? 0 : 0.25), value: displayFraction)

            HStack {
                Text(formatTime(displayFraction * totalDuration))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)
                Spacer()
                Text(formatTime(totalDuration))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
        }
    }

    // MARK: - Boundary fractions

    /// Fractions (0–1) in the story timeline where story units begin (skip the first = 0).
    private var boundaryFractions: [Double] {
        guard totalDuration > 0, storyUnits.count > 1 else { return [] }
        return storyUnits.dropFirst().map { unit in
            unit.cumulativeStartSeconds / totalDuration
        }
    }

    // MARK: - Helpers

    private func formatTime(_ seconds: TimeInterval) -> String {
        let s = max(0, seconds)
        return String(format: "%d:%02d", Int(s) / 60, Int(s) % 60)
    }
}
