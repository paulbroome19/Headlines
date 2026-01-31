import SwiftUI

struct MainView: View {
    let feedsService: FeedsServicing

    @State private var isLoading: Bool = false
    @State private var errorMessage: String? = nil
    @State private var feed: Feed? = nil

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Headlines")
        }
        .task {
            await load()
        }
    }

    @ViewBuilder
    private var content: some View {
        if isLoading {
            ProgressView("Loading…")
                .padding()
        } else if let errorMessage {
            VStack(spacing: 12) {
                Text("Something went wrong")
                    .font(.headline)

                Text(errorMessage)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                Button("Try again") {
                    Task { await load() }
                }
                .buttonStyle(.borderedProminent)
            }
            .padding()
        } else if let feed {
            List {
                Section {
                    ForEach(Array(feed.items.enumerated()), id: \.offset) { _, item in
                        Text(item)
                            .padding(.vertical, 6)
                    }
                } header: {
                    Text(feed.title)
                }
            }
        } else {
            VStack(spacing: 12) {
                Text("No feed yet")
                    .font(.headline)

                Button("Load feed") {
                    Task { await load() }
                }
                .buttonStyle(.bordered)
            }
            .padding()
        }
    }

    private func load() async {
        isLoading = true
        errorMessage = nil

        do {
            let latest = try await feedsService.fetchLatestFeed()
            feed = latest
        } catch {
            feed = nil
            errorMessage = String(describing: error)
        }

        isLoading = false
    }
}
