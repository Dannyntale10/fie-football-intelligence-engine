import SwiftUI

struct SettingsView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL
    @EnvironmentObject private var store: EngineStore

    @State private var testing = false
    @State private var testNote: String?
    @State private var testError: String?
    @State private var apiKey = ""
    @State private var keyNote: String?
    @State private var notifications: [DeliveryNotification] = []
    @State private var deliveryError: String?
    @State private var showSources = false

    private var trimmedURL: String {
        serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var docsURL: URL? {
        guard let base = URL(string: trimmedURL) else { return nil }
        return base.appendingPathComponent("docs")
    }

    private var version: String {
        let short = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1"
        return "\(short) (\(build))"
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    intro
                    engineAddress
                    if let testNote {
                        NoteRow(text: testNote)
                    }
                    if let testError {
                        NoteRow(text: testError, tone: .danger)
                    }
                    delivery
                    sourcesLink
                    policy
                    about
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Settings")
            .sheet(isPresented: $showSources) { SourcesView() }
            .task {
                apiKey = AppSettings.apiKey
                await loadDelivery()
            }
            .refreshable { await loadDelivery() }
            .onChange(of: serverURL) { _, _ in
                store.reset()
                Task {
                    await store.bootstrap()
                    store.startAutoRefresh(section: .health)
                }
            }
        }
    }

    private var delivery: some View {
        Card {
            HStack {
                Eyebrow(text: "Telegram delivery")
                Spacer()
                if let latest = notifications.first {
                    StatusPill(status: latest.status)
                }
            }
            if let deliveryError {
                Text(deliveryError)
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.warn)
                    .fixedSize(horizontal: false, vertical: true)
            } else if notifications.isEmpty {
                Text("No notification has been queued yet. The 07:30 EAT shortlist job queues one each morning.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                let pending = notifications.filter { $0.status == "pending" }.count
                let sent = notifications.filter { $0.status == "sent" }.count
                let failed = notifications.filter { $0.status == "failed" }.count
                HStack(spacing: 12) {
                    StatTile(label: "Pending", value: "\(pending)")
                    StatTile(label: "Sent", value: "\(sent)", caption: "most recent \(notifications.count)")
                    StatTile(label: "Failed", value: "\(failed)")
                }
                ForEach(notifications.prefix(3)) { row in
                    DividerLine()
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.subject ?? row.channel.capitalizedFirst)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(Palette.textPrimary)
                                .lineLimit(1)
                            Text(Fmt.timestamp(row.sentAt ?? row.createdAt))
                                .font(TypeScale.caption)
                                .foregroundStyle(Palette.textTertiary)
                        }
                        Spacer(minLength: 8)
                        StatusPill(status: row.status)
                    }
                }
            }
            Text("Queued notifications are delivered every 2 minutes between 06:00 and 23:00 EAT. Rows stay pending until TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS are set on the server, and the bot refuses every chat that is not allowlisted.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @MainActor
    private func loadDelivery() async {
        do {
            notifications = try await client.get(
                "/notifications",
                query: [URLQueryItem(name: "limit", value: "25")],
                timeout: 20,
                as: [DeliveryNotification].self
            )
            deliveryError = nil
        } catch {
            deliveryError = "Delivery status unavailable: \(error.localizedDescription)"
        }
    }

    private var client: APIClient {
        APIClient(baseURL: URL(string: trimmedURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "Connection")
            Text("Engine settings")
                .font(TypeScale.title)
                .foregroundStyle(Palette.textPrimary)
            Text("The app is a client for your own football intelligence engine. It stores no picks locally and never places a bet.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var engineAddress: some View {
        Card {
            Eyebrow(text: "Engine address")
            TextField(AppSettings.defaultServerURL, text: $serverURL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .font(.system(size: 15, weight: .medium, design: .monospaced))
                .foregroundStyle(Palette.textPrimary)
                .tint(Palette.accent)
                .padding(12)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(Color.white.opacity(0.06))
                )

            HStack(spacing: 10) {
                Button {
                    Task { await testConnection() }
                } label: {
                    HStack(spacing: 6) {
                        if testing {
                            ProgressView().controlSize(.small).tint(Palette.accent)
                        }
                        Text(testing ? "Testing…" : "Test connection")
                    }
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(testing)

                Button("Reset") {
                    serverURL = AppSettings.defaultServerURL
                    testNote = nil
                    testError = nil
                }
                .buttonStyle(SecondaryButtonStyle())
            }

            Text("The default is the hosted engine, which works on any network. To use the backend on your Mac instead, enter its LAN address here — both devices must be on the same Wi-Fi.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)

            Divider().overlay(Color.white.opacity(0.08))

            Eyebrow(text: "Engine key")
            SecureField("Paste the API key", text: $apiKey)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.system(size: 15, weight: .medium, design: .monospaced))
                .foregroundStyle(Palette.textPrimary)
                .tint(Palette.accent)
                .padding(12)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(Color.white.opacity(0.06))
                )

            HStack(spacing: 10) {
                Button("Save key") { saveKey() }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                if let keyNote {
                    Text(keyNote)
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                }
            }

            Text("The hosted engine rejects requests without this key. It is stored in the iOS Keychain and never leaves the device.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)

            if let docsURL {
                Link(destination: docsURL) {
                    HStack(spacing: 6) {
                        Image(systemName: "book")
                        Text("Open the API docs")
                    }
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.accent)
                }
            }
        }
    }

    private var sourcesLink: some View {
        Card {
            Eyebrow(text: "Data sources")
            Text("Bookmaker feed readiness and the latest sync log. Every price keeps its bookmaker and capture time; a feed returning nothing never creates placeholder odds.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Button {
                showSources = true
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "antenna.radiowaves.left.and.right")
                    Text("Open data sources")
                }
            }
            .buttonStyle(SecondaryButtonStyle())
        }
    }

    private var policy: some View {
        Card {
            Eyebrow(text: "Rules this app will not break")
            policyRow("Never fabricate fixtures, odds, injuries, lineups or statistics.")
            policyRow("Never lower the 85% gate to fill a report.")
            policyRow("Never present an estimate as a BetPawa price.")
            policyRow("Never hide losing predictions — history is append-only.")
            policyRow("Never place bets automatically.")
            Text("Paper trading and research only. Verify the model's walk-forward calibration before staking real money.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func policyRow(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text("•")
                .foregroundStyle(Palette.accent)
            Text(text)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var about: some View {
        Card {
            Eyebrow(text: "About")
            LabeledRow(label: "App version", value: version)
            LabeledRow(label: "Mode", value: "Paper only")
            LabeledRow(label: "Automatic bet placement", value: "Disabled by policy")
            LabeledRow(label: "Time zone", value: "EAT (Africa/Kampala)")
        }
    }

    @MainActor
    private func saveKey() {
        AppSettings.saveAPIKey(apiKey)
        apiKey = AppSettings.apiKey
        keyNote = apiKey.isEmpty ? "Key cleared." : "Key saved."
        testNote = nil
        testError = nil
        store.reset()
        Task {
            await store.bootstrap()
            store.startAutoRefresh(section: .health)
        }
    }

    private func testConnection() async {
        testNote = nil
        testError = nil
        guard let base = URL(string: trimmedURL) else {
            testError = "That server address is not a valid URL."
            return
        }
        testing = true
        defer { testing = false }
        do {
            let health = try await APIClient(baseURL: base).get("/health", timeout: 15, as: Health.self)
            testNote = "Reachable · \(Fmt.count(health.totalFixtures)) fixtures · database \(health.database) · published gate \(Fmt.percent(health.minPredictionProbability, digits: 0))."
        } catch {
            testError = error.localizedDescription
        }
    }
}
