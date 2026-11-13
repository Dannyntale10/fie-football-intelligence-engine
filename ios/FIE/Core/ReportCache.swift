import Foundation

struct CachedDailyReport {
    let report: DailyComboReport
    let savedAt: Date
}

/// Stores the last daily report so the slate survives a cold launch or an
/// unreachable engine. Reports from an earlier matchday are discarded rather
/// than shown as if they were today's.
enum ReportCache {
    private struct Stored: Codable {
        let savedAt: Date
        let report: DailyComboReport
    }

    private static var fileURL: URL? {
        guard
            let base = FileManager.default.urls(
                for: .applicationSupportDirectory,
                in: .userDomainMask
            ).first
        else { return nil }
        let folder = base.appendingPathComponent("FIE", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder.appendingPathComponent("daily-report.json")
    }

    private static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }

    private static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    static func save(_ report: DailyComboReport, at date: Date = Date()) {
        guard let url = fileURL else { return }
        let stored = Stored(savedAt: date, report: report)
        guard let data = try? encoder.encode(stored) else { return }
        try? data.write(to: url, options: .atomic)
    }

    static func load(now: Date = Date()) -> CachedDailyReport? {
        guard
            let url = fileURL,
            let data = try? Data(contentsOf: url),
            let stored = try? decoder.decode(Stored.self, from: data)
        else { return nil }
        guard stored.report.targetDate == Fmt.eatDay(now) else {
            try? FileManager.default.removeItem(at: url)
            return nil
        }
        return CachedDailyReport(report: stored.report, savedAt: stored.savedAt)
    }

    static func clear() {
        guard let url = fileURL else { return }
        try? FileManager.default.removeItem(at: url)
    }
}

enum SlipBuilder {
    static func text(combo: DailyCombo, report: DailyComboReport) -> String {
        var lines = [
            "FIE · \(combo.size)-leg combo · \(Fmt.percent(combo.combinedProbability)) win probability",
            "\(Fmt.odds(combo.totalOdds)) total odds · \(report.matchdayLabel)",
            "",
        ]

        for (index, leg) in combo.legs.enumerated() {
            var line = "\(index + 1). \(leg.match) — \(leg.selection) @ \(Fmt.odds(leg.odds)) (\(Fmt.percent(leg.probability)))"
            if let bookmaker = leg.bookmaker { line += " · \(bookmaker)" }
            if let captured = leg.capturedAt { line += " · captured \(Fmt.timestamp(captured))" }
            lines.append(line)
            lines.append("   \(leg.competition) · kickoff \(leg.kickoffEat)")
        }

        lines.append("")
        if let implied = combo.impliedProbability {
            lines.append("De-vigged market probability: \(Fmt.percent(implied))")
        }
        if let ev = combo.expectedValuePercent {
            lines.append("Expected value at these prices: \(Fmt.signedPercent(ev, digits: 2))")
        }
        lines.append("")
        lines.append("Paper only. Prices are as captured from the named bookmaker; verify them before staking.")
        lines.append("Published selection gate remains \(Int((report.tier.publishedGate * 100).rounded()))%; it is never lowered to fill a report.")
        return lines.joined(separator: "\n")
    }
}
