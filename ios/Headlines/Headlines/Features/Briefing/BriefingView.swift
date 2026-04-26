import SwiftUI

struct BriefingView: View {

    @StateObject private var vm = BriefingViewModel()
    @State private var showingProfilePicker = false
    @State private var showingCreateProfile = false
    @State private var showingEditProfile = false
    @State private var scriptExpanded = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 0) {
                    profileBar
                    Divider()
                    contentArea
                        .padding()
                }
            }
            .navigationTitle("Headlines")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await vm.reloadProfiles() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(vm.state == .loadingProfiles)
                }
            }
        }
        .task {
            if case .idle = vm.state {
                await vm.loadProfiles()
            }
        }
        .sheet(isPresented: $showingProfilePicker) {
            ProfilePickerSheet(profiles: vm.profiles, selected: vm.selectedProfile) { profile in
                vm.selectProfile(profile)
            }
        }
        .sheet(isPresented: $showingCreateProfile) {
            ProfileFormView(mode: .create, service: vm.service) {
                Task { await vm.reloadProfiles() }
            }
        }
        .sheet(isPresented: $showingEditProfile) {
            if let profile = vm.selectedProfile {
                ProfileFormView(mode: .edit(profile), service: vm.service) {
                    Task { await vm.reloadProfiles() }
                }
            }
        }
    }

    // MARK: - Profile bar

    private var profileBar: some View {
        HStack(spacing: 10) {
            Button {
                showingProfilePicker = true
            } label: {
                HStack(spacing: 8) {
                    if let profile = vm.selectedProfile {
                        avatarCircle(profile.name)
                        Text(profile.name)
                            .font(.subheadline.weight(.semibold))
                        Image(systemName: "chevron.down")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    } else {
                        Text("Select Profile")
                            .font(.subheadline)
                    }
                }
                .foregroundStyle(.primary)
            }

            Spacer()

            if vm.selectedProfile != nil {
                Button("Edit") { showingEditProfile = true }
                    .font(.subheadline)
            }

            Button {
                showingCreateProfile = true
            } label: {
                Image(systemName: "plus.circle")
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
    }

    // MARK: - Content area

    @ViewBuilder
    private var contentArea: some View {
        switch vm.state {
        case .idle, .loadingProfiles:
            loadingView
        case .noProfiles:
            noProfilesView
        case .failed(let msg):
            errorView(message: msg)
        default:
            mainContent
        }
    }

    private var loadingView: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("Loading…")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(48)
    }

    private var noProfilesView: some View {
        VStack(spacing: 16) {
            Image(systemName: "person.crop.circle.badge.plus")
                .font(.system(size: 52))
                .foregroundStyle(.secondary)
            Text("No profiles yet")
                .font(.headline)
            Text("Create a profile to generate your personalised news briefing.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
            Button("Create Profile") { showingCreateProfile = true }
                .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity)
        .padding(48)
    }

    private func errorView(message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 36))
                .foregroundStyle(.secondary)
            Text("Something went wrong")
                .font(.headline)
            Text(message)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
            Button("Try again") { Task { await vm.reloadProfiles() } }
                .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity)
        .padding(48)
    }

    // MARK: - Main content

    private var mainContent: some View {
        VStack(spacing: 16) {
            generateButton

            if vm.bulletin != nil {
                playerCard
                scriptCard
            }
        }
    }

    private var generateButton: some View {
        Button {
            Task { await vm.generateBulletin() }
        } label: {
            HStack(spacing: 8) {
                if vm.state == .generating || vm.state == .downloadingAudio {
                    ProgressView().tint(.white).scaleEffect(0.85)
                } else {
                    Image(systemName: "newspaper.fill")
                }
                Text(generateLabel)
                    .font(.headline)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
        }
        .buttonStyle(.borderedProminent)
        .disabled(!vm.canGenerate)
    }

    private var generateLabel: String {
        switch vm.state {
        case .generating: return "Generating Briefing…"
        case .downloadingAudio: return "Downloading Audio…"
        case .readyToPlay, .playing, .paused, .ended: return "Regenerate"
        default: return "Generate Briefing"
        }
    }

    private var playerCard: some View {
        VStack(spacing: 12) {
            ProgressView(value: vm.progress)
                .animation(.linear(duration: 0.25), value: vm.progress)

            HStack {
                Text(formatTime(vm.progress * vm.audioDuration))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                Spacer()
                Text(formatTime(vm.audioDuration))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            Button {
                vm.togglePlayPause()
            } label: {
                Image(systemName: playPauseIcon)
                    .font(.system(size: 56))
                    .symbolRenderingMode(.hierarchical)
            }
            .disabled(!vm.canTogglePlayPause)

            if let bulletin = vm.bulletin {
                HStack(spacing: 12) {
                    cacheBadge("Bulletin", cached: bulletin.bulletinCached)
                    cacheBadge("Audio", cached: bulletin.audioCached)
                    Spacer()
                    Text("\(bulletin.storyCount) stories")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private var scriptCard: some View {
        if let bulletin = vm.bulletin {
            VStack(alignment: .leading, spacing: 8) {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { scriptExpanded.toggle() }
                } label: {
                    HStack {
                        Text("Script")
                            .font(.subheadline.weight(.semibold))
                        Spacer()
                        Image(systemName: scriptExpanded ? "chevron.up" : "chevron.down")
                            .font(.caption)
                    }
                    .foregroundStyle(.primary)
                }

                if scriptExpanded, let script = bulletin.script {
                    Text(script)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 2)
                }
            }
            .padding()
            .background(Color(.secondarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    // MARK: - Helpers

    private var playPauseIcon: String {
        vm.state == .playing ? "pause.circle.fill" : "play.circle.fill"
    }

    private func avatarCircle(_ name: String) -> some View {
        Text(String(name.prefix(1)).uppercased())
            .font(.caption.weight(.bold))
            .frame(width: 24, height: 24)
            .background(Color.accentColor.opacity(0.15))
            .foregroundStyle(Color.accentColor)
            .clipShape(Circle())
    }

    private func cacheBadge(_ label: String, cached: Bool) -> some View {
        HStack(spacing: 4) {
            Circle()
                .fill(cached ? Color.green : Color.blue)
                .frame(width: 6, height: 6)
            Text("\(label): \(cached ? "cached" : "new")")
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
    }

    private func formatTime(_ seconds: TimeInterval) -> String {
        let s = max(0, seconds)
        return String(format: "%d:%02d", Int(s) / 60, Int(s) % 60)
    }
}

// MARK: - Profile picker sheet

struct ProfilePickerSheet: View {
    let profiles: [Profile]
    let selected: Profile?
    let onSelect: (Profile) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List(profiles) { profile in
                Button {
                    onSelect(profile)
                    dismiss()
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(profile.name)
                                .font(.body.weight(.medium))
                                .foregroundStyle(.primary)
                            Text("\(profile.maxStories) stories · \(categoryLabel(profile))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if profile.id == selected?.id {
                            Image(systemName: "checkmark")
                                .foregroundStyle(Color.accentColor)
                        }
                    }
                }
            }
            .navigationTitle("Select Profile")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    private func categoryLabel(_ profile: Profile) -> String {
        if let inc = profile.includeCategories, !inc.isEmpty {
            return inc.prefix(2).joined(separator: ", ")
        }
        return "all categories"
    }
}
