import SwiftUI

struct SourcesView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var sources: SourcesResponse?
    @State private var logs: [SyncLog] = []
    @State private var loading = false
    @State private var refreshing = false
    @State private var errorMessage: String?
    @State private var refreshError: String?
    @State private var refreshNote: String?

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    private var busy: Bool { loading || refreshing }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    intro

                    Button {
                        Task { await refreshFeeds() }
                    } label: {
                        HStack(spacing: 8) {
                            if refreshing {
                                ProgressView()
                                    .tint(Palette.background)
                                    .controlSize(.small)
                            }
                            Text(refreshing ? "Refreshing bookmaker feeds…" : "Refresh bookmaker feeds")
                        }
                    }
                    .buttonStyle(PrimaryButtonStyle(busy: busy))
                    .disabled(busy)

                    if let refreshNote {
                        NoteRow(text: refreshNote)
                    }
                    if let refreshError {
                        NoteRow(text: refreshError, tone: .warn)
                    }
                    if let errorMessage {
                        NoteRow(text: errorMessage, tone: .danger)
                    }
                    if loading && sources == nil {
                        LoadingCard(label: "Reading provider readiness…")
                    }

                    if let sources {
                        providersCard(sources)
                    }
                    if !logs.isEmpty {
                        logsCard
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Sources")
            .task { await load() }
            .refreshable { await load() }
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "Keyless bookmaker feeds")
            Text("Data sources")
                .font(TypeScale.title)
                .foregroundStyle(Palette.textPrimary)
            Text("BetPawa, SportPesa, FANSPORT and Betika are read from their public feeds. Every price keeps its bookmaker and capture time, and a feed returning nothing never creates placeholder odds.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func providersCard(_ sources: SourcesResponse) -> some View {
        Card {
            HStack {
                Eyebrow(text: "Providers")
                Spacer()
                Pill(text: "\(sources.configuredCount) configured")
            }
            ForEach(sources.providers) { provider in
                DividerLine()
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text(provider.name.replacingOccurrences(of: "_", with: " ").capitalizedFirst)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(Palette.textPrimary)
                        Spacer()
                        StatusPill(status: provider.configured ? "configured" : "needs key")
                    }
                    HStack(spacing: 6) {
                        if let plan = provider.freePlan {
                            Pill(text: plan, tint: Palette.textSecondary)
                        }
                        if let quota = provider.dailyQuota {
                            Pill(text: "quota \(quota)", tint: Palette.textSecondary)
                        }
                        if let status = provider.status {
                            StatusPill(status: status)
                        }
                        Spacer(minLength: 0)
                    }
                    if let lastSync = provider.lastSyncAt {
                        Text("Last sync \(Fmt.timestamp(lastSync))")
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textTertiary)
                    }
                    if let notes = provider.notes, !notes.isEmpty {
                        Text(notes)
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            Text(sources.note)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var logsCard: some View {
        Card {
            Eyebrow(text: "Recent syncs")
            ForEach(logs) { log in
                DividerLine()
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(log.entity.replacingOccurrences(of: "_", with: " ").capitalizedFirst)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Palette.textPrimary)
                        Spacer()
                        StatusPill(status: log.status)
                    }
                    Text("\(log.recordsWritten) records · \(log.requestCount) requests · \(Fmt.timestamp(log.startedAt))")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textSecondary)
                    if let message = log.message, !message.isEmpty {
                        Text(message)
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    @MainActor
    private func load() async {
        guard !busy else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            async let sourcesTask: SourcesResponse = client.get("/sources", as: SourcesResponse.self)
            async let logsTask: [SyncLog] = client.get(
                "/sources/sync-logs",
                query: [URLQueryItem(name: "limit", value: "12")],
                as: [SyncLog].self
            )
            let (loadedSources, loadedLogs) = try await (sourcesTask, logsTask)
            sources = loadedSources
            logs = loadedLogs
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    @MainActor
    private func refreshFeeds() async {
        guard !busy else { return }
        refreshing = true
        errorMessage = nil
        refreshError = nil
        refreshNote = nil
        do {
            let result = try await client.post(
                "/sources/refresh",
                timeout: 900,
                as: SourcesRefreshResult.self
            )
            applyRefreshSummary(result)
            refreshing = false
            await load()
        } catch {
            refreshError = error.localizedDescription
        }
        refreshing = false
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
