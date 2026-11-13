import SwiftUI

struct LiveView: View {
    @EnvironmentObject private var store: EngineStore

    private var errorMessage: String? { store.errors[.live] }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 12) {
                    NoteRow(
                        text: "Model estimate, not a price. Informational only. Live numbers never enter the 85% gate, a slip, or a staking recommendation.",
                        tone: .warn
                    )
                    if let errorMessage {
                        NoteRow(text: errorMessage, tone: .danger)
                    }
                    if store.liveUnreadable > 0 {
                        NoteRow(
                            text: "\(store.liveUnreadable) in-play fixture(s) could not be read from the engine and are not shown.",
                            tone: .warn
                        )
                    }
                    if store.live.isEmpty && errorMessage == nil && store.liveUnreadable == 0 {
                        emptyState
                    }
                    ForEach(store.live) { fixture in
                        NavigationLink(value: fixture.id) {
                            LiveFixtureCard(fixture: fixture)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Live")
            .refreshable { await store.refreshLive() }
            .navigationDestination(for: Int.self) { id in
                FixtureDetailView(fixtureId: id)
            }
        }
        .task {
            await store.refreshLive()
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                if Task.isCancelled { return }
                await store.refreshLive()
            }
        }
    }

    private var emptyState: some View {
        Card {
            Text("No matches in play")
                .font(.headline)
                .foregroundStyle(Palette.textPrimary)
            Text("Nothing is being played right now. This tab refreshes every 30 seconds.")
                .font(.footnote)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct LiveFixtureCard: View {
    let fixture: Fixture

    private var prediction: LivePrediction? { fixture.livePrediction }

    var body: some View {
        Card {
            HStack {
                Text(fixture.matchLabel)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if let freshness = prediction?.freshness {
                    Text(freshness.uppercased())
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(freshness == "fresh" ? Palette.accent : Palette.warn)
                }
            }

            Text(fixture.competition)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .lineLimit(1)

            HStack(alignment: .firstTextBaseline) {
                Text(scoreLabel(fixture.homeScore, fixture.awayScore))
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundStyle(Palette.textPrimary)
                Spacer()
                Text(fixture.liveClock ?? prediction.map { "\($0.minute)'" } ?? "—")
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(Palette.textSecondary)
            }

            if let probabilities = prediction?.probabilities {
                HStack(spacing: 12) {
                    probability("Home", probabilities.homeWin)
                    probability("Draw", probabilities.draw)
                    probability("Away", probabilities.awayWin)
                }
            } else if let status = prediction?.status {
                Text("No forecast: \(status.replacingOccurrences(of: "_", with: " "))")
                    .font(.caption)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Waiting for the engine's first in-play snapshot.")
                    .font(.caption)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text("Model estimate, not a price. Informational only.")
                .font(.caption2)
                .foregroundStyle(Palette.textTertiary)
        }
    }

    private func probability(_ label: String, _ value: Double?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(Palette.textTertiary)
            Text(value.map { $0.formatted(.percent.precision(.fractionLength(1))) } ?? "—")
                .font(.footnote.monospacedDigit().weight(.semibold))
                .foregroundStyle(Palette.textPrimary)
        }
    }

    /// A missing score is reported as missing, never rendered as 0 : 0.
    private func scoreLabel(_ home: Int?, _ away: Int?) -> String {
        "\(home.map(String.init) ?? "–") : \(away.map(String.init) ?? "–")"
    }
}
