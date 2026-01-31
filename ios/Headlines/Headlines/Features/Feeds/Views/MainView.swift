import SwiftUI

struct MainView: View {
    let feedsService: FeedsServicing

    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var feed: Feed?

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Headlines ✅ MainView v2")
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
                Section(header: Text(feed.title)) {
                    ForEach(feed.items) { item in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.text)
                                .font(.body)

                            Text("Summary \(item.summariseOutputID) • Position \(item.position)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 6)
                    }
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
            feed = try await feedsService.fetchLatestFeed()
        } catch {
            feed = nil
            errorMessage = String(describing: error)
        }

        isLoading = false
    }
}
