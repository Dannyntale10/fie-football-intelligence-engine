import SwiftUI

struct BankrollView: View {
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var stake = "50"
    @State private var odds = "1.20"
    @State private var bankroll = "1000"
    @State private var targetProfit = "10"
    @State private var slipsPerDay = "1"

    @State private var plan: BankrollPlanResponse?
    @State private var presets: BankrollPresets?
    @State private var loading = false
    @State private var errorMessage: String?

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                intro
                policyCard
                formCard
                if let errorMessage {
                    NoteRow(text: errorMessage, tone: .warn)
                }
                if loading && plan == nil {
                    LoadingCard(label: "Working out the paper arithmetic…")
                }
                if let plan {
                    planSummary(plan)
                    warnings(plan.warnings)
                    NoteRow(text: plan.note)
                    if let exposure = plan.dailyExposure {
                        exposureCard(exposure)
                    }
                }
                presetsSection
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Palette.background.ignoresSafeArea())
        .navigationTitle("Bankroll planner")
        .navigationBarTitleDisplayMode(.inline)
        .task { await calculate() }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "Paper staking")
            Text("What a target really requires")
                .font(TypeScale.section)
                .foregroundStyle(Palette.textPrimary)
            Text("Both readings of 50 over 10 use the same arithmetic: at 1.20 you stake 50 to profit 10 and need 83.3% of tickets to win; at 6.00 you stake 10 to profit 50 and need 16.7%. Nothing here predicts a result, and no stake is ever placed.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var policyCard: some View {
        Card {
            Eyebrow(text: "Policy caps")
            LabeledRow(label: "Per stake", value: "5% of bankroll")
            LabeledRow(label: "Per day", value: "20% of bankroll")
            Text("Caps are applied to the paper plan only. Exceeding one raises a warning; it never adjusts a real balance.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var formCard: some View {
        Card {
            Eyebrow(text: "Your plan")
            field("Stake", text: $stake, hint: "What you would risk on one slip")
            field("Total odds", text: $odds, hint: "Combined price, above 1.00")
            field("Bankroll", text: $bankroll, hint: "Total paper balance")
            field("Target profit", text: $targetProfit, hint: "Leave blank to skip")
            field("Slips per day", text: $slipsPerDay, hint: "For the daily exposure check")

            Button {
                Task { await calculate() }
            } label: {
                HStack(spacing: 8) {
                    if loading {
                        ProgressView()
                            .tint(Palette.background)
                            .controlSize(.small)
                    }
                    Text("Calculate")
                }
            }
            .buttonStyle(PrimaryButtonStyle(busy: loading))
            .disabled(loading || request() == nil)

            if request() == nil {
                Text("Enter a positive stake, total odds above 1.00 and a bankroll to calculate.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.warn)
            }
        }
    }

    private func field(_ label: String, text: Binding<String>, hint: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label.uppercased())
                .font(TypeScale.statLabel)
                .tracking(0.8)
                .foregroundStyle(Palette.textTertiary)
            TextField(label, text: text)
                .keyboardType(.decimalPad)
                .font(.system(size: 15, weight: .semibold, design: .monospaced))
                .foregroundStyle(Palette.textPrimary)
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(Palette.background)
                )
            Text(hint)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
        }
    }

    private func planSummary(_ plan: BankrollPlanResponse) -> some View {
        Card {
            Eyebrow(text: "Arithmetic")
            HStack(spacing: 12) {
                StatTile(
                    label: "Returns",
                    value: money(plan.potentialReturn, plan.currency),
                    caption: "\(money(plan.stake, plan.currency)) at \(Fmt.odds(plan.totalOdds))"
                )
                StatTile(
                    label: "Profit if it wins",
                    value: money(plan.potentialProfit, plan.currency),
                    caption: "before any losing ticket"
                )
            }
            HStack(spacing: 12) {
                StatTile(
                    label: "Break-even strike",
                    value: String(format: "%.1f%%", plan.requiredStrikeRatePercent),
                    caption: String(format: "about %.1f wins in 10", plan.breakEvenWinsPer10)
                )
                StatTile(
                    label: "Stake vs cap",
                    value: plan.maxStakeByPolicy.map { money($0, plan.currency) } ?? "no bankroll",
                    caption: plan.stakeWithinCap ? "within the cap" : "above the cap"
                )
            }
            DividerLine()
            if let share = plan.stakePercentOfBankroll {
                LabeledRow(
                    label: "Share of bankroll",
                    value: String(format: "%.1f%%", share),
                    mono: true
                )
            }
            if let target = plan.targetProfit, let needed = plan.stakeForTargetProfit {
                LabeledRow(
                    label: "Stake for \(money(target, plan.currency)) profit",
                    value: money(needed, plan.currency),
                    mono: true
                )
            }
            Text("\(String(format: "%.1f%%", plan.requiredStrikeRatePercent)) is the break-even strike for this price — not a forecast that it will happen.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func exposureCard(_ exposure: BankrollExposure) -> some View {
        Card {
            HStack {
                Eyebrow(text: "Daily exposure")
                Spacer()
                Pill(
                    text: exposure.exposureWithinCap ? "within the cap" : "above the cap",
                    tint: exposure.exposureWithinCap ? Palette.accent : Palette.warn
                )
            }
            Text("\(exposure.slipsPerDay) slip\(exposure.slipsPerDay == 1 ? "" : "s") per day at \(money(exposure.stakePerSlip, exposure.currency))")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
            LabeledRow(label: "Total staked", value: money(exposure.totalStake, exposure.currency), mono: true)
            LabeledRow(label: "Daily cap", value: money(exposure.maxDailyStake, exposure.currency), mono: true)
            LabeledRow(
                label: "Policy",
                value: String(format: "%.0f%% of bankroll", exposure.maxDailyExposurePercent),
                mono: true
            )
            warnings(exposure.warnings)
        }
    }

    @ViewBuilder
    private func warnings(_ rows: [BankrollWarning]) -> some View {
        ForEach(Array(rows.enumerated()), id: \.offset) { _, warning in
            NoteRow(text: warning.message, tone: .warn)
        }
    }

    @ViewBuilder
    private var presetsSection: some View {
        Card {
            Eyebrow(text: "The two readings of 50/10")
            if let presets {
                ForEach(presets.presets) { preset in
                    DividerLine()
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(preset.label)
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(Palette.textPrimary)
                            Spacer()
                            Pill(text: String(format: "%.1f%% strike", preset.plan.requiredStrikeRatePercent))
                        }
                        Text(preset.description)
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        LabeledRow(
                            label: "\(money(preset.plan.stake, preset.plan.currency)) at \(Fmt.odds(preset.plan.totalOdds))",
                            value: "profit \(money(preset.plan.potentialProfit, preset.plan.currency))",
                            mono: true
                        )
                    }
                }
                Text(presets.note)
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Preset readings load with the plan.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
            }
        }
    }

    private struct Request {
        let query: [URLQueryItem]
        let bankroll: Double
    }

    private func request() -> Request? {
        guard
            let stakeValue = Double(stake), stakeValue > 0,
            let oddsValue = Double(odds), oddsValue > 1,
            let bankrollValue = Double(bankroll), bankrollValue > 0
        else { return nil }
        let slips = max(1, Int(Double(slipsPerDay) ?? 1))
        var query = [
            URLQueryItem(name: "stake", value: String(stakeValue)),
            URLQueryItem(name: "odds", value: String(oddsValue)),
            URLQueryItem(name: "bankroll", value: String(bankrollValue)),
            URLQueryItem(name: "slips_per_day", value: String(slips)),
        ]
        if let target = Double(targetProfit), target >= 0 {
            query.append(URLQueryItem(name: "target_profit", value: String(target)))
        }
        return Request(query: query, bankroll: bankrollValue)
    }

    @MainActor
    private func calculate() async {
        guard let request = request() else { return }
        loading = true
        errorMessage = nil
        do {
            async let planLoad = client.get(
                "/bankroll/plan",
                query: request.query,
                timeout: 30,
                as: BankrollPlanResponse.self
            )
            async let presetLoad = client.get(
                "/bankroll/presets",
                query: [URLQueryItem(name: "bankroll", value: String(request.bankroll))],
                timeout: 30,
                as: BankrollPresets.self
            )
            let (loadedPlan, loadedPresets) = try await (planLoad, presetLoad)
            plan = loadedPlan
            presets = loadedPresets
        } catch {
            errorMessage = error.localizedDescription
        }
        loading = false
    }

    private func money(_ value: Double, _ currency: String) -> String {
        let digits = currency.uppercased() == "UGX" ? 0 : 2
        return String(format: "%.\(digits)f %@", value, currency)
    }
}
