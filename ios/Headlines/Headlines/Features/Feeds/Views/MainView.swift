import SwiftUI

struct MainView: View {

    // MARK: - State

    @StateObject private var vm: FeedsViewModel

    // MARK: - Init

    init(feedsService: FeedsServicing) {
        _vm = StateObject(
            wrappedValue: FeedsViewModel(feedsService: feedsService)
        )
    }

    // MARK: - View

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Headlines")
        }
        .task {
            await vm.loadLatestFeed()
        }
    }

    @ViewBuilder
    private var content: some View {
        if vm.isLoading {
            ProgressView("Loading…")
                .padding()

        } else if let error = vm.errorMessage {
            VStack(spacing: 12) {
                Text("Something went wrong")
                    .font(.headline)

                Text(error)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                Button("Try again") {
                    Task { await vm.loadLatestFeed() }
                }
                .buttonStyle(.borderedProminent)
            }
            .padding()

        } else if let feed = vm.feed {
            List {
                Section {
                    ForEach(feed.items) { item in
                        Text(item.text)
                            .padding(.vertical, 6)
                    }
                } header: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(feed.title)
                            .font(.headline)

                        if let updated = vm.lastUpdatedText {
                            Text("Updated \(updated)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .textCase(nil)
                }
            }
            .refreshable {
                await vm.loadLatestFeed()
            }

        } else {
            VStack(spacing: 12) {
                Text("No feed yet")
                    .font(.headline)

                Button("Load feed") {
                    Task { await vm.loadLatestFeed() }
                }
                .buttonStyle(.bordered)
            }
            .padding()
        }
    }
}
