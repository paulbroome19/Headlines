import SwiftUI

struct ProfileListView: View {
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
                            Text("\(profile.maxDurationMinutes) min · \(categoryLabel(profile))")
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
