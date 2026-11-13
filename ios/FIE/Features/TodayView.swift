import SwiftUI

struct TodayView: View {
    @EnvironmentObject private var store: EngineStore

    @State private var analysing = false
    @State private var refreshingFeeds = false
    @State private var refreshError: String?
    @State private var refreshNote: String?

    private var report: DailyComboReport? { store.report }
    private var cachedAt: Date? { store.cachedAt }
    private var health: Health? { store.health }
    private var errorMessage: String? { store.errors[.report] }
    private var hasLoaded: Bool { store.isBootstrapped }

    private var busy: Bool { analysing || refreshingFeeds || store.isRefreshing }

    private var analyseLabel: String {
        if analysing { return "Analysing stored prices…" }
        if refreshingFeeds { return "Refreshing bookmaker feeds…" }
        return "Analyse today's slate"
    }

    private var gate: Double? {
        report?.tier.publishedGate ?? health?.minPredictionProbability
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    intro
                    engineChip
                    actions
                    statusNotes
                    if let errorMessage {
                        NoteRow(text: errorMessage, tone: report == nil ? .danger : .warn)
                    }
                    if analysing && report == nil {
                        LoadingCard(label: "Reading today's slate from stored prices…")
                    }
                    if !store.live.isEmpty {
                        LiveMatchesCard(fixtures: store.live, maxRows: 3)
                    }
                    if let report {
                        DailyComboReportView(report: report, cachedAt: cachedAt)
                            .transition(.opacity)
                            .animation(
                                store.isAutoTicking ? nil : .easeOut(duration: 0.25),
                                value: report.generatedAt
                            )
                    } else if !busy && errorMessage == nil && hasLoaded {
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
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        HistoryView()
                    } label: {
                        Image(systemName: "clock.arrow.circlepath")
                    }
                    .accessibilityLabel("Test on history")
                }
            }
            .task {
                if !store.isBootstrapped { await store.bootstrap() }
            }
            .refreshable { await store.refreshAll() }
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

    private var engineChip: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(engineTint)
                .frame(width: 7, height: 7)
            Text(engineLabel)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.textSecondary)
                .lineLimit(2)
            Spacer(minLength: 0)
            if let gate {
                Pill(text: "\(Int((gate * 100).rounded()))% gate", tint: Palette.accent)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Palette.card)
        )
    }

    private var engineTint: Color {
        if health != nil { return Palette.accent }
        return store.isBootstrapped ? Palette.danger : Palette.textTertiary
    }

    private var engineLabel: String {
        if let health {
            return "Engine online · \(Fmt.count(health.totalFixtures)) fixtures stored"
        }
        if !store.isBootstrapped {
            return "Checking the engine…"
        }
        return report == nil
            ? "Engine unreachable · no saved report"
            : "Engine unreachable · showing the saved report"
    }

    private var actions: some View {
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
    private func analyseSlate() async {
        guard !busy else { return }
        refreshError = nil
        refreshNote = nil

        analysing = true
        await store.refresh(section: .report)
        analysing = false

        refreshingFeeds = true
        do {
            let refreshed = try await store.refreshFeeds()
            applyRefreshSummary(refreshed)
            if store.errors[.report] == nil {
                Haptics.success()
            } else {
                Haptics.warning()
            }
        } catch {
            refreshError = error.localizedDescription
            Haptics.warning()
        }
        refreshingFeeds = false
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
    var cachedAt: Date?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            tierHeader
            if report.slips.isEmpty {
                noSelectionCard
            } else {
                slipSection
            }
            ComboExplorer(report: report)
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
                if let cachedAt {
                    Pill(text: "cached \(Fmt.clock(cachedAt))", tint: Palette.warn)
                }
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
            Text("Built \(Fmt.timestamp(report.generatedAt)) from the stored prices at that moment.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
        }
    }

    @ViewBuilder
    private var slipSection: some View {
        let slips = report.slips
        ForEach(slips.indices, id: \.self) { index in
            ComboSlipCard(
                combo: slips[index],
                report: report,
                label: index == 0 ? "Best slip" : "Alternative \(index)",
                prominent: index == 0,
                shared: sharedFixtureIds(
                    in: slips[index],
                    before: Array(slips.prefix(index))
                )
            )
        }
        if slips.count < 3 {
            Text(
                "Only \(report.scope.qualifiedLegs) legs cleared the tier, so this is the whole "
                    + "set — another slip would need a leg below it."
            )
            .font(TypeScale.caption)
            .foregroundStyle(Palette.textTertiary)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func sharedFixtureIds(in combo: DailyCombo, before earlier: [DailyCombo]) -> Set<Int> {
        let used = earlier.reduce(into: Set<Int>()) { ids, slip in
            ids.formUnion(slip.legs.map(\.fixtureId))
        }
        return Set(combo.legs.map(\.fixtureId).filter { used.contains($0) })
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
                    NavigationLink {
                        FixtureDetailView(fixtureId: report.legs[index].fixtureId)
                    } label: {
                        LegDetailRow(index: index + 1, leg: report.legs[index])
                    }
                    .buttonStyle(.plain)
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

private struct ComboSlipCard: View {
    let combo: DailyCombo
    let report: DailyComboReport
    let label: String
    var prominent: Bool = false
    var shared: Set<Int> = []

    var body: some View {
        Card(glow: prominent) {
            HStack {
                Eyebrow(text: "\(label) · \(combo.size)-team combo")
                Spacer()
                Pill(text: "Paper only", tint: Palette.warn)
            }
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(Fmt.percent(combo.combinedProbability))
                    .font(.system(size: prominent ? 42 : 30, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.textPrimary)
                    .contentTransition(.numericText())
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
                NavigationLink {
                    FixtureDetailView(fixtureId: combo.legs[index].fixtureId)
                } label: {
                    LegDetailRow(
                        index: index + 1,
                        leg: combo.legs[index],
                        shared: shared.contains(combo.legs[index].fixtureId)
                    )
                }
                .buttonStyle(.plain)
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
            ShareLink(item: SlipBuilder.text(combo: combo, report: report)) {
                Label("Share ticket", systemImage: "square.and.arrow.up")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.accent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .fill(Palette.accentSoft)
                    )
            }
        }
    }
}

private struct ComboExplorer: View {
    let report: DailyComboReport

    @State private var selectedSize: Int?
    @State private var expanded: Set<String> = []

    private var sizes: [Int] {
        var seen = Set<Int>()
        for key in report.combos.keys {
            if let size = Int(key.prefix(1)) { seen.insert(size) }
        }
        if seen.isEmpty {
            for size in report.params.comboSizes { seen.insert(size) }
        }
        return seen.sorted()
    }

    private var activeSize: Int? { selectedSize ?? sizes.first }

    private var combos: [DailyCombo] {
        guard let activeSize else { return [] }
        let rows = report.combos["\(activeSize)_leg"]
            ?? report.combos.first { Int($0.key.prefix(1)) == activeSize }?.value
            ?? []
        return rows.sorted { $0.combinedProbability > $1.combinedProbability }
    }

    var body: some View {
        Card {
            HStack {
                Eyebrow(text: "Combo explorer")
                Spacer()
                Text("\(combos.count) ticket\(combos.count == 1 ? "" : "s")")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textSecondary)
            }

            if sizes.isEmpty {
                Text("No combo was built for today's tier.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
            } else {
                Picker(
                    "Combo size",
                    selection: Binding(
                        get: { activeSize ?? sizes[0] },
                        set: { selectedSize = $0 }
                    )
                ) {
                    ForEach(sizes, id: \.self) { size in
                        Text("\(size)-leg").tag(size)
                    }
                }
                .pickerStyle(.segmented)

                ForEach(combos.indices, id: \.self) { index in
                    let combo = combos[index]
                    let key = rowKey(combo)
                    if index > 0 { DividerLine() }
                    Button {
                        Haptics.tap()
                        withAnimation(.easeInOut(duration: 0.18)) {
                            if expanded.contains(key) {
                                expanded.remove(key)
                            } else {
                                expanded.insert(key)
                            }
                        }
                    } label: {
                        row(combo)
                    }
                    .buttonStyle(.plain)

                    if expanded.contains(key) {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(combo.legs.indices, id: \.self) { legIndex in
                                NavigationLink {
                                    FixtureDetailView(fixtureId: combo.legs[legIndex].fixtureId)
                                } label: {
                                    LegDetailRow(index: legIndex + 1, leg: combo.legs[legIndex])
                                }
                                .buttonStyle(.plain)
                                if legIndex < combo.legs.count - 1 { DividerLine() }
                            }
                            ShareLink(item: SlipBuilder.text(combo: combo, report: report)) {
                                Label("Share ticket", systemImage: "square.and.arrow.up")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(Palette.accent)
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 8)
                                    .background(
                                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                                            .fill(Palette.accentSoft)
                                    )
                            }
                        }
                        .padding(.top, 8)
                        .transition(.opacity)
                    }
                }
            }
        }
    }

    private func row(_ combo: DailyCombo) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(Fmt.percent(combo.combinedProbability))
                .font(.system(size: 17, weight: .bold, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(Palette.textPrimary)
            Text("@ \(Fmt.odds(combo.totalOdds))")
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(Palette.accent)
            if isBest(combo) {
                Pill(text: "best")
            }
            Spacer(minLength: 6)
            Text("\(combo.legs.count) legs")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
            Image(systemName: expanded.contains(rowKey(combo)) ? "chevron.up" : "chevron.down")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.textTertiary)
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
    }

    private func isBest(_ combo: DailyCombo) -> Bool {
        guard let best = report.best else { return false }
        return best.size == combo.size
            && best.legs.map(\.fixtureId) == combo.legs.map(\.fixtureId)
    }

    private func rowKey(_ combo: DailyCombo) -> String {
        let ids = combo.legs.map { String($0.fixtureId) }.joined(separator: "-")
        return "\(combo.size):\(ids)"
    }
}

private struct LegDetailRow: View {
    let index: Int?
    let leg: DailyComboLeg
    var shared: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
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
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(leg.selection)
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textSecondary)
                    .lineLimit(1)
                Spacer(minLength: 6)
                KickoffCountdown(iso: leg.kickoffUtc)
            }
            Text("\(leg.competition) · \(leg.kickoffEat)")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
            ProbabilityBar(model: leg.probability, market: leg.marketProbability)
            HStack(spacing: 6) {
                Pill(text: Fmt.percent(leg.probability))
                if leg.calibrated {
                    Pill(text: "calibrated", tint: Palette.accent)
                }
                if let bookmaker = leg.bookmaker {
                    Pill(text: bookmaker, tint: Palette.textSecondary)
                }
                if shared {
                    Pill(text: "used above", tint: Palette.warn)
                }
                Spacer(minLength: 0)
                if let age = leg.priceAgeHours {
                    Text("\(Fmt.age(hours: age)) old")
                        .font(TypeScale.caption)
                        .foregroundStyle(PriceFreshness.tint(hours: age))
                }
                Image(systemName: "chevron.right")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Palette.textTertiary)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}
