import SwiftUI

@main
struct FIEApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
                .preferredColorScheme(.dark)
                .tint(Palette.accent)
        }
    }
}

struct RootView: View {
    var body: some View {
        TabView {
            TodayView()
                .tabItem { Label("Today", systemImage: "sparkles") }

            FixturesView()
                .tabItem { Label("Fixtures", systemImage: "sportscourt") }

            PerformanceView()
                .tabItem { Label("Performance", systemImage: "chart.bar.xaxis") }

            SourcesView()
                .tabItem { Label("Sources", systemImage: "antenna.radiowaves.left.and.right") }

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}
