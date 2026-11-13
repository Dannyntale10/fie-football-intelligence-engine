import SwiftUI

struct Card<Content: View>: View {
    private let content: Content
    private let glow: Bool

    init(glow: Bool = false, @ViewBuilder content: () -> Content) {
        self.glow = glow
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            ZStack {
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(Palette.card)
                if glow {
                    RadialGradient(
                        colors: [Palette.accent.opacity(0.18), .clear],
                        center: .topTrailing,
                        startRadius: 4,
                        endRadius: 260
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                }
            }
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(glow ? Palette.accent.opacity(0.28) : Palette.cardStroke, lineWidth: 1)
        )
    }
}

struct Eyebrow: View {
    let text: String
    var tint: Color = Palette.textTertiary

    var body: some View {
        Text(text.uppercased())
            .font(TypeScale.eyebrow)
            .tracking(1.4)
            .foregroundStyle(tint)
    }
}

struct StatTile: View {
    let label: String
    let value: String
    var caption: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label.uppercased())
                .font(TypeScale.statLabel)
                .tracking(0.8)
                .foregroundStyle(Palette.textTertiary)
            Text(value)
                .font(TypeScale.statValue)
                .foregroundStyle(Palette.textPrimary)
                .monospacedDigit()
                .minimumScaleFactor(0.7)
                .lineLimit(1)
            if let caption {
                Text(caption)
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textSecondary)
                    .lineLimit(2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

enum NoteTone {
    case info, good, warn, danger

    var foreground: Color {
        switch self {
        case .info, .good: return Palette.accent
        case .warn: return Palette.warn
        case .danger: return Palette.danger
        }
    }

    var background: Color {
        switch self {
        case .info, .good: return Palette.accentSoft
        case .warn: return Palette.warnSoft
        case .danger: return Palette.dangerSoft
        }
    }

    var icon: String {
        switch self {
        case .info, .good: return "checkmark.circle"
        case .warn: return "exclamationmark.triangle"
        case .danger: return "xmark.octagon"
        }
    }
}

struct NoteRow: View {
    let text: String
    var tone: NoteTone = .info

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: tone.icon)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(tone.foreground)
                .padding(.top, 1)
            Text(text)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textPrimary.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(tone.background)
        )
    }
}

struct Pill: View {
    let text: String
    var tint: Color = Palette.accent

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10, weight: .bold))
            .tracking(0.6)
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                Capsule().fill(tint.opacity(0.14))
            )
    }
}

struct LoadingCard: View {
    let label: String

    var body: some View {
        HStack(spacing: 12) {
            ProgressView().tint(Palette.accent)
            Text(label)
                .font(TypeScale.body)
                .foregroundStyle(Palette.textSecondary)
            Spacer(minLength: 0)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Palette.card)
        )
    }
}

struct LabeledRow: View {
    let label: String
    let value: String
    var valueTint: Color = Palette.textPrimary
    var mono: Bool = false

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
            Spacer(minLength: 12)
            Text(value)
                .font(mono ? .system(size: 13, weight: .semibold, design: .monospaced)
                           : .system(size: 13, weight: .semibold))
                .foregroundStyle(valueTint)
                .multilineTextAlignment(.trailing)
        }
    }
}

struct DividerLine: View {
    var body: some View {
        Rectangle()
            .fill(Palette.divider)
            .frame(height: 1)
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    var busy: Bool = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(Palette.background)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(busy ? Palette.accent.opacity(0.55) : Palette.accent)
            )
            .opacity(configuration.isPressed ? 0.82 : 1)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(Palette.accent)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Palette.accentSoft)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Palette.accent.opacity(0.35), lineWidth: 1)
            )
            .opacity(configuration.isPressed ? 0.75 : 1)
    }
}

struct KickoffCountdown: View {
    let iso: String?

    var body: some View {
        TimelineView(.everyMinute) { context in
            if let kickoff = Fmt.date(iso) {
                let state = Self.state(minutes: Int(kickoff.timeIntervalSince(context.date) / 60))
                Text(state.text)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(state.tint)
                    .monospacedDigit()
            }
        }
    }

    static func state(minutes: Int) -> (text: String, tint: Color) {
        if minutes < 0 { return ("Kickoff passed", Palette.danger) }
        if minutes < 60 { return ("Kickoff in \(minutes)m", Palette.warn) }
        let hours = minutes / 60
        let rest = minutes % 60
        if rest == 0 { return ("Kickoff in \(hours)h", Palette.textSecondary) }
        return ("Kickoff in \(hours)h \(rest)m", Palette.textSecondary)
    }
}

struct ProbabilityBar: View {
    let model: Double
    let market: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.white.opacity(0.08))
                    Capsule()
                        .fill(
                            LinearGradient(
                                colors: [Palette.accent.opacity(0.35), Palette.accent],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .frame(width: max(2, geometry.size.width * clamped(model)))
                    Capsule()
                        .fill(Palette.textPrimary)
                        .frame(width: 2, height: 12)
                        .offset(x: min(geometry.size.width - 2, geometry.size.width * clamped(market)))
                }
            }
            .frame(height: 6)
            HStack(spacing: 10) {
                Text("model \(Fmt.percent(model))")
                    .foregroundStyle(Palette.accent)
                Text("market \(Fmt.percent(market))")
                    .foregroundStyle(Palette.textSecondary)
                Spacer(minLength: 0)
            }
            .font(.system(size: 10, weight: .semibold))
            .monospacedDigit()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Model probability \(Fmt.percent(model)) against market probability \(Fmt.percent(market))")
    }

    private func clamped(_ value: Double) -> CGFloat {
        CGFloat(min(max(value, 0), 1))
    }
}

enum PriceFreshness {
    static func tint(hours: Double) -> Color {
        if hours >= 12 { return Palette.danger }
        if hours >= 2 { return Palette.warn }
        return Palette.textTertiary
    }
}

struct StatusPill: View {
    let status: String?

    private var tint: Color {
        switch (status ?? "").lowercased() {
        case "ok", "success", "running", "configured", "applied": return Palette.accent
        case "failed", "error": return Palette.danger
        case "partial", "skipped": return Palette.warn
        default: return Palette.textSecondary
        }
    }

    var body: some View {
        Pill(text: status ?? "unknown", tint: tint)
    }
}

struct LiveDot: View {
    @State private var pulsing = false

    var body: some View {
        Circle()
            .fill(Palette.danger)
            .frame(width: 8, height: 8)
            .scaleEffect(pulsing ? 1.35 : 0.9)
            .opacity(pulsing ? 0.55 : 1)
            .animation(
                .easeInOut(duration: 0.9).repeatForever(autoreverses: true),
                value: pulsing
            )
            .onAppear { pulsing = true }
            .accessibilityHidden(true)
    }
}

struct LiveScoreRow: View {
    let fixture: Fixture

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(fixture.matchLabel)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Palette.textPrimary)
                    .lineLimit(2)
                Text(fixture.competition)
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            if let home = fixture.homeScore, let away = fixture.awayScore {
                Text("\(home) – \(away)")
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.textPrimary)
            }

            if let clock = fixture.liveClock {
                Pill(text: clock, tint: Palette.danger)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}

struct LiveMatchesCard: View {
    let fixtures: [Fixture]
    var maxRows: Int? = nil
    var glow: Bool = false

    private var shown: [Fixture] {
        guard let maxRows else { return fixtures }
        return Array(fixtures.prefix(maxRows))
    }

    var body: some View {
        Card(glow: glow) {
            HStack(spacing: 8) {
                LiveDot()
                Eyebrow(text: "Live now", tint: Palette.danger)
                Spacer()
                Pill(
                    text: fixtures.count > shown.count
                        ? "\(fixtures.count) in play"
                        : "\(fixtures.count) live",
                    tint: Palette.danger
                )
            }

            ForEach(shown) { fixture in
                NavigationLink {
                    FixtureDetailView(fixture: fixture)
                } label: {
                    LiveScoreRow(fixture: fixture)
                }
                .buttonStyle(.plain)
            }
        }
    }
}
