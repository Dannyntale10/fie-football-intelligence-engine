import SwiftUI

enum Palette {
    static let background = Color(red: 0.043, green: 0.055, blue: 0.047)
    static let card = Color(red: 0.078, green: 0.102, blue: 0.086)
    static let cardStroke = Color.white.opacity(0.08)
    static let accent = Color(red: 0.169, green: 0.851, blue: 0.486)
    static let accentSoft = Color(red: 0.169, green: 0.851, blue: 0.486).opacity(0.14)
    static let warn = Color(red: 0.980, green: 0.729, blue: 0.251)
    static let warnSoft = Color(red: 0.980, green: 0.729, blue: 0.251).opacity(0.14)
    static let danger = Color(red: 0.949, green: 0.361, blue: 0.361)
    static let dangerSoft = Color(red: 0.949, green: 0.361, blue: 0.361).opacity(0.14)
    static let textPrimary = Color.white
    static let textSecondary = Color.white.opacity(0.62)
    static let textTertiary = Color.white.opacity(0.42)
    static let divider = Color.white.opacity(0.07)
}

enum TypeScale {
    static let eyebrow = Font.system(size: 11, weight: .semibold).width(.expanded)
    static let title = Font.system(size: 28, weight: .bold)
    static let section = Font.system(size: 17, weight: .semibold)
    static let body = Font.system(size: 15, weight: .regular)
    static let caption = Font.system(size: 12, weight: .regular)
    static let statValue = Font.system(size: 24, weight: .semibold, design: .rounded)
    static let statLabel = Font.system(size: 11, weight: .medium)
}
