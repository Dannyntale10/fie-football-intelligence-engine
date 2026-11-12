import Foundation

enum Fmt {
    static func percent(_ value: Double?, digits: Int = 1) -> String {
        guard let value else { return "—" }
        return String(format: "%.\(digits)f%%", value * 100)
    }

    static func signedPercent(_ value: Double?, digits: Int = 1) -> String {
        guard let value else { return "—" }
        return String(format: "%+.\(digits)f%%", value)
    }

    static func odds(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.2f", value)
    }

    static func decimal(_ value: Double?, digits: Int = 3) -> String {
        guard let value else { return "—" }
        return String(format: "%.\(digits)f", value)
    }

    static func count(_ value: Int?) -> String {
        guard let value else { return "—" }
        return value.formatted()
    }

    static func count(_ value: Double?) -> String {
        guard let value else { return "—" }
        return Int(value.rounded()).formatted()
    }

    static func age(hours: Double?) -> String {
        guard let hours else { return "—" }
        if hours < 1 {
            return "\(Int((hours * 60).rounded())) min"
        }
        return String(format: "%.1f h", hours)
    }

    static func humanized(_ key: String) -> String {
        key.replacingOccurrences(of: "_", with: " ").capitalizedFirst
    }

    static func timestamp(_ iso: String?) -> String {
        guard let iso, !iso.isEmpty else { return "—" }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = parser.date(from: iso)
        if date == nil {
            parser.formatOptions = [.withInternetDateTime]
            date = parser.date(from: iso)
        }
        guard let date else { return iso }
        let display = DateFormatter()
        display.dateFormat = "d MMM · HH:mm"
        return display.string(from: date)
    }

    static func sizeLabel(_ key: String) -> String {
        let normalized = key.replacingOccurrences(of: "_", with: "").lowercased()
        if normalized.hasPrefix("2") { return "2-leg" }
        if normalized.hasPrefix("3") { return "3-leg" }
        if normalized.contains("total") || normalized.contains("both") { return "Both" }
        return key.replacingOccurrences(of: "_", with: " ")
    }

    static func sortedSizeKeys(_ rows: [String: ComboMetrics]) -> [String] {
        rows.keys.sorted { lhs, rhs in
            let left = Int(lhs.prefix(1)) ?? 9
            let right = Int(rhs.prefix(1)) ?? 9
            return left < right
        }
    }
}

extension String {
    var capitalizedFirst: String {
        guard let first else { return self }
        return first.uppercased() + dropFirst()
    }
}
