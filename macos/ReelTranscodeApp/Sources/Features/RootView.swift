import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppViewModel
    private let refreshTimer = Timer.publish(every: 2.0, on: .main, in: .common).autoconnect()

    var body: some View {
        Group {
            if model.onboardingRequired {
                OnboardingView()
            } else {
                NavigationSplitView {
                    List(SidebarSection.allCases, selection: $model.selectedSection) { section in
                        Label(section.rawValue, systemImage: icon(for: section))
                            .tag(section)
                    }
                    .navigationTitle("ReelTranscode")
                } detail: {
                    detailView
                }
                .frame(minWidth: 1000, minHeight: 680)
            }
        }
        .onReceive(refreshTimer) { _ in
            guard !model.onboardingRequired else { return }
            Task {
                await model.refreshStatus()
                model.refreshLaunchdStatus()
                model.refreshLogs()
            }
        }
        .alert("Error", isPresented: Binding(
            get: { model.lastError != nil },
            set: { value in if !value { model.lastError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.lastError ?? "Unknown error")
        }
    }

    @ViewBuilder
    private var detailView: some View {
        switch model.selectedSection ?? .dashboard {
        case .dashboard:
            DashboardView()
        case .jobs:
            JobsView()
        case .configuration:
            ConfigurationView()
        case .logs:
            LogsView()
        }
    }

    private func icon(for section: SidebarSection) -> String {
        switch section {
        case .dashboard:
            return "speedometer"
        case .jobs:
            return "list.bullet.rectangle"
        case .configuration:
            return "slider.horizontal.3"
        case .logs:
            return "doc.plaintext"
        }
    }
}
