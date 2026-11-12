import SwiftUI

struct TodayView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var report: DailyComboReport?
    @State private var loadingReport = false
    @State private var refreshingFeeds = false
    @State private var errorMessage: String?
    @State private var refreshError: String?
    @State private var refreshNote: String?
    @State private var hasLoaded = false

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    private var busy: Bool { loadingReport || refreshingFeeds }

    private var analyseLabel: String {
        if loadingReport { return "Analysing stored prices…" }
        if refreshingFeeds { return "Refreshing bookmaker feeds…" }
        return "Analyse today's slate"
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    intro
                    actions
                    statusNotes
                    if let errorMessage {
                        NoteRow(text: errorMessage, tone: .danger)
                    }
                    if loadingReport && report == nil {
                        LoadingCard(label: "Reading today's slate from stored prices…")
                    }
                    if let report {
                        DailyComboReportView(report: report)
                    } else if !loadingReport && errorMessage == nil && hasLoaded {
                        Card {
                            Text("No report yet.")
                                .font(TypeScale.section)
                            Text("Tap Analyse today's slate to read the stored bookmaker prices for today's fixtures.")
                                .font(TypeScale.body)
                                .foregroundStyle(Palette.textSecondary)
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Today")
            .task { await loadStoredReport() }
            .refreshable { await loadStoredReport() }
            .onChange(of: serverURL) { _, _ in
                report = nil
                hasLoaded = false
                Task { await loadStoredReport() }
            }
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "Control room")
            Text("Football Intelligence")
                .font(TypeScale.title)
                .foregroundStyle(Palette.textPrimary)
            Text("One 1X2 selection per fixture from today's real bookmaker prices, de-vigged. The 75% tier is exploratory and sits below the published 85% gate; the gate itself is unchanged.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var actions: some View {
        VStack(spacing: 10) {
            Button {
                Task { await analyseSlate() }
            } label: {
                HStack(spacing: 8) {
                    if busy {
                        ProgressView()
                            .tint(Palette.background)
                            .controlSize(.small)
                    }
                    Text(analyseLabel)
                }
            }
            .buttonStyle(PrimaryButtonStyle(busy: busy))
            .disabled(busy)

            NavigationLink {
                HistoryView()
            } label: {
                Label("Test on history", systemImage: "clock.arrow.circlepath")
            }
            .buttonStyle(SecondaryButtonStyle())
        }
    }

    @ViewBuilder
    private var statusNotes: some View {
        if refreshingFeeds {
            LoadingCard(label: "Refreshing the public bookmaker feeds; the report below uses stored prices meanwhile.")
        }
        if let refreshError {
            NoteRow(
                text: "\(refreshError) The report below was rebuilt from whatever prices are stored.",
                tone: .warn
            )
        }
        if let refreshNote {
            NoteRow(text: refreshNote)
        }
    }

    @MainActor
    private func loadStoredReport() async {
        guard !busy else { return }
        loadingReport = true
        errorMessage = nil
        defer {
            loadingReport = false
            hasLoaded = true
        }
        do {
            report = try await client.get("/combos/daily", as: DailyComboReport.self)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    @MainActor
    private func analyseSlate() async {
        guard !busy else { return }
        errorMessage = nil
        refreshError = nil
        refreshNote = nil

        // Show the analysis from stored prices first: a full feed sync can hang on an
        // unreachable bookmaker host for minutes, and the report must not wait for it.
        loadingReport = true
        do {
            report = try await client.get("/combos/daily", as: DailyComboReport.self)
        } catch {
            errorMessage = error.localizedDescription
        }
        loadingReport = false

        refreshingFeeds = true
        do {
            let refreshed = try await client.post(
                "/sources/refresh",
                timeout: 900,
                as: SourcesRefreshResult.self
            )
            report = try await client.get("/combos/daily", as: DailyComboReport.self)
            applyRefreshSummary(refreshed)
        } catch {
            refreshError = error.localizedDescription
        }
        refreshingFeeds = false
        hasLoaded = true
    }

    @MainActor
    private func applyRefreshSummary(_ refreshed: SourcesRefreshResult) {
        let feeds = Array(refreshed.feeds.values)
        let failed = feeds.filter(\.isFailed).count
        let prices = refreshed.totals.oddsUpdated ?? 0
        let newFixtures = refreshed.totals.insertedNew ?? 0
        refreshNote = "Synced \(feeds.count - failed)/\(feeds.count) bookmaker feeds · \(prices) prices updated · \(newFixtures) new fixtures."

        if failed > 0 {
            let reasons = feeds.filter(\.isFailed).map { feed -> String in
                let name = feed.label ?? feed.provider ?? "feed"
                return "\(name): \(feed.error ?? "unreachable")"
            }
            refreshError = "\(failed) bookmaker feed(s) unreachable, so their prices were not refreshed"
                + (reasons.isEmpty ? "." : " (\(reasons.joined(separator: "; "))).")
        }
    }
}

// MARK: - Report

struct DailyComboReportView: View {
    let report: DailyComboReport

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            tierHeader
            if let best = report.best {
                BestComboCard(combo: best)
            } else {
                noSelectionCard
            }
            legPool
            exclusions
            calibration
            if !report.staleLegs.isEmpty {
                NoteRow(
                    text: "\(report.staleLegs.count) leg(s) carry prices older than 12 hours; they are shown with their price age.",
                    tone: .warn
                )
            }
            Text(report.note)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var tierHeader: some View {
        Card {
            HStack {
                Eyebrow(text: report.tier.label)
                Spacer()
                Pill(text: "\(Int((report.tier.publishedGate * 100).rounded()))% gate")
            }
            Text(report.matchdayLabel)
                .font(.system(size: 19, weight: .semibold))
                .foregroundStyle(Palette.textPrimary)
            HStack(spacing: 6) {
                Text("\(report.scope.fixturesOnSlate) on the slate")
                Text("·")
                Text("\(report.scope.qualifiedLegs) legs at or above \(Fmt.percent(report.params.minLegProbability, digits: 0))")
            }
            .font(TypeScale.caption)
            .foregroundStyle(Palette.textSecondary)
        }
    }

    private var noSelectionCard: some View {
        Card {
            Text(report.message)
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(Palette.warn)
            Text("Nothing met the tier for this slate. The gate is never lowered to fill a report.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
        }
    }

    private var legPool: some View {
        Card {
            HStack {
                Eyebrow(text: "Leg pool")
                Spacer()
                Text("\(report.legs.count) qualified")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textSecondary)
            }
            if report.legs.isEmpty {
                Text("No fixture on today's slate produced an attributed price above the tier.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
            } else {
                ForEach(report.legs.indices, id: \.self) { index in
                    if index > 0 { DividerLine() }
                    LegDetailRow(index: index + 1, leg: report.legs[index])
                }
            }
        }
    }

    @ViewBuilder
    private var exclusions: some View {
        let visible = report.scope.rejected
            .filter { $0.value > 0 }
            .map { RejectionRow(label: Fmt.humanized($0.key), count: $0.value) }
            .sorted { $0.label < $1.label }
        if !visible.isEmpty {
            Card {
                Eyebrow(text: "Excluded from the slate")
                ForEach(visible) { entry in
                    LabeledRow(label: entry.label, value: "\(entry.count)")
                }
                Text("Every rejection is logged; nothing is silently dropped.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
            }
        }
    }

    private var calibration: some View {
        Card {
            Eyebrow(text: "Calibration")
            if report.learning.applied {
                Text("Learned from \(Fmt.count(report.learning.n)) settled legs (a=\(Fmt.decimal(report.learning.a)), b=\(Fmt.decimal(report.learning.b))). It is applied to today's legs only because it cleared its own held-out improvement bar.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("No stored calibration yet; raw de-vigged market probabilities are used.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
            }
        }
    }
}

private struct RejectionRow: Identifiable {
    let label: String
    let count: Int

    var id: String { label }
}

private struct BestComboCard: View {
    let combo: DailyCombo

    var body: some View {
        Card {
            HStack {
                Eyebrow(text: "Best \(combo.size)-team combo")
                Spacer()
                Pill(text: "Paper only", tint: Palette.warn)
            }
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(Fmt.percent(combo.combinedProbability))
                    .font(.system(size: 42, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.textPrimary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("win probability")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textSecondary)
                    Text("\(Fmt.odds(combo.totalOdds)) total odds")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Palette.accent)
                        .monospacedDigit()
                }
                Spacer(minLength: 0)
            }
            DividerLine()
            ForEach(combo.legs.indices, id: \.self) { index in
                if index > 0 { DividerLine() }
                LegDetailRow(index: nil, leg: combo.legs[index])
            }
            DividerLine()
            if let implied = combo.impliedProbability {
                LabeledRow(
                    label: "De-vigged market probability",
                    value: Fmt.percent(implied),
                    mono: true
                )
            }
            if let ev = combo.expectedValuePercent {
                LabeledRow(
                    label: "Expected value at these prices",
                    value: Fmt.signedPercent(ev, digits: 2),
                    valueTint: ev >= 0 ? Palette.accent : Palette.danger,
                    mono: true
                )
            }
        }
    }
}

private struct LegDetailRow: View {
    let index: Int?
    let leg: DailyComboLeg

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let index {
                    Text("\(index)")
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .foregroundStyle(Palette.textTertiary)
                        .frame(width: 18, alignment: .leading)
                }
                Text(leg.match)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Palette.textPrimary)
                Spacer(minLength: 8)
                Text(Fmt.odds(leg.odds))
                    .font(.system(size: 16, weight: .bold, design: .monospaced))
                    .foregroundStyle(Palette.accent)
            }
            Text("\(leg.selection) · \(leg.kickoffEat)")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
            Text(leg.competition)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
            HStack(spacing: 6) {
                Pill(text: Fmt.percent(leg.probability))
                Pill(text: "\(Fmt.percent(leg.marketProbability)) market", tint: Palette.textSecondary)
                if let bookmaker = leg.bookmaker {
                    Pill(text: bookmaker, tint: Palette.textSecondary)
                }
                Spacer(minLength: 0)
                if let age = leg.priceAgeHours {
                    Text("\(Fmt.age(hours: age)) old")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
