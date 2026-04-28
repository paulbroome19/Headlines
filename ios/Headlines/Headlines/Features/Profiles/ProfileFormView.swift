import SwiftUI

// MARK: - Filter load state (view-local)

private enum FilterState {
    case loading
    case ready([CategoryGroup])
    case failed(String)
}

// MARK: - View

struct ProfileFormView: View {

    enum Mode {
        case create
        case edit(Profile)

        var title: String {
            switch self {
            case .create: return "New Profile"
            case .edit(let p): return "Edit — \(p.name)"
            }
        }
    }

    let mode: Mode
    let service: ProfileServicing
    let onSaved: () -> Void

    @Environment(\.dismiss) private var dismiss

    // Identity
    @State private var name = ""

    // Listening
    @State private var maxDurationMinutes = 5
    @State private var includeTopStories = true

    // Filters
    @State private var selectedCategories: Set<String> = []
    @State private var filterState: FilterState = .loading

    // Form control
    @State private var isSaving = false
    @State private var errorMessage: String?

    private let durationOptions = [3, 5, 10]

    // MARK: - Body

    var body: some View {
        NavigationStack {
            Form {
                identitySection
                listeningSection
                filtersSections
                if let error = errorMessage { saveErrorSection(error) }
            }
            .navigationTitle(mode.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(isSaving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "Saving…" : "Save") {
                        Task { await save() }
                    }
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || isSaving)
                }
            }
        }
        .onAppear(perform: populateForm)
        .task { await loadFilters() }
    }

    // MARK: - Sections

    private var identitySection: some View {
        Section("Identity") {
            TextField("Name", text: $name).autocorrectionDisabled()
        }
    }

    private var listeningSection: some View {
        Section {
            Picker("Duration", selection: $maxDurationMinutes) {
                ForEach(durationOptions, id: \.self) { mins in
                    Text("\(mins) min").tag(mins)
                }
            }
            .pickerStyle(.segmented)
            Toggle("Include top stories", isOn: $includeTopStories)
        } header: {
            Text("Listening")
        } footer: {
            Text("Start briefings with the biggest stories, even if they're outside your filters.")
        }
    }

    @ViewBuilder
    private var filtersSections: some View {
        switch filterState {
        case .loading:
            Section("Filters") {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Loading filters…")
                        .foregroundStyle(.secondary)
                        .font(.footnote)
                }
                .padding(.vertical, 2)
            }

        case .failed(let msg):
            Section("Filters") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Couldn't load filters")
                        .foregroundStyle(.secondary)
                        .font(.footnote)
                    Text(msg)
                        .foregroundStyle(.secondary)
                        .font(.caption)
                    Button("Retry") { Task { await loadFilters() } }
                        .font(.footnote)
                }
                .padding(.vertical, 2)
            }

        case .ready(let groups):
            ForEach(groups) { group in
                if group.subcategories.isEmpty {
                    Section {
                        Toggle(group.label, isOn: categoryBinding(for: group.slug))
                    }
                } else {
                    Section(group.label) {
                        ForEach(group.subcategories) { item in
                            Toggle(item.label, isOn: categoryBinding(for: item.slug))
                        }
                    }
                }
            }
            Section {
                EmptyView()
            } footer: {
                Text("Select categories to include. Leave all off to include everything.")
            }
        }
    }

    private func saveErrorSection(_ message: String) -> some View {
        Section {
            Text(message).foregroundStyle(.red).font(.footnote)
        }
    }

    // MARK: - Category helpers

    private func categoryBinding(for slug: String) -> Binding<Bool> {
        Binding(
            get: { selectedCategories.contains(slug) },
            set: { if $0 { selectedCategories.insert(slug) } else { selectedCategories.remove(slug) } }
        )
    }

    private func loadFilters() async {
        filterState = .loading
        let result = await fetchTopCategories()
        switch result {
        case .loaded(let groups): filterState = .ready(groups)
        case .failed(let msg):    filterState = .failed(msg)
        }
    }

    // MARK: - Populate from profile

    private func populateForm() {
        guard case .edit(let profile) = mode else { return }
        name                 = profile.name
        maxDurationMinutes   = durationOptions.contains(profile.maxDurationMinutes)
                                   ? profile.maxDurationMinutes : 5
        includeTopStories    = profile.includeTopStories
        selectedCategories   = Set(profile.includeCategories ?? [])
    }

    // MARK: - Save

    private func save() async {
        isSaving = true
        errorMessage = nil

        let trimmedName = name.trimmingCharacters(in: .whitespaces)
        let inc: [String]? = selectedCategories.isEmpty ? nil : selectedCategories.sorted()

        do {
            switch mode {
            case .create:
                _ = try await service.createProfile(
                    name: trimmedName,
                    maxDurationMinutes: maxDurationMinutes,
                    voice: nil,
                    includeCategories: inc,
                    excludeCategories: nil,
                    includeTopStories: includeTopStories
                )
            case .edit(let profile):
                _ = try await service.updateProfile(
                    id: profile.id,
                    name: trimmedName,
                    maxDurationMinutes: maxDurationMinutes,
                    voice: nil,
                    includeCategories: inc,
                    excludeCategories: nil,
                    includeTopStories: includeTopStories
                )
            }
            onSaved()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
            isSaving = false
        }
    }
}
