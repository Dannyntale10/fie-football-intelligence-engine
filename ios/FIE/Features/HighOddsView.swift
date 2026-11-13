import SwiftUI

struct HighOddsView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL
    @State private var report: HighOddsReport?
    @State private var loading = false
    @State private var errorMessage: String?

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                intro
                if loading && report == nil {
                    LoadingCard(label: "Scanning the slate for value legs…")
                }
                if let errorMessage {
                    NoteRow(text: errorMessage, tone: .warn)
                }
                if let report {
                    stats(report)
                    NoteRow(text: report.note)
                    if report.slips.isEmpty {
                        emptyState(report)
                    } else {
                        slips(report)
                    }
                    rejectedFooter(report)
                    scopeFooter(report)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Palette.background.ignoresSafeArea())
        .navigationTitle("High odds 6.0")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "Near-6.00 tickets")
            Text("Three to five result legs, combined into 5.50–6.50")
                .font(TypeScale.section)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("A leg only appears when its blended probability beats the attributed price. The 85% gate is a separate mode and is untouched; no slip is ever placed.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func stats(_ report: HighOddsReport) -> some View {
        Card {
            HStack(spacing: 12) {
                StatTile(
                    label: "Slips published",
                    value: "\(report.count)",
                    caption: "inside the band, cap 3"
                )
                StatTile(
                    label: "Target odds",
                    value: Fmt.odds(report.params.targetTotalOdds),
                    caption: report.params.oddsBand.count == 2
                        ? "band \(Fmt.odds(report.params.oddsBand[0]))–\(Fmt.odds(report.params.oddsBand[1]))"
                        : nil
                )
            }
            HStack(spacing: 12) {
                StatTile(
                    label: "+EV legs",
                    value: "\(report.positiveEvLegs)/\(report.legsEvaluated)",
                    caption: "beats the price after the margin"
                )
                StatTile(
                    label: "Break-even strike",
                    value: String(format: "%.1f%%", report.diagnostics.requiredStrikeRatePercent),
                    caption: "share of 6.00 tickets that must win"
                )
            }
        }
    }

    @ViewBuilder
    private func slips(_ report: HighOddsReport) -> some View {
        ForEach(report.slips.indices, id: \.self) { index in
            slipCard(report.slips[index], rank: index + 1)
        }
        if report.slips.count < 3 {
            Text(
                "Only \(report.slips.count) disjoint slip\(report.slips.count == 1 ? "" : "s") fit "
                    + "the band with distinct matches, so the list is shorter than its cap of 3."
            )
            .font(TypeScale.caption)
            .foregroundStyle(Palette.textTertiary)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func slipCard(_ slip: HighOddsSlip, rank: Int) -> some View {
        Card(glow: rank == 1) {
            HStack {
                Eyebrow(text: "Slip \(rank) · \(slip.size) legs")
                Spacer()
                Pill(text: "every leg +EV")
            }
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(Fmt.odds(slip.totalOdds))
                    .font(.system(size: rank == 1 ? 38 : 30, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.textPrimary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("total odds")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textSecondary)
                    Text("\(Fmt.percent(slip.combinedProbability)) combined · \(String(format: "%.1f%%", slip.expectedValuePercent)) EV")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Palette.accent)
                        .monospacedDigit()
                }
                Spacer(minLength: 0)
            }
            DividerLine()
            ForEach(slip.legs) { leg in
                HighOddsLegRow(leg: leg)
                DividerLine()
            }
            LabeledRow(
                label: "Weakest leg",
                value: Fmt.percent(slip.minLegProbability),
                mono: true
            )
            LabeledRow(
                label: "Break-even strike for this price",
                value: Fmt.percent(slip.impliedProbability),
                mono: true
            )
            if let first = slip.legs.first {
                Text(provenance(first))
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func provenance(_ leg: HighOddsLeg) -> String {
        var parts: [String] = ["Prices from \(leg.bookmaker ?? "an unnamed bookmaker")"]
        if let source = leg.source { parts[0] += " (\(source))" }
        if let age = leg.priceAgeHours {
            parts.append(String(format: "%.1f h old at analysis", age))
        }
        parts.append("captured \(Fmt.timestamp(leg.capturedAt))")
        return parts.joined(separator: " · ")
    }

    private func emptyState(_ report: HighOddsReport) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Card {
                Text(report.message)
                    .font(.system(size: 17, weight: .bold))
                    .foregroundStyle(Palette.warn)
                Text("No leg was priced above its blended probability, so there is no honest 6.00 ticket to publish. The expected-value rule is never relaxed to fill this page.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let inconsistent = report.rejected["inconsistent_market_prices"], inconsistent > 0 {
                    Text("\(inconsistent) fixture\(inconsistent == 1 ? "" : "s") were excluded because the three best prices came from different bookmakers and no longer add up to a real market.")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Card {
                Eyebrow(text: "How close the best legs were")
                if report.closestLegs.isEmpty {
                    Text("No priced fixture made it far enough to compare. \(report.scope.fixturesOnSlate) fixture\(report.scope.fixturesOnSlate == 1 ? "" : "s") on the slate.")
                        .font(TypeScale.body)
                        .foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    if let match = report.diagnostics.bestLegMatch {
                        Text("Best leg: \(match) · \(report.diagnostics.bestLegSelection ?? "—") at \(Fmt.odds(report.diagnostics.bestLegOdds)) · EV \(report.diagnostics.bestLegEv.map { String(format: "%+.1f%%", $0 * 100) } ?? "—")")
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(report.closestLegs) { leg in
                        HighOddsLegRow(leg: leg)
                        DividerLine()
                    }
                    Text("These legs are below the bar and are shown for context only — they are not picks.")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder
    private func rejectedFooter(_ report: HighOddsReport) -> some View {
        let visible = report.rejected
            .filter { $0.value > 0 }
            .sorted { $0.key < $1.key }
        if !visible.isEmpty {
            Text("Filtered out: " + visible.map { "\($0.value) \(Fmt.humanized($0.key))" }.joined(separator: " · ") + ".")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func scopeFooter(_ report: HighOddsReport) -> some View {
        Text(
            "Fixtures on the slate: \(report.scope.fixturesOnSlate) · analysed \(report.scope.fixturesAnalysed) · "
                + "candidate legs \(report.scope.candidateLegs) · \(report.diagnostics.combosInBand) "
                + "combination\(report.diagnostics.combosInBand == 1 ? "" : "s") inside the band before de-duplication."
        )
        .font(TypeScale.caption)
        .foregroundStyle(Palette.textTertiary)
        .fixedSize(horizontal: false, vertical: true)
    }

    @MainActor
    private func load() async {
        loading = true
        errorMessage = nil
        do {
            report = try await client.get("/markets/high-odds", timeout: 120, as: HighOddsReport.self)
        } catch {
            errorMessage = error.localizedDescription
        }
        loading = false
    }
}

private struct HighOddsLegRow: View {
    let leg: HighOddsLeg

    private var evLabel: String {
        String(format: "%+.1f%%", leg.expectedValue * 100)
    }

    var body: some View {
        NavigationLink {
            FixtureDetailView(fixtureId: leg.fixtureId)
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
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
                    Text("· \(Fmt.humanized(leg.marketType))")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                    Spacer(minLength: 6)
                    Text(evLabel)
                        .font(.system(size: 13, weight: .bold, design: .monospaced))
                        .foregroundStyle(leg.expectedValue > 0 ? Palette.accent : Palette.textSecondary)
                }
                HStack(spacing: 6) {
                    Text("\(leg.competition) · \(leg.kickoffEat)")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                        .lineLimit(1)
                    Spacer(minLength: 6)
                    Text("mkt margin \(String(format: "%.1f%%", (leg.marketOverround - 1) * 100))")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                }
                HStack(spacing: 6) {
                    Pill(text: Fmt.percent(leg.probability))
                    if let bookmaker = leg.bookmaker {
                        Pill(text: bookmaker, tint: Palette.textSecondary)
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
        .buttonStyle(.plain)
    }
}
