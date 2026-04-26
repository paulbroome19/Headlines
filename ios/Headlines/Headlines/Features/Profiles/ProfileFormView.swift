import SwiftUI

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

    @State private var name = ""
    @State private var maxStories = 8
    @State private var voice = ""
    @State private var includeText = ""
    @State private var excludeText = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Identity") {
                    TextField("Name", text: $name)
                        .autocorrectionDisabled()
                    Stepper("Max Stories: \(maxStories)", value: $maxStories, in: 1...20)
                }

                Section {
                    TextField("Voice ID (optional)", text: $voice)
                        .font(.system(.body, design: .monospaced))
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } footer: {
                    Text("ElevenLabs voice ID. Leave blank to use server default (George, British male).")
                }

                Section {
                    TextField("e.g. politics, sport", text: $includeText)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    TextField("e.g. entertainment", text: $excludeText)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("Category Filters (comma-separated)")
                } footer: {
                    Text("Use taxonomy slugs: politics, world, sport, technology, business, health, climate, science, entertainment. Leave blank for all categories.")
                }

                if let error = errorMessage {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                            .font(.footnote)
                    }
                }
            }
            .navigationTitle(mode.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(isSaving)
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
    }

    // MARK: - Form population

    private func populateForm() {
        guard case .edit(let profile) = mode else { return }
        name = profile.name
        maxStories = profile.maxStories
        voice = profile.voice ?? ""
        includeText = profile.includeCategories?.joined(separator: ", ") ?? ""
        excludeText = profile.excludeCategories?.joined(separator: ", ") ?? ""
    }

    // MARK: - Save

    private func save() async {
        isSaving = true
        errorMessage = nil

        let trimmedName = name.trimmingCharacters(in: .whitespaces)
        let trimmedVoice = voice.trimmingCharacters(in: .whitespaces)
        let inc = parseCats(includeText)
        let exc = parseCats(excludeText)

        do {
            switch mode {
            case .create:
                _ = try await service.createProfile(
                    name: trimmedName,
                    maxStories: maxStories,
                    voice: trimmedVoice.isEmpty ? nil : trimmedVoice,
                    includeCategories: inc,
                    excludeCategories: exc
                )
            case .edit(let profile):
                _ = try await service.updateProfile(
                    id: profile.id,
                    name: trimmedName,
                    maxStories: maxStories,
                    voice: trimmedVoice.isEmpty ? nil : trimmedVoice,
                    includeCategories: inc,
                    excludeCategories: exc
                )
            }
            onSaved()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
            isSaving = false
        }
    }

    private func parseCats(_ text: String) -> [String]? {
        let cats = text
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            .filter { !$0.isEmpty }
        return cats.isEmpty ? nil : cats
    }
}
