import SwiftUI

struct FixturesView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var fixtures: [Fixture] = []
    @State private var loading = false
    @State private var errorMessage: String?
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
            ScrollView {
                LazyVStack(spacing: 10) {
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
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Fixtures")
            .searchable(text: $searchText, prompt: "Team or competition")
            .task { await load() }
            .refreshable { await load() }
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
