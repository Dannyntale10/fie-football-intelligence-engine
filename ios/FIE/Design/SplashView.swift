import SwiftUI

struct SplashView: View {
    let readyCount: Int
    let total: Int

    @State private var appeared = false

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            RadialGradient(
                colors: [Palette.accent.opacity(0.22), .clear],
                center: .center,
                startRadius: 10,
                endRadius: 420
            )
            .ignoresSafeArea()

            VStack(spacing: 18) {
                Spacer()

                Image("BrandLogo")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 120, height: 120)
                    .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 28, style: .continuous)
                            .stroke(Palette.accent.opacity(0.35), lineWidth: 1)
                    )
                    .shadow(color: Palette.accent.opacity(0.25), radius: 24, y: 8)

                Text("FIE")
                    .font(.system(size: 40, weight: .bold, design: .rounded))
                    .tracking(4)
                    .foregroundStyle(Palette.textPrimary)

                Text("Football Intelligence Engine")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)

                Spacer()

                VStack(spacing: 8) {
                    ProgressView()
                        .tint(Palette.accent)
                    Text("Loading · \(readyCount) / \(total) ready")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                        .monospacedDigit()
                }
                .padding(.bottom, 36)
            }
            .opacity(appeared ? 1 : 0)
            .scaleEffect(appeared ? 1 : 0.96)
        }
        .onAppear {
            withAnimation(.easeOut(duration: 0.45)) { appeared = true }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "Football Intelligence Engine is loading, \(readyCount) of \(total) sections ready"
        )
    }
}
