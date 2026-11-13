import SwiftUI

struct FixtureDetailView: View {
    private let fixture: Fixture?
    private let fixtureId: Int

    init(fixture: Fixture) {
        self.fixture = fixture
        self.fixtureId = fixture.id
    }

    init(fixtureId: Int) {
        self.fixture = nil
        self.fixtureId = fixtureId
    }

    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL
    @EnvironmentObject private var store: EngineStore
    @State private var dossier: Dossier?
    @State private var discovery: Discovery?
    @State private var liveHistory: LiveHistoryResponse?
    @State private var loading = false
    @State private var errorMessage: String?

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let info = headerInfo {
                    header(info)
                }
                if let fixture {
                    prices(fixture)
                }
                if let latest = liveHistory?.latest {
                    liveCard(latest)
                }
                if let history = liveHistory, !history.history.isEmpty {
                    liveHistoryCard(history)
                }
                if loading {
                    LoadingCard(label: "Building the match dossier…")
                }
                if let errorMessage {
                    NoteRow(text: errorMessage, tone: .warn)
                }
                if let dossier {
                    verdict(dossier)
                    modelCard(dossier)
                    marketsCard(dossier)
                    if !dossier.risks.isEmpty {
                        risksCard(dossier)
                    }
                    teamNewsCard(dossier)
                    learningCard(dossier)
                }
                if let discovery {
                    discoveryCard(discovery)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Palette.background.ignoresSafeArea())
        .navigationTitle("Match analysis")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            async let dossierLoad: Void = loadDossier()
            async let discoveryLoad: Void = loadDiscovery()
            async let liveLoad: Void = loadLiveHistory()
            _ = await (dossierLoad, discoveryLoad, liveLoad)
        }
    }

    private struct HeaderInfo {
        let competition: String
        let match: String
        let kickoff: String
        let country: String
        let status: String
        let source: String
        let qualityGrade: String?
    }

    private var headerInfo: HeaderInfo? {
        if let fixture {
            return HeaderInfo(
                competition: fixture.competition,
                match: fixture.matchLabel,
                kickoff: fixture.kickoffEat ?? "—",
                country: fixture.country,
                status: fixture.status?.uppercased() ?? "—",
                source: fixture.source,
                qualityGrade: fixture.dataQualityGrade
            )
        }
        guard let info = dossier?.fixture else { return nil }
        return HeaderInfo(
            competition: info.competition,
            match: info.match,
            kickoff: info.kickoffEat,
            country: info.country,
            status: info.status.uppercased(),
            source: info.source,
            qualityGrade: nil
        )
    }

    private func header(_ info: HeaderInfo) -> some View {
        Card {
            Eyebrow(text: info.competition)
            Text(info.match)
                .font(TypeScale.title)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            if let live = liveMatch {
                liveScoreLine(live)
            }
            HStack(spacing: 6) {
                Text(info.kickoff)
                Text("·")
                Text(info.country)
                Text("·")
                Text(info.status)
            }
            .font(TypeScale.caption)
            .foregroundStyle(Palette.textSecondary)
            if let iso = fixture?.kickoffUtc ?? dossier?.fixture.kickoffUtc {
                KickoffCountdown(iso: iso)
            }
            HStack(spacing: 6) {
                if !info.source.isEmpty {
                    Pill(text: info.source, tint: Palette.textSecondary)
                }
                if let grade = info.qualityGrade {
                    Pill(text: "quality \(grade)", tint: Palette.accent)
                }
            }
        }
    }

    private var liveMatch: Fixture? {
        store.live.first { $0.id == fixtureId }
    }

    @ViewBuilder
    private func liveScoreLine(_ live: Fixture) -> some View {
        if let home = live.homeScore, let away = live.awayScore {
            HStack(spacing: 8) {
                LiveDot()
                Text("\(home) – \(away)")
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.textPrimary)
                if let clock = live.liveClock {
                    Pill(text: clock, tint: Palette.danger)
                }
                Spacer(minLength: 0)
            }
        }
    }

    private func prices(_ fixture: Fixture) -> some View {
        Card {
            Eyebrow(text: "Bookmaker prices")
            HStack(spacing: 10) {
                priceTile(title: "Home", value: fixture.homeOdds)
                priceTile(title: "Draw", value: fixture.drawOdds)
                priceTile(title: "Away", value: fixture.awayOdds)
            }
            Text("Prices are the stored quotes attributed to the named feed; they are never mixed with estimates.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
        }
    }

    private func priceTile(title: String, value: Double?) -> some View {
        VStack(spacing: 4) {
            Text(title.uppercased())
                .font(TypeScale.statLabel)
                .tracking(0.6)
                .foregroundStyle(Palette.textTertiary)
            Text(Fmt.odds(value))
                .font(.system(size: 20, weight: .bold, design: .monospaced))
                .foregroundStyle(Palette.textPrimary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.white.opacity(0.05))
        )
    }

    @ViewBuilder
    private func liveCard(_ prediction: LivePrediction) -> some View {
        Card {
            HStack {
                Eyebrow(text: "Live forecast")
                Spacer()
                Text(prediction.freshness.uppercased())
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(prediction.freshness == "fresh" ? Palette.accent : Palette.textSecondary)
            }
            if let probabilities = prediction.probabilities {
                HStack(spacing: 16) {
                    Text("Home \(percentText(probabilities.homeWin))")
                    Text("Draw \(percentText(probabilities.draw))")
                    Text("Away \(percentText(probabilities.awayWin))")
                }
                .font(.footnote.monospacedDigit())
                .foregroundStyle(Palette.textPrimary)
            } else {
                Text("No forecast: \(prediction.status.replacingOccurrences(of: "_", with: " "))")
                    .font(.footnote)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Model estimate, not a price. Informational only.")
                .font(.caption2)
                .foregroundStyle(Palette.textTertiary)
        }
    }

    @ViewBuilder
    private func liveHistoryCard(_ history: LiveHistoryResponse) -> some View {
        Card {
            Eyebrow(text: "Settled snapshots")
            ForEach(history.history) { row in
                HStack {
                    Text("\(row.minute)'")
                        .font(.caption.monospacedDigit())
                        .frame(width: 44, alignment: .leading)
                    Text(snapshotScore(row))
                        .font(.caption.monospacedDigit())
                    Spacer()
                    Text(row.result?.replacingOccurrences(of: "_", with: " ") ?? "open")
                        .font(.caption)
                        .foregroundStyle(Palette.textSecondary)
                    if let brier = row.brier {
                        Text(brier, format: .number.precision(.fractionLength(3)))
                            .font(.caption.monospacedDigit())
                    }
                }
            }
        }
    }

    /// A missing probability is reported as missing, never as 0%.
    private func percentText(_ value: Double?) -> String {
        value.map { $0.formatted(.percent.precision(.fractionLength(1))) } ?? "—"
    }

    /// A snapshot taken without a score keeps the score empty; it is never 0 : 0.
    private func snapshotScore(_ row: LiveHistoryResponse.Snapshot) -> String {
        "\(row.score.home.map(String.init) ?? "–") : \(row.score.away.map(String.init) ?? "–")"
    }

    private func verdict(_ dossier: Dossier) -> some View {
        Card {
            Eyebrow(text: "Model verdict")
            Text(dossier.verdict.headline)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                Pill(text: "confidence \(dossier.verdict.confidence)", tint: Palette.accent)
                Pill(
                    text: dossier.verdict.modelValidated ? "model validated" : "model not validated",
                    tint: dossier.verdict.modelValidated ? Palette.accent : Palette.warn
                )
                Pill(
                    text: dossier.verdict.ratingsFresh ? "ratings fresh" : "ratings stale",
                    tint: dossier.verdict.ratingsFresh ? Palette.accent : Palette.warn
                )
            }
        }
    }

    @ViewBuilder
    private func modelCard(_ dossier: Dossier) -> some View {
        Card {
            Eyebrow(text: "Model")
            if let lambdas = dossier.model.lambdas, !lambdas.isEmpty {
                ForEach(lambdas.keys.sorted(), id: \.self) { key in
                    LabeledRow(
                        label: lambdaLabel(key),
                        value: Fmt.decimal(lambdas[key], digits: 2),
                        mono: true
                    )
                }
            }
            if let source = dossier.model.lambdaSource {
                LabeledRow(label: "Goal source", value: source)
            }
            if !dossier.model.correctScoreTop5.isEmpty {
                DividerLine()
                Eyebrow(text: "Most likely scorelines")
                HStack(spacing: 8) {
                    ForEach(dossier.model.correctScoreTop5.prefix(5)) { row in
                        VStack(spacing: 3) {
                            Text(row.score)
                                .font(.system(size: 14, weight: .bold, design: .monospaced))
                                .foregroundStyle(Palette.textPrimary)
                            Text(Fmt.percent(row.probability))
                                .font(TypeScale.caption)
                                .foregroundStyle(Palette.textSecondary)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                        .background(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(Color.white.opacity(0.05))
                        )
                    }
                }
            }
        }
    }

    private func lambdaLabel(_ key: String) -> String {
        let normalized = key.lowercased()
        if normalized.contains("home") { return "Home expected goals" }
        if normalized.contains("away") { return "Away expected goals" }
        return "Expected goals"
    }

    private func marketsCard(_ dossier: Dossier) -> some View {
        let rows = Array(
            dossier.markets
                .sorted { $0.modelProbability > $1.modelProbability }
                .prefix(10)
        )
        return Card {
            Eyebrow(text: "Priced markets")
            if rows.isEmpty {
                Text("No priced market was available for this fixture.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
            } else {
                ForEach(rows) { market in
                    DividerLine()
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(market.selection)
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.textPrimary)
                            if market.isValue {
                                Pill(text: "value")
                            }
                            Spacer(minLength: 8)
                            Text(Fmt.odds(market.price))
                                .font(.system(size: 15, weight: .bold, design: .monospaced))
                                .foregroundStyle(Palette.accent)
                        }
                        Text("\(market.marketType) · fair \(Fmt.odds(market.fairOdds))")
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textSecondary)
                        HStack(spacing: 12) {
                            Text("model \(Fmt.percent(market.calibratedProbability))")
                            Text("edge \(Fmt.signedPercent(market.edgePercent, digits: 2))")
                            Text("EV \(Fmt.signedPercent(market.expectedValuePercent, digits: 2))")
                        }
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                    }
                }
                Text("Model probabilities are advisory until walk-forward calibration shows the model beating the market. A value flag is not a staking instruction.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func risksCard(_ dossier: Dossier) -> some View {
        Card {
            Eyebrow(text: "Risks")
            ForEach(dossier.risks) { risk in
                HStack(alignment: .top, spacing: 8) {
                    Pill(
                        text: risk.severity,
                        tint: risk.severity.lowercased() == "high" ? Palette.danger : Palette.warn
                    )
                    Text(risk.detail)
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func teamNewsCard(_ dossier: Dossier) -> some View {
        Card {
            HStack {
                Eyebrow(text: "Team news")
                Spacer()
                Pill(
                    text: dossier.teamNews.advisory ? "advisory" : dossier.teamNews.badge,
                    tint: dossier.teamNews.advisory ? Palette.warn : Palette.textSecondary
                )
            }
            Text(dossier.teamNews.injuries)
                .font(TypeScale.body)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            LabeledRow(label: "Form", value: dossier.teamNews.formSummary)
            LabeledRow(label: "Head to head", value: dossier.teamNews.h2hSummary)
            Text(dossier.teamNews.riskNote)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            Text("Squad news is advisory and sourced, never invented.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
        }
    }

    private func learningCard(_ dossier: Dossier) -> some View {
        Card {
            Eyebrow(text: "Calibration")
            if dossier.learning.active {
                Text("Learning is active. Markets calibrated on this fixture: \(dossier.learning.calibratedMarketsHere.isEmpty ? "none" : dossier.learning.calibratedMarketsHere.joined(separator: ", ")).")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("No calibration is active for these markets; raw probabilities are shown.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
            }
        }
    }

    private func discoveryCard(_ discovery: Discovery) -> some View {
        let rows = discovery.markets.sorted { $0.price > $1.price }
        return Card {
            HStack {
                Eyebrow(text: "Discovered markets")
                Spacer()
                Pill(
                    text: discovery.dataStatus,
                    tint: discovery.dataStatus.lowercased() == "priced" ? Palette.accent : Palette.textSecondary
                )
            }
            HStack(spacing: 6) {
                Pill(text: "\(discovery.discoveredCount) priced", tint: Palette.accent)
                Pill(text: "\(discovery.familyCount) families", tint: Palette.textSecondary)
                if discovery.unmodelledCount > 0 {
                    Pill(text: "\(discovery.unmodelledCount) unmodelled", tint: Palette.warn)
                }
                Spacer(minLength: 0)
            }
            if rows.isEmpty {
                Text("No priced market has been captured for this fixture. The engine reports that instead of inventing markets.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ForEach(rows.prefix(8)) { market in
                    DividerLine()
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(market.title)
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.textPrimary)
                            Spacer(minLength: 8)
                            Text(Fmt.odds(market.price))
                                .font(.system(size: 15, weight: .bold, design: .monospaced))
                                .foregroundStyle(Palette.accent)
                        }
                        Text(
                            "\(market.family ?? market.category ?? "market") · "
                            + "\(market.bookmaker ?? market.source)\(captureAge(market.capturedAt))"
                        )
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                    }
                }
                Text("Prices come from the feed that priced them, with their capture time. Unmodelled markets are reported, never analysed.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func captureAge(_ iso: String?) -> String {
        guard let date = Fmt.date(iso) else { return "" }
        let hours = Date().timeIntervalSince(date) / 3600
        return " · \(Fmt.age(hours: hours)) old"
    }

    @MainActor
    private func loadLiveHistory() async {
        do {
            liveHistory = try await client.get(
                "/fixtures/\(fixtureId)/live",
                timeout: 20,
                as: LiveHistoryResponse.self
            )
        } catch {
            // No live row for this fixture is a normal answer, not an error to shout about.
            liveHistory = nil
        }
    }

    @MainActor
    private func loadDiscovery() async {
        do {
            discovery = try await client.get(
                "/markets/fixtures/\(fixtureId)/discovery",
                timeout: 30,
                as: Discovery.self
            )
        } catch {
            // The dossier error already explains an unreachable engine.
            discovery = nil
        }
    }

    @MainActor
    private func loadDossier() async {
        guard !loading else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            dossier = try await client.get(
                "/fixtures/\(fixtureId)/deep-analysis",
                as: Dossier.self
            )
        } catch {
            errorMessage = fixture == nil
                ? error.localizedDescription
                : "\(error.localizedDescription) The fixture details above still come from stored data."
        }
    }
}
