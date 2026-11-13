import Foundation

enum AppSettings {
    static let defaultServerURL = "https://fie-football-intelligence-engine.onrender.com"
    static let serverURLKey = "fie.serverURL"
    private static let apiKeyAccount = "engine-api-key"

    static var serverURL: String {
        let stored = UserDefaults.standard.string(forKey: serverURLKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let stored, !stored.isEmpty { return stored }
        return defaultServerURL
    }

    static var apiKey: String {
        Keychain.read(apiKeyAccount)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    static func saveAPIKey(_ value: String) {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            Keychain.delete(apiKeyAccount)
        } else {
            Keychain.write(trimmed, account: apiKeyAccount)
        }
    }
}
