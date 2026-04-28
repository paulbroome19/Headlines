import SwiftUI

struct BriefingLoadingView: View {
    let isLoading: Bool
    let onCreateProfile: () -> Void

    var body: some View {
        if isLoading {
            LoadingStateView()
        } else {
            noProfilesContent
        }
    }

    private var noProfilesContent: some View {
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
            PrimaryButton(title: "Create Profile", action: onCreateProfile)
        }
        .frame(maxWidth: .infinity)
        .padding(48)
    }
}
