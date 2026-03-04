import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var model: AppViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Dashboard")
                    .font(.largeTitle.weight(.semibold))

                HStack(spacing: 12) {
                    statCard(title: "Total", value: model.status?.summary.total ?? 0)
                    statCard(title: "Running", value: model.status?.summary.running ?? 0)
                    statCard(title: "Success", value: model.status?.summary.success ?? 0)
                    statCard(title: "Failed", value: model.status?.summary.failed ?? 0)
                    statCard(title: "Skipped", value: model.status?.summary.skipped ?? 0)
                }

                GroupBox("Service") {
                    HStack {
                        Label(model.isServiceRunning ? "Running" : "Stopped", systemImage: model.isServiceRunning ? "checkmark.circle.fill" : "xmark.circle")
                            .foregroundStyle(model.isServiceRunning ? .green : .secondary)
                        Spacer()
                        Button("Refresh service") {
                            model.refreshLaunchdStatus()
                        }
                    }
                    Text(model.serviceStatusText.isEmpty ? "No launchd status yet" : model.serviceStatusText)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(6)
                }

                GroupBox("Actions") {
                    HStack {
                        Button("Run Batch") { Task { await model.runBatch() } }
                        Button("Refresh Status") { Task { await model.refreshStatus() } }
                        Button("Validate Config") { Task { await model.validateConfig() } }
                    }
                }

                if !model.configValidationErrors.isEmpty {
                    GroupBox("Validation Errors") {
                        ForEach(model.configValidationErrors) { error in
                            Text("• \(error.field): \(error.message)")
                                .font(.system(.body, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
            }
            .padding(20)
        }
    }

    private func statCard(title: String, value: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.callout)
                .foregroundStyle(.secondary)
            Text("\(value)")
                .font(.title2.weight(.semibold))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
    }
}
