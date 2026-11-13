import SwiftUI

@main
struct FIEApp: App {
    @StateObject private var store = EngineStore()

    init() {
        #if DEBUG
        // Lets a simulator or device QA run seed the key without typing it;
        // Release builds never read this.
        if let seed = ProcessInfo.processInfo.environment["FIE_API_KEY_SEED"], !seed.isEmpty {
            AppSettings.saveAPIKey(seed)
        }
        #endif
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
                .tint(Palette.accent)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var store: EngineStore
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("fie.selectedTab") private var selectedTab = 0
    @State private var showSplash = true

    var body: some View {
        ZStack {
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

                LiveView()
                    .tag(3)
                    .tabItem { Label("Live", systemImage: "dot.radiowaves.left.and.right") }

                SettingsView()
                    .tag(4)
                    .tabItem { Label("Settings", systemImage: "gearshape") }
            }

            if showSplash {
                SplashView(
                    readyCount: store.readyCount,
                    total: EngineSection.allCases.count
                )
                .transition(.opacity)
                .zIndex(1)
            }
        }
        .task { await loadUntilReady() }
        .onAppear {
            // iOS 17 only shows five tabs; a sixth would push Live and Settings
            // behind "More". Sources now lives inside Settings.
            if selectedTab == 5 { selectedTab = 3 }
            if selectedTab > 4 { selectedTab = 0 }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                Task {
                    await store.refreshLive()
                    store.startAutoRefresh(section: EngineSection.forTab(selectedTab))
                }
            case .background, .inactive:
                store.stopAutoRefresh()
            @unknown default:
                break
            }
        }
        .onChange(of: selectedTab) { _, tab in
            store.setActiveSection(EngineSection.forTab(tab))
        }
    }

    @MainActor
    private func loadUntilReady() async {
        let started = Date()
        Task { await store.bootstrap() }

        while !store.isBootstrapped && Date().timeIntervalSince(started) < 6 {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }

        let remaining = 1.0 - Date().timeIntervalSince(started)
        if remaining > 0 {
            try? await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000))
        }

        withAnimation(.easeOut(duration: 0.35)) { showSplash = false }
        store.startAutoRefresh(section: EngineSection.forTab(selectedTab))
    }
}
