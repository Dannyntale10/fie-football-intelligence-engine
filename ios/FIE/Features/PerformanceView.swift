import SwiftUI

struct PerformanceView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var health: Health?
    @State private var summary: PerformanceSummary?
    @State private var loading = false
    @State private var errorMessage: String?

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    intro
                    if let errorMessage {
                        NoteRow(text: errorMessage, tone: .danger)
                    }
                    if loading && health == nil && summary == nil {
                        LoadingCard(label: "Reading the ledger…")
                    }
                    if let health {
                        engineCard(health)
                    }
                    if let summary {
                        ledgerCard(summary)
                        slipTypeCard(summary)
                        Text(summary.note)
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Performance")
            .task { await load() }
            .refreshable { await load() }
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "Measured, not projected")
            Text("Honest ledger")
                .font(TypeScale.title)
                .foregroundStyle(Palette.textPrimary)
            Text("Real settled slips only; synthetic test data is excluded. Paper results while the model is uncalibrated — no profit claim is made.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func engineCard(_ health: Health) -> some View {
        Card {
            HStack {
                Eyebrow(text: "Engine")
                Spacer()
                Pill(text: health.status, tint: health.status == "ok" ? Palette.accent : Palette.danger)
            }
            LabeledRow(label: "Database", value: health.database)
            LabeledRow(label: "Environment", value: health.environment)
            LabeledRow(label: "Fixtures tracked", value: Fmt.count(health.totalFixtures))
            LabeledRow(label: "Published gate", value: Fmt.percent(health.minPredictionProbability, digits: 0), mono: true)
        }
    }

    private func ledgerCard(_ summary: PerformanceSummary) -> some View {
        Card {
            Eyebrow(text: "Real settled slips")
            HStack(spacing: 12) {
                StatTile(label: "Settled", value: Fmt.count(summary.settled))
                StatTile(
                    label: "Hit rate",
                    value: Fmt.percent(summary.accuracy),
                    caption: "\(summary.wins)W · \(summary.losses)L · \(summary.voids)V"
                )
            }
            HStack(spacing: 12) {
                StatTile(
                    label: "ROI",
                    value: Fmt.signedPercent((summary.roi ?? 0) * 100, digits: 2),
                    caption: "Profit \(Fmt.decimal(summary.profit, digits: 2))"
                )
                StatTile(label: "Stake", value: Fmt.decimal(summary.stake, digits: 2))
            }
            DividerLine()
            LabeledRow(label: "Bets recorded", value: "\(summary.bets)")
            LabeledRow(label: "Pending", value: "\(summary.pending)")
            LabeledRow(label: "Scope", value: summary.scope)
        }
    }

    @ViewBuilder
    private func slipTypeCard(_ summary: PerformanceSummary) -> some View {
        let rows = summary.bySlipType
            .map { SlipTypeRow(name: $0.key, bucket: $0.value) }
            .sorted { $0.name < $1.name }
        if !rows.isEmpty {
            Card {
                Eyebrow(text: "By slip type")
                ForEach(rows) { row in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.name.replacingOccurrences(of: "_", with: " ").capitalizedFirst)
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.textPrimary)
                            Text("\(row.bucket.bets) settled · \(Fmt.percent(row.bucket.accuracy)) hit")
                                .font(TypeScale.caption)
                                .foregroundStyle(Palette.textSecondary)
                        }
                        Spacer()
                        Text(Fmt.signedPercent((row.bucket.roi ?? 0) * 100, digits: 2))
                            .font(.system(size: 14, weight: .bold, design: .monospaced))
                            .foregroundStyle((row.bucket.roi ?? 0) >= 0 ? Palette.accent : Palette.danger)
                    }
                }
            }
        }
    }

    @MainActor
    private func load() async {
        guard !loading else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            async let healthTask: Health = client.get("/health", as: Health.self)
            async let summaryTask: PerformanceSummary = client.get("/performance/summary", as: PerformanceSummary.self)
            let (loadedHealth, loadedSummary) = try await (healthTask, summaryTask)
            health = loadedHealth
            summary = loadedSummary
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct SlipTypeRow: Identifiable {
    let name: String
    let bucket: SlipBucket

    var id: String { name }
}
