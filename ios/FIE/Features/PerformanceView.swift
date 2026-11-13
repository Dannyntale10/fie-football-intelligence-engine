import SwiftUI

struct PerformanceView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var health: Health?
    @State private var summary: PerformanceSummary?
    @State private var clv: ClvSummary?
    @State private var learning: LearningSummary?
    @State private var sweep: ComboSweep?
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
                    if let clv {
                        clvCard(clv)
                    }
                    if let learning {
                        learningCard(learning)
                    }
                    if let sweep, (sweep.rows ?? []).isEmpty == false {
                        sweepCard(sweep)
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

    private func clvCard(_ clv: ClvSummary) -> some View {
        Card {
            Eyebrow(text: "Closing-line value · 90 days")
            if clv.nSelections == 0 {
                Text(clv.note ?? "No selection has a matching closing price yet.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                StatTile(
                    label: "Selections matched",
                    value: Fmt.count(clv.nSelections),
                    caption: "settled selections with a recorded close"
                )
                LabeledRow(
                    label: "Mean CLV",
                    value: Fmt.signedPercent(clv.meanClvPercent, digits: 2),
                    valueTint: (clv.meanClvPercent ?? 0) >= 0 ? Palette.accent : Palette.danger,
                    mono: true
                )
                LabeledRow(
                    label: "Beat the close",
                    value: String(format: "%.1f%%", clv.beatClosePercent ?? 0),
                    mono: true
                )
                ForEach((clv.byMarket ?? [:]).keys.sorted(), id: \.self) { key in
                    if let row = clv.byMarket?[key] {
                        DividerLine()
                        LabeledRow(
                            label: "\(Fmt.humanized(key)) · \(row.n)",
                            value: Fmt.signedPercent(row.meanClvPercent, digits: 2),
                            valueTint: (row.meanClvPercent ?? 0) >= 0 ? Palette.accent : Palette.danger,
                            mono: true
                        )
                    }
                }
                Text("CLV compares the price we took with the closing price. Positive means we beat the close; it does not by itself prove profit.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func learningCard(_ learning: LearningSummary) -> some View {
        Card {
            HStack {
                Eyebrow(text: "Learning")
                Spacer()
                Pill(
                    text: learning.trained ? "trained" : "not trained",
                    tint: learning.trained ? Palette.accent : Palette.warn
                )
            }
            if !learning.trained {
                Text(learning.note ?? "No learned parameters yet. Train from settled results to enable self-correction.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                LabeledRow(label: "Version", value: learning.version ?? "—", mono: true)
                if let trainedAt = learning.trainedAt {
                    LabeledRow(label: "Trained", value: Fmt.timestamp(trainedAt))
                }
                LabeledRow(
                    label: "Settled legs in fit",
                    value: Fmt.count(learning.sources?.settledLegs ?? 0)
                )
                LabeledRow(
                    label: "Walk-forward matches",
                    value: Fmt.count(learning.sources?.walkforwardMatches ?? 0)
                )
                LabeledRow(
                    label: "Applied markets",
                    value: (learning.appliedMarkets ?? []).isEmpty
                        ? "none"
                        : (learning.appliedMarkets ?? []).joined(separator: ", ")
                )
                let premiums = learning.edgePremiums ?? [:]
                ForEach(premiums.keys.sorted(), id: \.self) { key in
                    if let entry = premiums[key] {
                        DividerLine()
                        LabeledRow(
                            label: "\(Fmt.humanized(key)) edge premium",
                            value: String(format: "+%.1f%%", (entry.premium - 1.0) * 100),
                            valueTint: entry.premium > 1.0 ? Palette.accent : Palette.textSecondary,
                            mono: true
                        )
                    }
                }
                ForEach(learning.notes ?? [], id: \.self) { note in
                    Text(note)
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text("Calibration is applied only when the held-out evaluation showed a real improvement. A settled-leg count of zero means secondary-market premiums are still waiting for evidence.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func sweepCard(_ sweep: ComboSweep) -> some View {
        let recommendation = sweep.recommendation
        return Card {
            HStack {
                Eyebrow(text: "Tier sweep · history")
                Spacer()
                if let action = recommendation?.action {
                    Pill(
                        text: action == "consider_tier" ? "consider" : "keep default",
                        tint: action == "consider_tier" ? Palette.accent : Palette.warn
                    )
                }
            }
            if let window = sweep.window {
                Text("\(Fmt.count(window.matchdays)) matchdays · \(Fmt.count(window.settledMatches)) settled matches")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
            }
            if let recommendation, let tier = recommendation.tier {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(Fmt.percent(tier, digits: 0))
                        .font(.system(size: 32, weight: .bold, design: .rounded))
                        .foregroundStyle(Palette.textPrimary)
                    Text("leads on training")
                        .font(TypeScale.body)
                        .foregroundStyle(Palette.textSecondary)
                }
                if let train = recommendation.train {
                    LabeledRow(
                        label: "Train · \(Fmt.count(train.count)) tickets",
                        value: "hit \(Fmt.percent(train.hitRate)) · \(Fmt.signedPercent(train.roiPercent, digits: 2)) ROI",
                        valueTint: (train.roiPercent ?? 0) >= 0 ? Palette.accent : Palette.danger,
                        mono: true
                    )
                }
                if let holdout = recommendation.holdout {
                    LabeledRow(
                        label: "Holdout · \(Fmt.count(holdout.count)) tickets",
                        value: "hit \(Fmt.percent(holdout.hitRate)) · \(Fmt.signedPercent(holdout.roiPercent, digits: 2)) ROI",
                        valueTint: (holdout.roiPercent ?? 0) >= 0 ? Palette.accent : Palette.danger,
                        mono: true
                    )
                    if let required = holdout.requiredHitRate {
                        LabeledRow(
                            label: "Holdout break-even hit",
                            value: Fmt.percent(required),
                            mono: true
                        )
                    }
                }
                if let guidance = recommendation.guidance {
                    NoteRow(text: guidance, tone: recommendation.holdoutConfirmed == true ? .good : .warn)
                }
            } else if let recommendation {
                Text(recommendation.note ?? recommendation.guidance ?? "Not enough settled tickets to compare tiers yet.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            DividerLine()
            ForEach(sweep.rows ?? []) { row in
                sweepTierRow(row, recommended: recommendation?.tier)
            }
            Text("Tiers are compared on training matchdays only; the holdout is confirmation, not an input. The published 85% gate is unchanged.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func sweepTierRow(_ row: SweepTier, recommended: Double?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(Fmt.percent(row.tier, digits: 0))
                .font(.system(size: 13, weight: .bold, design: .monospaced))
                .foregroundStyle(row.tier == recommended ? Palette.accent : Palette.textPrimary)
                .frame(width: 44, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text("train \(Fmt.percent(row.train?.hitRate)) · \(Fmt.signedPercent(row.train?.roiPercent, digits: 1))")
                Text("holdout \(Fmt.percent(row.holdout?.hitRate)) · \(Fmt.signedPercent(row.holdout?.roiPercent, digits: 1))")
            }
            .font(.system(size: 11, weight: .medium, design: .monospaced))
            .foregroundStyle(Palette.textSecondary)
            Spacer(minLength: 0)
            if row.tier == recommended {
                Pill(text: "train best")
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
            async let clvTask = try? client.get(
                "/evaluation/clv",
                query: [URLQueryItem(name: "days", value: "90")],
                as: ClvSummary.self
            )
            async let learningTask = try? client.get("/learning/summary", as: LearningSummary.self)
            async let sweepTask = try? client.get("/combos/backtest/sweep", as: ComboSweep.self)
            let (loadedHealth, loadedSummary, loadedClv, loadedLearning, loadedSweep) = try await (
                healthTask, summaryTask, clvTask, learningTask, sweepTask
            )
            health = loadedHealth
            summary = loadedSummary
            clv = loadedClv
            learning = loadedLearning
            sweep = loadedSweep
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
