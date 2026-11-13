import Foundation
import SwiftUI

enum EngineSection: String, CaseIterable, Identifiable {
    case health, report, fixtures, gate, summary, clv, scorecard, learning, sweep, backtest, sources, live

    var id: String { rawValue }

    var title: String {
        switch self {
        case .health: return "Engine"
        case .report: return "Daily report"
        case .fixtures: return "Fixtures"
        case .gate: return "85% gate"
        case .summary: return "Ledger"
        case .clv: return "Closing line"
        case .scorecard: return "Weekly scorecard"
        case .learning: return "Learning"
        case .sweep: return "Tier sweep"
        case .backtest: return "Historical replay"
        case .sources: return "Sources"
        case .live: return "Live scores"
        }
    }

    static func forTab(_ index: Int) -> EngineSection {
        switch index {
        case 0: return .report
        case 1: return .fixtures
        case 2: return .summary
        case 3: return .live
        default: return .health
        }
    }
}

@MainActor
final class EngineStore: ObservableObject {
    @Published private(set) var report: DailyComboReport?
    @Published private(set) var fixtures: [Fixture] = []
    @Published private(set) var gate: GateReport?
    @Published private(set) var health: Health?
    @Published private(set) var summary: PerformanceSummary?
    @Published private(set) var clv: ClvSummary?
    @Published private(set) var scorecard: Scorecard?
    @Published private(set) var learning: LearningSummary?
    @Published private(set) var sweep: ComboSweep?
    @Published private(set) var sources: SourcesResponse?
    @Published private(set) var backtest: ComboBacktestReport?
    @Published private(set) var live: [Fixture] = []
    @Published private(set) var liveUnreadable = 0
    @Published private(set) var errors: [EngineSection: String] = [:]
    @Published private(set) var ready: Set<EngineSection> = []
    @Published private(set) var isBootstrapped = false
    @Published private(set) var isRefreshing = false
    @Published private(set) var lastUpdated: Date?
    @Published private(set) var livePaused = false
    @Published private(set) var cachedAt: Date?

    private var autoTask: Task<Void, Never>?
    private var activeSection: EngineSection = .report

    var readyCount: Int { ready.count }
    var isAutoTicking: Bool { autoTask != nil }

    private var client: APIClient {
        let raw = UserDefaults.standard.string(forKey: AppSettings.serverURLKey)
            ?? AppSettings.defaultServerURL
        return APIClient(baseURL: URL(string: raw) ?? URL(string: AppSettings.defaultServerURL)!)
    }

    func bootstrap() async {
        guard !isBootstrapped, !isRefreshing else { return }

        if report == nil, let cached = ReportCache.load() {
            report = cached.report
            cachedAt = cached.savedAt
        }

        isRefreshing = true
        await withTaskGroup(of: Void.self) { group in
            for section in EngineSection.allCases {
                group.addTask { await self.load(section) }
            }
        }
        isRefreshing = false
        isBootstrapped = true
        lastUpdated = Date()
    }

    func refreshAll() async {
        isRefreshing = true
        await withTaskGroup(of: Void.self) { group in
            for section in EngineSection.allCases {
                group.addTask { await self.load(section) }
            }
        }
        isRefreshing = false
        lastUpdated = Date()
    }

    func refresh(section: EngineSection) async {
        await load(section)
        lastUpdated = Date()
    }

    func refreshLive() async {
        do {
            let response = try await client.get(
                "/fixtures/live",
                timeout: 20,
                as: LiveFixturesResponse.self
            )
            live = response.fixtures
            liveUnreadable = response.unreadable
            livePaused = false
            errors.removeValue(forKey: .live)
            lastUpdated = Date()
        } catch {
            livePaused = true
            errors[.live] = error.localizedDescription
        }
    }

    func refreshFeeds() async throws -> SourcesRefreshResult {
        let result = try await client.post(
            "/sources/refresh",
            timeout: 900,
            as: SourcesRefreshResult.self
        )
        await refresh(section: .report)
        await refresh(section: .fixtures)
        await refresh(section: .live)
        await refresh(section: .sources)
        return result
    }

    func runBacktest() async throws -> ComboBacktestReport {
        let result = try await client.post(
            "/combos/backtest",
            timeout: 900,
            as: ComboBacktestReport.self
        )
        backtest = result
        errors.removeValue(forKey: .backtest)
        return result
    }

    func startAutoRefresh(section: EngineSection) {
        setActiveSection(section)
        guard autoTask == nil else { return }
        autoTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 60_000_000_000)
                if Task.isCancelled { return }
                guard let self else { return }
                await self.refreshLive()
                await self.refresh(section: self.activeSection)
            }
        }
    }

    func stopAutoRefresh() {
        autoTask?.cancel()
        autoTask = nil
    }

    func setActiveSection(_ section: EngineSection) {
        activeSection = section
    }

    func reset() {
        stopAutoRefresh()
        report = nil
        fixtures = []
        gate = nil
        health = nil
        summary = nil
        clv = nil
        scorecard = nil
        learning = nil
        sweep = nil
        sources = nil
        backtest = nil
        live = []
        liveUnreadable = 0
        errors = [:]
        ready = []
        isBootstrapped = false
        isRefreshing = false
        lastUpdated = nil
        livePaused = false
        cachedAt = nil
        ReportCache.clear()
    }

    private func load(_ section: EngineSection) async {
        do {
            switch section {
            case .health:
                health = try await client.get("/health", timeout: 15, as: Health.self)
            case .report:
                let fresh = try await client.get("/combos/daily", as: DailyComboReport.self)
                report = fresh
                cachedAt = nil
                ReportCache.save(fresh)
            case .fixtures:
                fixtures = try await client.get(
                    "/fixtures",
                    query: [
                        URLQueryItem(name: "upcoming_only", value: "true"),
                        URLQueryItem(name: "limit", value: "120"),
                    ],
                    as: [Fixture].self
                )
            case .gate:
                gate = try await client.get(
                    "/markets/qualifying",
                    timeout: 120,
                    as: GateReport.self
                )
            case .summary:
                summary = try await client.get(
                    "/performance/summary",
                    as: PerformanceSummary.self
                )
            case .clv:
                clv = try await client.get(
                    "/evaluation/clv",
                    query: [URLQueryItem(name: "days", value: "90")],
                    as: ClvSummary.self
                )
            case .scorecard:
                scorecard = try await client.get(
                    "/evaluation/scorecard",
                    query: [URLQueryItem(name: "days", value: "7")],
                    as: Scorecard.self
                )
            case .learning:
                learning = try await client.get("/learning/summary", as: LearningSummary.self)
            case .sweep:
                sweep = try await client.get(
                    "/combos/backtest/sweep",
                    timeout: 120,
                    as: ComboSweep.self
                )
            case .backtest:
                backtest = try await client.get("/combos/backtest", as: ComboBacktestReport.self)
            case .sources:
                sources = try await client.get("/sources", as: SourcesResponse.self)
            case .live:
                let response = try await client.get(
                    "/fixtures/live",
                    timeout: 20,
                    as: LiveFixturesResponse.self
                )
                live = response.fixtures
                liveUnreadable = response.unreadable
                livePaused = false
            }
            errors.removeValue(forKey: section)
        } catch let error as APIError where
            section == .backtest && error.message.contains("No combo backtest yet") {
            errors.removeValue(forKey: .backtest)
        } catch {
            errors[section] = error.localizedDescription
            if section == .live { livePaused = true }
        }
        ready.insert(section)
    }
}
