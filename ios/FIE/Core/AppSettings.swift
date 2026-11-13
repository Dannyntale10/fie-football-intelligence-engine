import Foundation

enum AppSettings {
    static let defaultServerURL = "http://192.168.1.165:8001"
    static let serverURLKey = "fie.serverURL"

    static var serverURL: String {
        let stored = UserDefaults.standard.string(forKey: serverURLKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let stored, !stored.isEmpty { return stored }
        return defaultServerURL
    }
}
