import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var model: AppViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Dashboard")
                    .font(.largeTitle.weight(.semibold))

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 12)], spacing: 12) {
                    statCard(title: "Total", value: model.status?.summary.total ?? 0)
                    statCard(title: "Queued", value: model.status?.summary.pending ?? 0)
                    statCard(title: "Running", value: model.status?.summary.running ?? 0)
                    statCard(title: "Converted", value: model.status?.summary.success ?? 0)
                    statCard(title: "Errors", value: model.status?.summary.failed ?? 0)
                    statCard(title: "Ignored", value: model.status?.summary.skipped ?? 0)
                }

                GroupBox("Service") {
                    HStack {
                        Label(model.isServiceRunning ? "Running" : "Stopped", systemImage: model.isServiceRunning ? "checkmark.circle.fill" : "xmark.circle")
                            .foregroundStyle(model.isServiceRunning ? .green : .secondary)
                        Spacer()
                        Button(model.isServiceRunning ? "Stop" : "Start") {
                            if model.isServiceRunning {
                                model.stopWatchService()
                            } else {
                                model.startWatchService()
                            }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    Text(model.serviceStatusText.isEmpty ? "No launchd status yet" : model.serviceStatusText)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(6)
                }

                GroupBox("Actions") {
                    HStack {
                        Button("Run Library Now") { Task { await model.runBatch() } }
                    }
                }

                if let jobs = model.status?.latestJobs, !jobs.isEmpty {
                    GroupBox("Recent Activity") {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(Array(jobs.prefix(6))) { job in
                                HStack(alignment: .top, spacing: 12) {
                                    statusDot(for: job.status)
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(URL(fileURLWithPath: job.sourcePath).lastPathComponent)
                                            .font(.headline)
                                            .lineLimit(1)
                                        Text(job.targetPath ?? job.sourcePath)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                        if let error = job.errorMessage, !error.isEmpty {
                                            Text(error)
                                                .font(.caption)
                                                .foregroundStyle(.red)
                                                .lineLimit(2)
                                        }
                                    }
                                    Spacer()
                                    Text(job.statusLabel)
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(statusColor(for: job.status))
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                if job.id != jobs.prefix(6).last?.id {
                                    Divider()
                                }
                            }
                        }
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

    private func statusDot(for status: String) -> some View {
        Circle()
            .fill(statusColor(for: status))
            .frame(width: 10, height: 10)
            .padding(.top, 5)
    }

    private func statusColor(for status: String) -> Color {
        switch status.lowercased() {
        case "success":
            return .green
        case "failed":
            return .red
        case "running":
            return .orange
        case "pending":
            return .blue
        case "skipped":
            return .secondary
        default:
            return .secondary
        }
    }
}
