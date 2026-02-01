import SwiftUI

struct PlaybackView: View {

    @StateObject private var viewModel: PlaybackViewModel

    init(viewModel: PlaybackViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel)
    }

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Now Playing")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            Task { await viewModel.loadLatest() }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .accessibilityLabel("Refresh")
                        .disabled(viewModel.state == .loading)
                    }
                }
        }
        .task {
            // Load once on first appearance
            if case .idle = viewModel.state {
                await viewModel.loadLatest()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {

        case .idle, .loading:
            VStack(spacing: 14) {
                ProgressView()
                Text("Building your briefing…")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            .padding()

        case .failed(let message):
            VStack(spacing: 12) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 34))
                    .foregroundStyle(.secondary)

                Text("Couldn’t load playback")
                    .font(.headline)

                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                Button("Try again") {
                    Task { await viewModel.loadLatest() }
                }
                .buttonStyle(.borderedProminent)
                .padding(.top, 4)
            }
            .padding()

        case .ready, .playing, .paused, .ended:
            if let np = viewModel.nowPlaying {
                VStack(alignment: .leading, spacing: 14) {

                    // Story label
                    Text("Story \(np.itemIndex + 1)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)

                    // The story text
                    Text(np.item.text)
                        .font(.title3.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)

                    // Progress
                    ProgressView(value: np.progress)
                        .padding(.top, 6)

                    HStack {
                        Text("\(Int(np.progress * 100))%")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        Spacer()

                        Text(stateLabel(viewModel.state))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    // Controls
                    HStack(spacing: 18) {

                        Button {
                            viewModel.skipToPrevious()
                        } label: {
                            Image(systemName: "backward.fill")
                                .frame(width: 48, height: 48)
                        }
                        .buttonStyle(.bordered)

                        Button {
                            viewModel.togglePlayPause()
                        } label: {
                            Image(systemName: playPauseIcon(for: viewModel.state))
                                .frame(width: 64, height: 48)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!canPlayOrPause(viewModel.state))

                        Button {
                            viewModel.skipToNext()
                        } label: {
                            Image(systemName: "forward.fill")
                                .frame(width: 48, height: 48)
                        }
                        .buttonStyle(.bordered)
                    }
                    .padding(.top, 8)

                    Spacer()
                }
                .padding()
            } else {
                VStack(spacing: 12) {
                    Text("No audio loaded yet")
                        .font(.headline)

                    Button("Load latest") {
                        Task { await viewModel.loadLatest() }
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding()
            }
        }
    }

    // MARK: - Helpers

    private func stateLabel(_ state: PlaybackViewModel.PlaybackState) -> String {
        switch state {
        case .idle: return "idle"
        case .loading: return "loading"
        case .ready: return "ready"
        case .playing: return "playing"
        case .paused: return "paused"
        case .ended: return "ended"
        case .failed: return "failed"
        }
    }

    private func playPauseIcon(for state: PlaybackViewModel.PlaybackState) -> String {
        switch state {
        case .playing: return "pause.fill"
        default: return "play.fill"
        }
    }

    private func canPlayOrPause(_ state: PlaybackViewModel.PlaybackState) -> Bool {
        switch state {
        case .ready, .playing, .paused:
            return true
        default:
            return false
        }
    }
}
