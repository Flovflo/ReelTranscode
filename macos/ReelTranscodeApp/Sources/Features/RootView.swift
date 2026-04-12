import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppViewModel

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
                .navigationSplitViewStyle(.balanced)
                .frame(minWidth: 1000, minHeight: 680)
                .toolbar {
                    ToolbarItemGroup {
                        Button {
                            Task { await model.refreshAll(reportErrors: true) }
                        } label: {
                            Label("Refresh", systemImage: "arrow.clockwise")
                        }
                        .disabled(model.isRefreshing)

                        Button {
                            Task { await model.runBatch() }
                        } label: {
                            Label("Run Library Now", systemImage: "play.fill")
                        }
                        .disabled(model.isBusy)

                        Button {
                            if model.isServiceRunning {
                                model.stopWatchService()
                            } else {
                                model.startWatchService()
                            }
                        } label: {
                            Label(
                                model.isServiceRunning ? "Stop Watch" : "Start Watch",
                                systemImage: model.isServiceRunning ? "stop.circle.fill" : "play.circle.fill"
                            )
                        }
                        .disabled(model.isBusy)
                    }
                }
            }
        }
        .task(id: model.onboardingRequired) {
            await model.runAutomaticRefreshLoop()
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
        case .ingest:
            IngestView()
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
        case .ingest:
            return "square.stack.3d.up.fill"
        case .jobs:
            return "list.bullet.rectangle"
        case .configuration:
            return "slider.horizontal.3"
        case .logs:
            return "doc.plaintext"
        }
    }
}
