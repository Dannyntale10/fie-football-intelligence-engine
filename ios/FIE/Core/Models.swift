import Foundation

// MARK: - System

struct Health: Decodable {
    let status: String
    let app: String
    let environment: String
    let database: String
    let totalFixtures: Int
    let minPredictionProbability: Double
}

struct QualifiedPolicy: Decodable {
    let minimumProbability: Double
    let minimumConfidence: Double
    let messageWhenEmpty: String
    let probabilitiesAndConfidenceAreSeparate: Bool
    let neverLoweredToFillAReport: Bool
}

// MARK: - Fixtures and predictions

struct Fixture: Decodable, Identifiable {
    let id: Int
    let kickoffUtc: String?
    let kickoffEat: String?
    let homeTeam: String
    let awayTeam: String
    let competition: String
    let country: String
    let source: String
    let externalFixtureId: String?
    let homeOdds: Double?
    let drawOdds: Double?
    let awayOdds: Double?
    let homeScore: Int?
    let awayScore: Int?
    let liveClock: String?
    let status: String?
    let dataQualityScore: Double?
    let dataQualityGrade: String?
    let eligibleForAnalysis: Bool?
    let livePrediction: LivePrediction?

    var matchLabel: String { "\(homeTeam) vs \(awayTeam)" }
}

struct LiveFixturesResponse: Decodable {
    let asOf: String?
    let refreshed: Bool
    let warning: String?
    let fixtures: [Fixture]
    /// Rows the decoder could not read. The board reports the count instead of
    /// silently hiding them.
    let unreadable: Int

    private struct LossyFixture: Decodable {
        let fixture: Fixture?

        init(from decoder: Decoder) throws {
            fixture = try? Fixture(from: decoder)
        }
    }

    private enum CodingKeys: String, CodingKey {
        case asOf, refreshed, warning, fixtures
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        asOf = try container.decodeIfPresent(String.self, forKey: .asOf)
        refreshed = try container.decode(Bool.self, forKey: .refreshed)
        warning = try container.decodeIfPresent(String.self, forKey: .warning)
        let rows = try container.decode([LossyFixture].self, forKey: .fixtures)
        fixtures = rows.compactMap(\.fixture)
        unreadable = rows.count - fixtures.count
    }
}

struct LivePrediction: Decodable {
    struct Probabilities: Decodable {
        let homeWin: Double?
        let draw: Double?
        let awayWin: Double?
    }

    struct Score: Decodable {
        let home: Int?
        let away: Int?
    }

    struct LambdaPair: Decodable {
        let home: Double?
        let away: Double?
    }

    let status: String
    let observedAt: String?
    let minute: Int
    let freshness: String
    let score: Score
    let remainingMinutes: Int
    let probabilities: Probabilities?
    let prematchProbabilities: Probabilities?
    let lambda: LambdaPair
    let remainingLambda: LambdaPair
    let intensity: Double?
    let intensitySource: String?
    let lambdaSource: String?
    let modelVersion: String
}

struct LiveHistoryResponse: Decodable {
    struct Snapshot: Decodable, Identifiable {
        let minute: Int
        let minuteBucket: Int
        let observedAt: String?
        let score: LivePrediction.Score
        let probabilities: LivePrediction.Probabilities?
        let result: String?
        let extraHomeGoals: Int?
        let extraAwayGoals: Int?
        let brier: Double?
        let logLoss: Double?
        let topPickCorrect: Bool?

        var id: Int { minuteBucket }
    }

    let fixtureId: Int
    let latest: LivePrediction?
    let history: [Snapshot]
}

struct PredictionList: Decodable {
    let count: Int
    let predictions: [Prediction]
}

struct Prediction: Decodable, Identifiable {
    let id: Int
    let fixtureId: Int?
    let family: String
    let selection: String
    let line: Double?
    let probability: Double?
    let confidence: Double?
    let fairOdds: Double?
    let ev: Double?
    let qualified: Bool
    let qualificationReason: String?
    let createdAt: String?
}

// MARK: - Performance

struct PerformanceSummary: Decodable {
    let scope: String
    let periodDays: Int?
    let bets: Int
    let settled: Int
    let pending: Int
    let wins: Int
    let losses: Int
    let voids: Int
    let accuracy: Double?
    let stake: Double
    let profit: Double
    let roi: Double?
    let yield: Double?
    let bySlipType: [String: SlipBucket]
    let note: String
}

struct SlipBucket: Decodable {
    let bets: Int
    let wins: Int
    let losses: Int
    let voids: Int
    let stake: Double
    let profit: Double
    let roi: Double?
    let accuracy: Double?
}

// MARK: - Sources

struct SourceEntry: Decodable, Identifiable {
    let name: String
    let keyEnv: String?
    let freePlan: String?
    let dailyQuota: Int?
    let configured: Bool
    let status: String?
    let quotaUsed: Int?
    let lastSyncAt: String?
    let notes: String?

    var id: String { name }
}

struct SourcesResponse: Decodable {
    let providers: [SourceEntry]
    let configuredCount: Int
    let note: String
}

struct FeedStatus: Decodable {
    let provider: String?
    let label: String?
    let status: String?
    let error: String?

    var isFailed: Bool { (status ?? "").lowercased() == "failed" }
}

struct RefreshTotals: Decodable {
    let fetchedEvents: Int?
    let matchedExisting: Int?
    let oddsUpdated: Int?
    let insertedNew: Int?
}

struct SourcesRefreshResult: Decodable {
    let feeds: [String: FeedStatus]
    let totals: RefreshTotals
    let syncedAt: String?
}

struct SyncLog: Decodable, Identifiable {
    let id: Int
    let providerId: Int?
    let entity: String
    let status: String
    let recordsWritten: Int
    let requestCount: Int
    let startedAt: String?
    let finishedAt: String?
    let message: String?
}

// MARK: - Daily combos

struct DailyComboLeg: Codable, Identifiable {
    let fixtureId: Int
    let match: String
    let competition: String
    let kickoffUtc: String?
    let kickoffEat: String
    let marketKey: String
    let marketType: String
    let selection: String
    let odds: Double
    let probability: Double
    let marketProbability: Double
    let calibrated: Bool
    let bookmaker: String?
    let source: String?
    let sourceUrl: String?
    let capturedAt: String?
    let priceAgeHours: Double?

    var id: Int { fixtureId }
}

struct DailyCombo: Codable {
    let size: Int
    let legs: [DailyComboLeg]
    let combinedProbability: Double
    let totalOdds: Double
    let impliedProbability: Double?
    let expectedValuePercent: Double?
    let minLegProbability: Double
}

struct DailyComboReport: Codable {
    let targetDate: String
    let matchdayLabel: String
    let generatedAt: String
    let params: Params
    let tier: Tier
    let scope: Scope
    let learning: Learning
    let legs: [DailyComboLeg]
    let combos: [String: [DailyCombo]]
    let best: DailyCombo?
    /// Disjoint-first slips from the engine; absent on reports cached before it
    /// shipped, so the viewer falls back to the single best combo.
    let topCombos: [DailyCombo]?
    let staleLegs: [StaleLeg]
    let message: String
    let note: String

    var slips: [DailyCombo] {
        if let topCombos, !topCombos.isEmpty { return topCombos }
        return best.map { [$0] } ?? []
    }

    struct Params: Codable {
        let minLegProbability: Double
        let comboSizes: [Int]
        let maxPriceAgeHours: Double?
        let competition: String?
    }

    struct Tier: Codable {
        let label: String
        let probabilitySource: String
        let publishedGate: Double
        let belowPublishedGate: Bool
        let note: String
    }

    struct Scope: Codable {
        let fixturesOnSlate: Int
        let fixturesPriced: Int
        let qualifiedLegs: Int
        let candidateLegs: Int
        let combosConsidered: Int
        let rejected: [String: Int]
    }

    struct Learning: Codable {
        let applied: Bool
        let a: Double?
        let b: Double?
        let n: Int?
        let source: String?
    }
}

struct StaleLeg: Codable {
    let match: String
    let priceAgeHours: Double
}

// MARK: - Historical replay

struct ComboMetrics: Decodable {
    let count: Int
    let wins: Int?
    let losses: Int?
    let hitRate: Double?
    let averagePredictedProbability: Double?
    let calibrationGap: Double?
    let averageOdds: Double?
    let brier: Double?
    let logLoss: Double?
    let totalStaked: Double?
    let totalReturned: Double?
    let profit: Double?
    let roiPercent: Double?
}

struct ComboBuckets: Decodable {
    let all: [String: ComboMetrics]?
    let train: [String: ComboMetrics]?
    let holdout: [String: ComboMetrics]?
}

struct BacktestWindow: Decodable {
    let firstMatchday: String?
    let lastMatchday: String?
    let matchdays: Int
    let settledMatches: Int
}

struct BacktestScope: Decodable {
    let settledFixtures: Int
    let qualifiedLegs: Int?
    let combos: Int?
    let skipped: [String: Int]?
}

struct BacktestParams: Decodable {
    let minLegProbability: Double?
    let comboSizes: [Int]?
    let stake: Double?
    let priceMode: String?
    let combosPerSize: Int?
    let holdoutShare: Double?
}

struct BacktestLearning: Decodable {
    let applied: Bool
    let a: Double?
    let b: Double?
    let n: Int?
    let nTrain: Int?
    let nValidation: Int?
    let loglossBefore: Double?
    let loglossAfter: Double?
    let improvement: Double?
    let reason: String?
}

struct HoldoutFit: Decodable {
    let applied: Bool
    let a: Double?
    let b: Double?
    let n: Int?
    let improvement: Double?
    let reason: String?
}

struct ComboBacktestReport: Decodable {
    let version: String
    let generatedAt: String
    let status: String
    let message: String?
    let window: BacktestWindow?
    let params: BacktestParams?
    let scope: BacktestScope?
    let combos: ComboBuckets?
    let learning: BacktestLearning?
    let holdoutAfterLearning: [String: ComboMetrics]?
    let holdoutFit: HoldoutFit?
    let notes: [String]?
}

// MARK: - Match dossier

struct Dossier: Decodable {
    let version: String
    let generatedAt: String
    let fixture: FixtureInfo
    let verdict: Verdict
    let model: ModelInfo
    let markets: [DossierMarket]
    let risks: [Risk]
    let teamNews: TeamNews
    let learning: LearningInfo

    struct FixtureInfo: Decodable {
        let id: Int
        let match: String
        let homeTeam: String
        let awayTeam: String
        let competition: String
        let country: String
        let kickoffEat: String
        let kickoffUtc: String?
        let status: String
        let source: String
    }

    struct Verdict: Decodable {
        let headline: String
        let confidence: String
        let modelValidated: Bool
        let ratingsFresh: Bool
    }

    struct ModelInfo: Decodable {
        let lambdas: [String: Double]?
        let lambdaSource: String?
        let correctScoreTop5: [ScoreProbability]
    }

    struct ScoreProbability: Decodable, Identifiable {
        let score: String
        let probability: Double

        var id: String { score }
    }

    struct DossierMarket: Decodable, Identifiable {
        let marketKey: String
        let marketType: String
        let selection: String
        let team: String?
        let price: Double
        let modelProbability: Double
        let calibratedProbability: Double
        let calibrationApplied: Bool
        let fairOdds: Double
        let edgePercent: Double
        let expectedValuePercent: Double
        let isValue: Bool

        var id: String { "\(marketKey)-\(selection)" }
    }

    struct Risk: Decodable, Identifiable {
        let code: String
        let severity: String
        let detail: String

        var id: String { "\(code)-\(detail)" }
    }

    struct TeamNews: Decodable {
        let verdict: String
        let source: String
        let advisory: Bool
        let badge: String
        let injuries: String
        let formSummary: String
        let h2hSummary: String
        let riskNote: String
    }

    struct LearningInfo: Decodable {
        let active: Bool
        let version: String?
        let appliedMarkets: [String]
        let calibratedMarketsHere: [String]
    }
}

// MARK: - Market discovery

struct Discovery: Decodable {
    let fixtureId: Int?
    let source: String
    let capturedAt: String?
    let dataStatus: String
    let discoveredCount: Int
    let familyCount: Int
    let unmodelledCount: Int
    let markets: [DiscoveredMarket]
}

struct DiscoveredMarket: Decodable, Identifiable {
    let family: String?
    let category: String?
    let label: String?
    let selection: String?
    let line: Double?
    let price: Double
    let bookmaker: String?
    let source: String
    let capturedAt: String?

    var id: String { "\(label ?? "")-\(selection ?? "")-\(bookmaker ?? "")-\(price)" }
    var title: String { label ?? selection ?? "Market" }
}

// MARK: - Gate

struct GateReport: Decodable {
    let targetDate: String
    let threshold: Double
    let fixturesScanned: Int
    let count: Int
    let qualifying: [GateSelection]
    let message: String
}

struct GateSelection: Decodable, Identifiable {
    let fixtureId: Int
    let match: String
    let competition: String
    let kickoffUtc: String?
    let kickoffEat: String
    let market: String
    let probability: Double

    var id: String { "\(fixtureId)-\(market)" }
}

// MARK: - Closing-line value and learning

struct ClvSummary: Decodable {
    let nSelections: Int
    let meanClvPercent: Double?
    let beatClosePercent: Double?
    let byMarket: [String: ClvMarket]?
    let recent: [ClvRow]?
    let note: String?

    struct ClvMarket: Decodable {
        let n: Int
        let meanClvPercent: Double?
    }

    struct ClvRow: Decodable, Identifiable {
        let fixtureId: Int
        let marketKey: String
        let selectionPrice: Double
        let closingPrice: Double
        let clvPercent: Double
        let beatClose: Bool

        var id: String { "\(fixtureId)-\(marketKey)-\(selectionPrice)" }
    }
}

struct LearningSummary: Decodable {
    let trained: Bool
    let version: String?
    let trainedAt: String?
    let sources: Evidence?
    let appliedMarkets: [String]?
    let edgePremiums: [String: EdgePremium]?
    let notes: [String]?
    let note: String?

    struct Evidence: Decodable {
        let settledLegs: Int?
        let walkforwardMatches: Int?
    }

    struct EdgePremium: Decodable {
        let premium: Double
        let n: Int?
    }
}

// MARK: - Delivery

struct DeliveryNotification: Decodable, Identifiable {
    let id: Int
    let channel: String
    let recipient: String?
    let subject: String?
    let body: String
    let status: String
    let relatedType: String?
    let relatedId: Int?
    let sentAt: String?
    let createdAt: String?
}

// MARK: - Tier sweep

struct ComboSweep: Decodable {
    let version: String?
    let status: String?
    let window: SweepWindow?
    let params: SweepParams?
    let rows: [SweepTier]?
    let recommendation: SweepRecommendation?
}

struct SweepWindow: Decodable {
    let matchdays: Int?
    let settledMatches: Int?
    let firstMatchday: String?
    let lastMatchday: String?
}

struct SweepParams: Decodable {
    let tiers: [Double]?
    let publishedDefaultTier: Double?
}

struct SweepTier: Decodable, Identifiable {
    let tier: Double
    let combos: [String: [String: SweepMetric]]?

    var id: Double { tier }
    var train: SweepMetric? { combos?["train"]?["total"] }
    var holdout: SweepMetric? { combos?["holdout"]?["total"] }
}

struct SweepMetric: Decodable {
    let count: Int?
    let hitRate: Double?
    let averagePredictedProbability: Double?
    let requiredHitRate: Double?
    let roiPercent: Double?
}

struct SweepRecommendation: Decodable {
    let tier: Double?
    let action: String?
    let matchesPublishedDefault: Bool?
    let holdoutConfirmed: Bool?
    let guidance: String?
    let note: String?
    let train: SweepMetric?
    let holdout: SweepMetric?
}
