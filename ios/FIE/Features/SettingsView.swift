import SwiftUI

struct SettingsView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var testing = false
    @State private var testNote: String?
    @State private var testError: String?

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
                    policy
                    about
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Settings")
        }
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
            TextField("http://localhost:8001", text: $serverURL)
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

            Text("The iOS Simulator reaches the Mac at localhost. A physical iPhone needs the Mac's LAN address, for example http://192.168.1.20:8001, with the backend bound to 0.0.0.0 and Local Network access allowed.")
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
