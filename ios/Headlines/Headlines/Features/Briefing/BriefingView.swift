import SwiftUI

struct BriefingView: View {

    @StateObject private var vm = BriefingViewModel()
    @State private var showingProfilePicker = false
    @State private var showingCreateProfile = false
    @State private var showingEditProfile   = false
    @State private var transcriptExpanded   = false

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding(.horizontal, 24)
                .padding(.top, 20)
                .padding(.bottom, 20)

            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Color(.systemBackground).ignoresSafeArea())
        .task {
            if case .idle = vm.state { await vm.loadProfiles() }
        }
        .sheet(isPresented: $showingProfilePicker) {
            ProfileListView(profiles: vm.profiles, selected: vm.selectedProfile) {
                vm.selectProfile($0)
            }
        }
        .sheet(isPresented: $showingCreateProfile) {
            ProfileFormView(mode: .create, service: vm.profileService) {
                Task { await vm.reloadProfiles() }
            }
        }
        .sheet(isPresented: $showingEditProfile) {
            if let profile = vm.selectedProfile {
                ProfileFormView(mode: .edit(profile), service: vm.profileService) {
                    Task { await vm.reloadProfiles() }
                }
            }
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 3) {
                Text(contextLabel.uppercased())
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.tertiary)
                    .kerning(0.8)

                Button {
                    guard vm.profiles.count > 1 else { return }
                    showingProfilePicker = true
                } label: {
                    HStack(spacing: 5) {
                        Text(vm.selectedProfile?.name ?? "Headlines")
                            .font(.title2.weight(.semibold))
                            .foregroundStyle(.primary)
                        if vm.profiles.count > 1 {
                            Image(systemName: "chevron.down")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.tertiary)
                        }
                    }
                }
                .buttonStyle(.plain)
            }

            Spacer()

            Menu {
                if vm.selectedProfile != nil {
                    Button { showingEditProfile = true } label: {
                        Label("Edit Profile", systemImage: "person.crop.circle")
                    }
                    Divider()
                }
                Button { showingCreateProfile = true } label: {
                    Label("New Profile", systemImage: "plus")
                }
                Button { Task { await vm.reloadProfiles() } } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.title3)
                    .foregroundStyle(.secondary)
                    .frame(width: 40, height: 40)
                    .contentShape(Rectangle())
            }
        }
    }

    // MARK: - Content routing

    @ViewBuilder
    private var content: some View {
        switch vm.state {
        case .idle, .loadingProfiles:
            loadingView
        case .noProfiles:
            emptyView
        case .failed(let msg):
            errorView(msg)
        default:
            mainContent
        }
    }

    // MARK: - Loading

    private var loadingView: some View {
        VStack {
            Spacer()
            ProgressView()
            Spacer()
        }
    }

    // MARK: - Empty

    private var emptyView: some View {
        VStack(spacing: 28) {
            Spacer()
            VStack(spacing: 12) {
                Image(systemName: "waveform")
                    .font(.system(size: 44, weight: .ultraLight))
                    .foregroundStyle(Color(.tertiaryLabel))
                VStack(spacing: 6) {
                    Text("No profiles yet")
                        .font(.headline)
                    Text("Create a profile to receive\nyour personalised briefing.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
            }
            Button("Create Profile") { showingCreateProfile = true }
                .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding(.horizontal, 48)
        .frame(maxWidth: .infinity)
    }

    // MARK: - Error

    private func errorView(_ message: String) -> some View {
        VStack(spacing: 24) {
            Spacer()
            VStack(spacing: 12) {
                Image(systemName: "exclamationmark.circle")
                    .font(.system(size: 32, weight: .light))
                    .foregroundStyle(.secondary)
                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 40)
            }
            Button("Try Again") { Task { await vm.reloadProfiles() } }
                .buttonStyle(.bordered)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Main content

    private var mainContent: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 0) {
                playerSection
                    .padding(.horizontal, 32)
                    .padding(.top, 48)
                    .padding(.bottom, 36)

                actionSection
                    .padding(.horizontal, 28)

                if let bulletin = vm.bulletin {
                    storiesSection(bulletin)
                        .padding(.horizontal, 28)
                        .padding(.top, 44)
                        .padding(.bottom, 52)
                }
            }
        }
    }

    // MARK: - Player section

    @ViewBuilder
    private var playerSection: some View {
        switch vm.state {
        case .generating:
            statusView("Generating briefing…")
        case .downloadingAudio:
            statusView("Downloading audio…")
        case .readyToPlay, .playing, .paused, .ended:
            PlayerView(
                progress: vm.progress,
                duration: vm.audioDuration,
                canTogglePlayPause: vm.canTogglePlayPause,
                isPlaying: vm.state == .playing,
                canNavigate: vm.hasStoryTimings,
                onPrevious: vm.previousStory,
                onTogglePlayPause: vm.togglePlayPause,
                onNext: vm.nextStory
            )
        default:
            idleView
        }
    }

    private func statusView(_ label: String) -> some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(0.9)
            Text(label)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 200)
    }

    private var idleView: some View {
        Image(systemName: "waveform")
            .font(.system(size: 52, weight: .ultraLight))
            .foregroundStyle(Color(.quaternaryLabel))
            .frame(maxWidth: .infinity)
            .frame(height: 200)
    }

    // MARK: - Action section

    @ViewBuilder
    private var actionSection: some View {
        switch vm.state {
        case .generating, .downloadingAudio:
            EmptyView()
        case .readyToPlay, .playing, .paused, .ended:
            Button { Task { await vm.generateBulletin() } } label: {
                Text("Regenerate")
                    .font(.subheadline)
                    .foregroundStyle(.tertiary)
            }
            .buttonStyle(.plain)
        default:
            Button { Task { await vm.generateBulletin() } } label: {
                Text("Generate Briefing")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
            }
            .buttonStyle(.borderedProminent)
            .disabled(!vm.canGenerate)
        }
    }

    // MARK: - Stories section

    @ViewBuilder
    private func storiesSection(_ bulletin: BulletinResult) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Rectangle()
                .fill(Color(.separator))
                .frame(height: 0.5)
                .padding(.bottom, 20)

            HStack(alignment: .firstTextBaseline) {
                Text("\(bulletin.storyCount) \(bulletin.storyCount == 1 ? "story" : "stories")".uppercased())
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.tertiary)
                    .kerning(0.5)
                Spacer()
                if !bulletin.bulletinCached {
                    Text("Fresh")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .overlay(Capsule().strokeBorder(Color(.separator), lineWidth: 0.5))
                }
            }
            .padding(.bottom, 18)

            if !bulletin.stories.isEmpty {
                storyRows(bulletin.stories)
            }
        }
    }

    private func storyRows(_ stories: [BulletinStory]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(stories.enumerated()), id: \.element.id) { index, story in
                let isCurrent = vm.currentStoryIndex == index
                HStack(alignment: .top, spacing: 12) {
                    Text("\(index + 1)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(isCurrent ? Color.accentColor : Color(.quaternaryLabel))
                        .frame(width: 18, alignment: .trailing)
                        .padding(.top, 2)

                    VStack(alignment: .leading, spacing: 3) {
                        Text(story.headline)
                            .font(.subheadline)
                            .foregroundStyle(isCurrent ? .primary : Color(.label).opacity(0.7))
                            .fixedSize(horizontal: false, vertical: true)

                        if let category = story.category {
                            Text(categoryDisplay(category))
                                .font(.caption2.weight(.medium))
                                .foregroundStyle(.tertiary)
                                .kerning(0.3)
                        }
                    }

                    Spacer(minLength: 0)
                }
                .padding(.vertical, 8)
                .padding(.horizontal, 8)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color(.systemFill))
                        .opacity(isCurrent ? 1 : 0)
                )
                .contentShape(Rectangle())
                .onTapGesture { vm.seekToStory(at: index) }
                .animation(.easeInOut(duration: 0.2), value: isCurrent)
            }
        }
        .padding(.horizontal, -8)
    }

    private func categoryDisplay(_ slug: String) -> String {
        slug.split(separator: ".").first.map { $0.prefix(1).uppercased() + $0.dropFirst() } ?? slug
    }

    // MARK: - Helpers

    private var contextLabel: String {
        let hour = Calendar.current.component(.hour, from: Date())
        switch hour {
        case 5..<12:  return "Morning briefing"
        case 12..<17: return "Afternoon briefing"
        default:      return "Evening briefing"
        }
    }
}
