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
    @AppStorage("fie.selectedTab") private var selectedTab = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            TodayView()
                .tag(0)
                .tabItem { Label("Today", systemImage: "sparkles") }

            FixturesView()
                .tag(1)
                .tabItem { Label("Fixtures", systemImage: "sportscourt") }

            PerformanceView()
                .tag(2)
                .tabItem { Label("Performance", systemImage: "chart.bar.xaxis") }

            SourcesView()
                .tag(3)
                .tabItem { Label("Sources", systemImage: "antenna.radiowaves.left.and.right") }

            SettingsView()
                .tag(4)
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}
