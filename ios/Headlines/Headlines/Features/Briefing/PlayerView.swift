import SwiftUI

struct PlayerView: View {
    let progress: Double
    let duration: TimeInterval
    let canTogglePlayPause: Bool
    let isPlaying: Bool
    // Navigation. canNavigate is false (and buttons invisible) until the backend
    // supplies start_time per story. Wire-up is zero-change once it does.
    let canNavigate: Bool
    let onPrevious: () -> Void
    let onTogglePlayPause: () -> Void
    let onNext: () -> Void

    var body: some View {
        VStack(spacing: 28) {
            controls
            progressTrack
        }
    }

    // MARK: - Controls row

    // Play/pause is always centred. Prev/next overlay the sides via ZStack so
    // the play button position is unaffected by navigation availability.
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

    // MARK: - Progress track

    private var progressTrack: some View {
        VStack(spacing: 8) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color(.systemFill))
                        .frame(height: 3)
                    Capsule()
                        .fill(Color.accentColor)
                        .frame(width: max(0, geo.size.width * CGFloat(progress)), height: 3)
                }
            }
            .frame(height: 3)
            .animation(.linear(duration: 0.25), value: progress)

            HStack {
                Text(formatTime(progress * duration))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)
                Spacer()
                Text(formatTime(duration))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
        }
    }

    // MARK: - Helpers

    private func formatTime(_ seconds: TimeInterval) -> String {
        let s = max(0, seconds)
        return String(format: "%d:%02d", Int(s) / 60, Int(s) % 60)
    }
}
