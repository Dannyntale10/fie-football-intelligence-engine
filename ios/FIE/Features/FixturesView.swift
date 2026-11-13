import SwiftUI

struct FixturesView: View {
    private enum Mode: String, CaseIterable {
        case upcoming = "Upcoming"
        case gate = "85% gate"
    }

    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL
    @AppStorage("fie.fixturesMode") private var storedMode = Mode.upcoming.rawValue

    @State private var mode: Mode = .upcoming
    @State private var fixtures: [Fixture] = []
    @State private var gate: GateReport?
    @State private var loading = false
    @State private var loadingGate = false
    @State private var errorMessage: String?
    @State private var gateError: String?
    @State private var searchText = ""

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    private var filtered: [Fixture] {
        let needle = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else { return fixtures }
        return fixtures.filter { fixture in
            fixture.homeTeam.lowercased().contains(needle)
                || fixture.awayTeam.lowercased().contains(needle)
                || fixture.competition.lowercased().contains(needle)
                || fixture.country.lowercased().contains(needle)
        }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("View", selection: $mode) {
                    ForEach(Mode.allCases, id: \.self) { item in
                        Text(item.rawValue).tag(item)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)

                ScrollView {
                    LazyVStack(spacing: 10) {
                        switch mode {
                        case .upcoming:
                            upcomingContent
                        case .gate:
                            gateContent
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 4)
                }
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Fixtures")
            .searchable(text: $searchText, prompt: "Team or competition")
            .task(id: mode) {
                await loadCurrent()
                storedMode = mode.rawValue
            }
            .onAppear {
                mode = Mode(rawValue: storedMode) ?? .upcoming
            }
            .refreshable { await loadCurrent() }
        }
    }

    @ViewBuilder
    private var upcomingContent: some View {
        if let errorMessage {
            NoteRow(text: errorMessage, tone: .danger)
        }
        if loading && fixtures.isEmpty {
            LoadingCard(label: "Reading the upcoming slate…")
        }
        ForEach(filtered) { fixture in
            NavigationLink {
                FixtureDetailView(fixture: fixture)
            } label: {
                FixtureCard(fixture: fixture)
            }
            .buttonStyle(.plain)
        }
        if !loading && filtered.isEmpty && errorMessage == nil {
            Card {
                Text("No fixtures on the slate.")
                    .font(TypeScale.section)
                Text("Refresh the bookmaker feeds from the Sources tab, or clear the search filter.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
            }
        }
    }

    @ViewBuilder
    private var gateContent: some View {
        if let gateError {
            NoteRow(text: gateError, tone: .danger)
        }
        if loadingGate && gate == nil {
            LoadingCard(label: "Scanning today's fixtures for the 85% gate…")
        }
        if let gate {
            Card {
                HStack {
                    Eyebrow(text: "Published gate")
                    Spacer()
                    Pill(text: "\(Int((gate.threshold * 100).rounded()))%", tint: Palette.accent)
                }
                Text(gate.message)
                    .font(TypeScale.section)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                LabeledRow(label: "Fixtures scanned", value: Fmt.count(gate.fixturesScanned))
                LabeledRow(label: "Markets clearing the gate", value: Fmt.count(gate.count))
                Text("Only markets at or above the published threshold are listed. The gate is never lowered to fill this screen; when nothing clears it, the screen says so.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if gate.qualifying.isEmpty {
                Card {
                    Text("NO QUALIFIED SELECTIONS FOUND.")
                        .font(.system(size: 15, weight: .bold, design: .monospaced))
                        .foregroundStyle(Palette.warn)
                    Text("Scan more fixtures, or wait for fresher prices. Nothing below \(Fmt.percent(gate.threshold, digits: 0)) is shown.")
                        .font(TypeScale.body)
                        .foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            ForEach(gate.qualifying) { row in
                NavigationLink {
                    FixtureDetailView(fixtureId: row.fixtureId)
                } label: {
                    GateRow(row: row)
                }
                .buttonStyle(.plain)
            }
        }
    }

    @MainActor
    private func loadCurrent() async {
        switch mode {
        case .upcoming:
            await load()
        case .gate:
            await loadGate()
        }
    }

    @MainActor
    private func loadGate() async {
        guard !loadingGate else { return }
        loadingGate = true
        gateError = nil
        defer { loadingGate = false }
        do {
            gate = try await client.get(
                "/markets/qualifying",
                timeout: 120,
                as: GateReport.self
            )
        } catch {
            gateError = error.localizedDescription
        }
    }

    @MainActor
    private func load() async {
        guard !loading else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            fixtures = try await client.get(
                "/fixtures",
                query: [
                    URLQueryItem(name: "upcoming_only", value: "true"),
                    URLQueryItem(name: "limit", value: "120"),
                ],
                as: [Fixture].self
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct FixtureCard: View {
    let fixture: Fixture

    var body: some View {
        Card {
            HStack {
                Eyebrow(text: fixture.competition)
                Spacer()
                Text(fixture.kickoffEat ?? "—")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textSecondary)
            }
            Text(fixture.matchLabel)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                OddsChip(label: "1", value: fixture.homeOdds)
                OddsChip(label: "X", value: fixture.drawOdds)
                OddsChip(label: "2", value: fixture.awayOdds)
                Spacer(minLength: 0)
            }
            HStack(spacing: 6) {
                Pill(text: fixture.source, tint: Palette.textSecondary)
                if let grade = fixture.dataQualityGrade {
                    Pill(text: "quality \(grade)", tint: grade == "A" || grade == "B" ? Palette.accent : Palette.warn)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Palette.textTertiary)
            }
        }
    }
}

private struct GateRow: View {
    let row: GateSelection

    var body: some View {
        Card {
            HStack {
                Eyebrow(text: row.competition)
                Spacer()
                Pill(text: Fmt.percent(row.probability), tint: Palette.accent)
            }
            Text(row.match)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text(row.market)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(Palette.accent)
            HStack(spacing: 6) {
                Text(row.kickoffEat)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Palette.textTertiary)
            }
            .font(TypeScale.caption)
            .foregroundStyle(Palette.textSecondary)
            KickoffCountdown(iso: row.kickoffUtc)
        }
    }
}

private struct OddsChip: View {
    let label: String
    let value: Double?

    var body: some View {
        VStack(spacing: 2) {
            Text(label)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Palette.textTertiary)
            Text(Fmt.odds(value))
                .font(.system(size: 14, weight: .bold, design: .monospaced))
                .foregroundStyle(Palette.textPrimary)
        }
        .frame(width: 52)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.white.opacity(0.05))
        )
    }
}
