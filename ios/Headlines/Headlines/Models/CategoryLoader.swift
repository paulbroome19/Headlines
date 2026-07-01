import Foundation

// MARK: - Domain types

struct CategoryGroup: Decodable, Identifiable {
    let slug: String
    let label: String
    let subcategories: [CategoryItem]
    var id: String { slug }
}

struct CategoryItem: Decodable, Identifiable {
    let slug: String
    let label: String
    /// Nested taxonomy depth (competitions → teams, rugby sub-levels, …). Empty at
    /// a leaf. Optional/defaulted so a leaf that omits the key still decodes.
    let subcategories: [CategoryItem]
    var id: String { slug }

    enum CodingKeys: String, CodingKey { case slug, label, subcategories }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slug = try c.decode(String.self, forKey: .slug)
        label = try c.decode(String.self, forKey: .label)
        subcategories = try c.decodeIfPresent([CategoryItem].self, forKey: .subcategories) ?? []
    }
}

// MARK: - Result type

enum CategoryLoadResult {
    case loaded([CategoryGroup])
    case failed(String)
}

// MARK: - In-memory cache

private actor CategoryCache {
    private(set) var groups: [CategoryGroup]?
    func store(_ groups: [CategoryGroup]) { self.groups = groups }
}

private let _categoryCache = CategoryCache()

// MARK: - Fetch

func fetchTopCategories(
    client: APIClient = APIClient(baseURL: AppConfig.apiBaseURL)
) async -> CategoryLoadResult {

    if let cached = await _categoryCache.groups {
        #if DEBUG
        print("📂 categories: cache hit (\(cached.count) groups)")
        #endif
        return .loaded(cached)
    }

    #if DEBUG
    let start = Date()
    print("📂 categories: → \(client.baseURL.absoluteString)/data/categories")
    #endif

    let result = await withTaskGroup(of: CategoryLoadResult.self) { group in
        group.addTask {
            do {
                let dto: CategoryGroupsDTO = try await client.get("data/categories")
                return .loaded(dto.groups)
            } catch {
                return .failed(error.localizedDescription)
            }
        }

        group.addTask {
            try? await Task.sleep(nanoseconds: 10_000_000_000)
            return .failed("Request timed out")
        }

        let first = await group.next()!
        group.cancelAll()
        return first
    }

    #if DEBUG
    let elapsed = Date().timeIntervalSince(start)
    switch result {
    case .loaded(let groups):
        let count = groups.reduce(0) { $0 + max(1, $1.subcategories.count) }
        print("📂 categories: ✓ \(groups.count) groups, \(count) items in \(String(format: "%.2f", elapsed))s")
    case .failed(let msg):
        print("📂 categories: ✗ failed after \(String(format: "%.2f", elapsed))s — \(msg)")
    }
    #endif

    if case .loaded(let groups) = result {
        await _categoryCache.store(groups)
    }
    return result
}

// MARK: - DTO (private to this file)

private struct CategoryGroupsDTO: Decodable {
    let groups: [CategoryGroup]
}
