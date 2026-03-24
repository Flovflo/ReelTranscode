import SwiftUI

struct IngestControlsSection: View {
    @EnvironmentObject private var model: AppViewModel
    let data: IngestViewData

    var body: some View {
        GroupBox("Library Control") {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    controlButton(data.runtime.watchRunning ? "Stop" : "Start", action: toggleWatch)
                    controlButton(data.runtime.watchPaused ? "Resume Intake" : "Pause Intake", action: togglePause)
                    controlButton("Scan Existing Files", action: runBatch)
                    controlButton("Refresh", action: refresh)
                }

                HStack(spacing: 14) {
                    statusBadge(
                        title: data.runtime.watchRunning ? "Watcher Live" : "Watcher Stopped",
                        tint: data.runtime.watchRunning ? .green : .secondary
                    )
                    statusBadge(
                        title: data.runtime.watchPaused ? "Intake Paused" : "Intake Open",
                        tint: data.runtime.watchPaused ? .orange : .blue
                    )
                    statusBadge(title: "\(data.runtime.activeWorkers)/\(max(1, data.runtime.maxWorkers)) workers", tint: .purple)
                    statusBadge(title: "\(data.runtime.queuedPaths) queued", tint: .indigo)
                }
            }
        }
    }

    private func controlButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.borderedProminent)
    }

    private func statusBadge(title: String, tint: Color) -> some View {
        Text(title)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(tint.opacity(0.12), in: Capsule())
            .foregroundStyle(tint)
    }

    private func toggleWatch() {
        if data.runtime.watchRunning {
            model.stopWatchService()
        } else {
            model.startWatchService()
        }
    }

    private func togglePause() {
        Task {
            if data.runtime.watchPaused {
                await model.resumeWatch()
            } else {
                await model.pauseWatch()
            }
        }
    }

    private func runBatch() {
        Task { await model.runBatch() }
    }

    private func refresh() {
        Task { await model.refreshStatus() }
    }
}
