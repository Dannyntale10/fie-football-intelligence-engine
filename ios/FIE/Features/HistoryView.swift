import SwiftUI

struct HistoryView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var report: ComboBacktestReport?
    @State private var loading = false
    @State private var running = false
    @State private var errorMessage: String?
    @State private var statusMessage: String?
    @State private var hasLoaded = false

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    private var busy: Bool { loading || running }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                intro

                Button {
                    Task { await runReplay() }
                } label: {
                    HStack(spacing: 8) {
                        if running {
                            ProgressView()
                                .tint(Palette.background)
                                .controlSize(.small)
                        }
                        Text(running ? "Replaying settled history…" : "Run historical replay")
                    }
                }
                .buttonStyle(PrimaryButtonStyle(busy: busy))
                .disabled(busy)

                if let statusMessage {
                    NoteRow(text: statusMessage)
                }
                if let errorMessage {
                    NoteRow(text: errorMessage, tone: .danger)
                }
                if loading && report == nil {
                    LoadingCard(label: "Loading the saved replay…")
                }

                if let report {
                    reportBody(report)
                } else if !busy && errorMessage == nil && hasLoaded {
                    Card {
                        Text("No replay saved yet.")
                            .font(TypeScale.section)
                        Text("Run the replay on settled history to measure hit rate and ROI, then keep the calibration only if it beats its own holdout bar.")
                            .font(TypeScale.body)
                            .foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Palette.background.ignoresSafeArea())
        .navigationTitle("Historical test")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadSaved() }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "Out-of-sample replay")
            Text("Test on history")
                .font(TypeScale.title)
                .foregroundStyle(Palette.textPrimary)
            Text("The combo rule is replayed on settled matches, one top-ranked ticket per size per matchday. The latest 30% of matchdays is the holdout; calibration is stored only when it reduces held-out log loss.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func reportBody(_ report: ComboBacktestReport) -> some View {
        if report.status != "ok" {
            NoteRow(text: report.message ?? "The replay did not finish.", tone: .warn)
        }

        if let window = report.window {
            Card {
                HStack {
                    Eyebrow(text: "Settled history")
                    Spacer()
                    Pill(text: "\(window.matchdays) matchdays")
                }
                StatTile(
                    label: "Settled matches",
                    value: Fmt.count(window.settledMatches),
                    caption: windowCaption(window)
                )
                if let params = report.params {
                    DividerLine()
                    LabeledRow(label: "Leg tier", value: Fmt.percent(params.minLegProbability, digits: 0))
                    if let stake = params.stake {
                        LabeledRow(label: "Flat stake per ticket", value: Fmt.decimal(stake, digits: 2))
                    }
                    if let mode = params.priceMode {
                        LabeledRow(label: "Price mode", value: mode)
                    }
                    if let share = params.holdoutShare {
                        LabeledRow(label: "Holdout share", value: Fmt.percent(share, digits: 0))
                    }
                }
            }
        }

        if let holdout = report.combos?.holdout, !holdout.isEmpty {
            MetricsTable(
                title: "Holdout · latest \(Int(((report.params?.holdoutShare ?? 0.3) * 100).rounded()))%",
                caption: "Out-of-sample tickets. Hit rate above break-even is the measure of whether the tier is worth playing.",
                rows: holdout
            )
        }

        if let after = report.holdoutAfterLearning, !after.isEmpty {
            MetricsTable(
                title: "Holdout · after learning",
                caption: "Legs re-selected with a calibration fitted on training matchdays only, so this comparison stays out of sample.",
                rows: after
            )
        }

        if let all = report.combos?.all, !all.isEmpty {
            MetricsTable(
                title: "All history",
                caption: "Every settled matchday, including the training slice.",
                rows: all
            )
        }

        if let learning = report.learning {
            learningCard(learning)
        }

        if let fit = report.holdoutFit, !fit.applied {
            NoteRow(
                text: "A calibration fitted on training matchdays only did not clear its own held-out improvement bar, so no after-learning holdout table is published.\(fit.reason.map { " Reason: \($0)." } ?? "")",
                tone: .warn
            )
        }

        if let notes = report.notes, !notes.isEmpty {
            Card {
                Eyebrow(text: "How to read this")
                ForEach(notes, id: \.self) { note in
                    HStack(alignment: .top, spacing: 8) {
                        Text("•")
                            .foregroundStyle(Palette.textTertiary)
                        Text(note)
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func windowCaption(_ window: BacktestWindow) -> String {
        guard let first = window.firstMatchday, let last = window.lastMatchday else {
            return "\(window.matchdays) matchdays"
        }
        return "\(window.matchdays) matchdays · \(first) → \(last)"
    }

    private func learningCard(_ learning: BacktestLearning) -> some View {
        Card {
            HStack {
                Eyebrow(text: "Learning")
                Spacer()
                Pill(
                    text: learning.applied ? "Applied" : "Not applied",
                    tint: learning.applied ? Palette.accent : Palette.warn
                )
            }
            LabeledRow(label: "Calibration a", value: Fmt.decimal(learning.a), mono: true)
            LabeledRow(label: "Calibration b", value: Fmt.decimal(learning.b), mono: true)
            LabeledRow(label: "Settled legs in fit", value: Fmt.count(learning.n))
            if let before = learning.loglossBefore, let after = learning.loglossAfter {
                LabeledRow(
                    label: "Holdout log loss",
                    value: "\(Fmt.decimal(before, digits: 4)) → \(Fmt.decimal(after, digits: 4))",
                    mono: true
                )
            }
            if let improvement = learning.improvement {
                LabeledRow(
                    label: "Improvement",
                    value: Fmt.decimal(improvement, digits: 5),
                    valueTint: improvement >= 0.002 ? Palette.accent : Palette.warn,
                    mono: true
                )
            }
            if let reason = learning.reason, !reason.isEmpty {
                Text(reason)
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @MainActor
    private func loadSaved() async {
        guard !busy else { return }
        loading = true
        errorMessage = nil
        defer {
            loading = false
            hasLoaded = true
        }
        do {
            report = try await client.get("/combos/backtest", as: ComboBacktestReport.self)
        } catch let error as APIError where error.message.contains("No combo backtest yet") {
            statusMessage = "No replay saved yet. Run the replay to build it."
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    @MainActor
    private func runReplay() async {
        guard !busy else { return }
        running = true
        errorMessage = nil
        statusMessage = nil
        defer {
            running = false
            hasLoaded = true
        }
        do {
            let result = try await client.post("/combos/backtest", timeout: 900, as: ComboBacktestReport.self)
            report = result
            if result.status == "ok" {
                if result.learning?.applied == true {
                    statusMessage = "Replay finished. The calibration cleared its held-out bar and is now stored for live use."
                } else {
                    statusMessage = "Replay finished. The calibration did not clear its held-out bar, so raw de-vigged prices stay in use."
                }
            } else {
                statusMessage = result.message ?? "The replay returned status \(result.status)."
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Metrics table

private struct MetricsTable: View {
    let title: String
    let caption: String
    let rows: [String: ComboMetrics]

    var body: some View {
        Card {
            Eyebrow(text: title)
            Text(caption)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            tableHeader
            ForEach(Fmt.sortedSizeKeys(rows), id: \.self) { key in
                if let metrics = rows[key] {
                    DividerLine()
                    row(label: Fmt.sizeLabel(key), metrics: metrics)
                }
            }
            Text("Top-ranked ticket per size per matchday, flat stakes. ROI is measured at historical prices; a negative ROI is expected at a bookmaker's own margin.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var tableHeader: some View {
        HStack(spacing: 8) {
            Text("TICKET")
                .frame(width: 58, alignment: .leading)
            Text("SETTLED")
                .frame(maxWidth: .infinity, alignment: .trailing)
            Text("HIT")
                .frame(maxWidth: .infinity, alignment: .trailing)
            Text("PRED.")
                .frame(maxWidth: .infinity, alignment: .trailing)
            Text("ROI")
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .font(.system(size: 10, weight: .semibold))
        .tracking(0.6)
        .foregroundStyle(Palette.textTertiary)
    }

    private func row(label: String, metrics: ComboMetrics) -> some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Palette.textPrimary)
                .frame(width: 58, alignment: .leading)
            valueCell(Fmt.count(metrics.count))
            valueCell(Fmt.percent(metrics.hitRate))
            valueCell(Fmt.percent(metrics.averagePredictedProbability))
            valueCell(
                Fmt.signedPercent(metrics.roiPercent, digits: 2),
                tint: (metrics.roiPercent ?? 0) >= 0 ? Palette.accent : Palette.danger
            )
        }
    }

    private func valueCell(_ text: String, tint: Color = Palette.textSecondary) -> some View {
        Text(text)
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundStyle(tint)
            .frame(maxWidth: .infinity, alignment: .trailing)
            .minimumScaleFactor(0.8)
            .lineLimit(1)
    }
}
