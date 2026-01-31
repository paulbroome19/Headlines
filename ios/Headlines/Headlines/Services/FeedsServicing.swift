import Foundation

protocol FeedsServicing {
    func fetchLatestFeed() async throws -> Feed
}
