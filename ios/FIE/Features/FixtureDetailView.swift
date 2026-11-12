import SwiftUI

struct FixtureDetailView: View {
    let fixture: Fixture

    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL
    @State private var dossier: Dossier?
    @State private var loading = false
    @State private var errorMessage: String?

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                prices
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
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Palette.background.ignoresSafeArea())
        .navigationTitle("Match analysis")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadDossier() }
    }

    private var header: some View {
        Card {
            Eyebrow(text: fixture.competition)
            Text(fixture.matchLabel)
                .font(TypeScale.title)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                Text(fixture.kickoffEat ?? "—")
                Text("·")
                Text(fixture.country)
                Text("·")
                Text(fixture.status?.uppercased() ?? "—")
            }
            .font(TypeScale.caption)
            .foregroundStyle(Palette.textSecondary)
            HStack(spacing: 6) {
                Pill(text: fixture.source, tint: Palette.textSecondary)
                if let grade = fixture.dataQualityGrade {
                    Pill(text: "quality \(grade)", tint: Palette.accent)
                }
            }
        }
    }

    private var prices: some View {
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

    @MainActor
    private func loadDossier() async {
        guard !loading else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            dossier = try await client.get(
                "/fixtures/\(fixture.id)/deep-analysis",
                as: Dossier.self
            )
        } catch {
            errorMessage = "\(error.localizedDescription) The fixture details above still come from stored data."
        }
    }
}
