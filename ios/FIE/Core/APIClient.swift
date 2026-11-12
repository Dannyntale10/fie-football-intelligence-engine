import Foundation

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct APIClient {
    let baseURL: URL

    init(baseURL: URL) {
        self.baseURL = baseURL
    }

    static func fromStoredSettings() -> APIClient {
        let raw = AppSettings.serverURL
        let url = URL(string: raw) ?? URL(string: AppSettings.defaultServerURL)!
        return APIClient(baseURL: url)
    }

    func get<T: Decodable>(
        _ path: String,
        query: [URLQueryItem] = [],
        timeout: TimeInterval = 60,
        as type: T.Type = T.self
    ) async throws -> T {
        try await send(path: path, method: "GET", query: query, timeout: timeout)
    }

    func post<T: Decodable>(
        _ path: String,
        query: [URLQueryItem] = [],
        timeout: TimeInterval = 600,
        as type: T.Type = T.self
    ) async throws -> T {
        try await send(path: path, method: "POST", query: query, timeout: timeout)
    }

    private func send<T: Decodable>(
        path: String,
        method: String,
        query: [URLQueryItem],
        timeout: TimeInterval
    ) async throws -> T {
        guard var components = URLComponents(
            url: baseURL.appendingPathComponent(path),
            resolvingAgainstBaseURL: false
        ) else {
            throw APIError(message: "The server URL in Settings is not valid.")
        }
        if !query.isEmpty { components.queryItems = query }
        guard let url = components.url else {
            throw APIError(message: "Could not build the request URL.")
        }

        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw APIError(message: Self.describe(error))
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError(message: "The engine returned an unexpected response.")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError(
                message: Self.detail(from: data) ?? "The engine returned HTTP \(http.statusCode)."
            )
        }

        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch {
            throw APIError(message: "Could not read the engine response: \(error.localizedDescription)")
        }
    }

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private static func detail(from data: Data) -> String? {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let detail = object["detail"]
        else { return nil }
        if let text = detail as? String { return text }
        if let rows = detail as? [[String: Any]] {
            let messages = rows.compactMap { $0["msg"] as? String }
            if !messages.isEmpty { return messages.joined(separator: " · ") }
        }
        return nil
    }

    private static func describe(_ error: Error) -> String {
        guard let urlError = error as? URLError else { return error.localizedDescription }
        switch urlError.code {
        case .cannotConnectToHost, .cannotFindHost:
            return "Cannot reach the engine at \(AppSettings.serverURL). Start the backend and check the server URL in Settings."
        case .timedOut:
            return "The engine did not answer in time. A bookmaker feed refresh can take several minutes; try again shortly."
        case .notConnectedToInternet, .networkConnectionLost:
            return "No usable network connection."
        case .appTransportSecurityRequiresSecureConnection:
            return "iOS blocked the plain-HTTP request. Use the localhost URL or an HTTPS deployment."
        default:
            return urlError.localizedDescription
        }
    }
}
