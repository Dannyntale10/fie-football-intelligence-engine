import SwiftUI

struct Card<Content: View>: View {
    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(Palette.card)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(Palette.cardStroke, lineWidth: 1)
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
