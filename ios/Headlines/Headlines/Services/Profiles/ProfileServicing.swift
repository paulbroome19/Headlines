import Foundation

protocol ProfileServicing {
    func fetchProfiles() async throws -> [Profile]
    func createProfile(name: String, maxStories: Int, voice: String?, includeCategories: [String]?, excludeCategories: [String]?) async throws -> Profile
    func updateProfile(id: Int, name: String, maxStories: Int, voice: String?, includeCategories: [String]?, excludeCategories: [String]?) async throws -> Profile
    func generateBulletin(profileID: Int) async throws -> BulletinResult
    func audioFileURL(forBulletinID bulletinId: Int) -> URL
}
