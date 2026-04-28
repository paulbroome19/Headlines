import SwiftUI

struct SettingsView: View {
    var body: some View {
        NavigationStack {
            List {
                Section("API") {
                    LabeledContent("Host", value: AppConfig.apiBaseURL.host ?? "—")
                    LabeledContent("Port", value: AppConfig.apiBaseURL.port.map(String.init) ?? "—")
                }
            }
            .navigationTitle("Settings")
        }
    }
}
