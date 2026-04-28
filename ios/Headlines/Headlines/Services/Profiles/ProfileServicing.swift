import Foundation

protocol ProfileServicing {
    func fetchProfiles() async throws -> [Profile]
    func createProfile(name: String, maxDurationMinutes: Int, voice: String?, includeCategories: [String]?, excludeCategories: [String]?, includeTopStories: Bool) async throws -> Profile
    func updateProfile(id: Int, name: String, maxDurationMinutes: Int, voice: String?, includeCategories: [String]?, excludeCategories: [String]?, includeTopStories: Bool) async throws -> Profile
}
