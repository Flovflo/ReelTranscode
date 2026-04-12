import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var model: AppViewModel

    private let metricColumns = [
        GridItem(.adaptive(minimum: 150), spacing: 14)
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header

                HStack(alignment: .top, spacing: 16) {
                    serviceCard
                    toolchainCard
                }

                LazyVGrid(columns: metricColumns, spacing: 14) {
                    MetricCard(title: "Total", value: model.status?.summary.total ?? 0, tint: .primary)
                    MetricCard(title: "Queued", value: model.status?.summary.pending ?? 0, tint: .blue)
                    MetricCard(title: "Running", value: model.status?.summary.running ?? 0, tint: .orange)
                    MetricCard(title: "Converted", value: model.status?.summary.success ?? 0, tint: .green)
                    MetricCard(title: "Errors", value: model.status?.summary.failed ?? 0, tint: .red)
                    MetricCard(title: "Ignored", value: model.status?.summary.skipped ?? 0, tint: .secondary)
                }

                actionsCard

                if let jobs = model.status?.latestJobs, !jobs.isEmpty {
                    recentActivityCard(jobs: jobs)
                } else {
                    ContentUnavailableView(
                        "No Recent Activity",
                        systemImage: "tray",
                        description: Text("Run a library batch or start watch mode to populate the dashboard.")
                    )
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 18)
                }

                if !model.configValidationErrors.isEmpty {
                    validationCard
                }
            }
            .padding(24)
        }
        .background(alignment: .topTrailing) {
            LinearGradient(
                colors: [
                    Color.blue.opacity(0.18),
                    Color.teal.opacity(0.08),
                    .clear
                ],
                startPoint: .topTrailing,
                endPoint: .bottomLeading
            )
            .frame(height: 220)
            .allowsHitTesting(false)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Operations")
                .font(.system(size: 30, weight: .bold, design: .rounded))

            Text("Pilot the native macOS control app, background watch service, and packaged ReelTranscode runtime from one place.")
                .font(.callout)
                .foregroundStyle(.secondary)

            HStack(spacing: 12) {
                Label(
                    model.isServiceRunning ? "Watch service online" : "Watch service stopped",
                    systemImage: model.isServiceRunning ? "dot.radiowaves.left.and.right" : "pause.circle"
                )
                .foregroundStyle(model.isServiceRunning ? .green : .secondary)

                if model.isRefreshing {
                    Label("Refreshing now", systemImage: "arrow.clockwise")
                        .foregroundStyle(.secondary)
                } else if let lastRefreshAt = model.lastRefreshAt {
                    Text("Updated \(lastRefreshAt.formatted(date: .omitted, time: .standard))")
                        .foregroundStyle(.secondary)
                }
            }
            .font(.caption)
        }
    }

    private var serviceCard: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(model.isServiceRunning ? "Background automation is active." : "Background automation is idle.")
                            .font(.headline)
                        Text(model.serviceStatusText.isEmpty ? "Watch service status unavailable." : model.serviceStatusText)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(model.isServiceRunning ? "Stop" : "Start") {
                        if model.isServiceRunning {
                            model.stopWatchService()
                        } else {
                            model.startWatchService()
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isBusy)
                }

                if !model.serviceDiagnosticsText.isEmpty {
                    DisclosureGroup("Runtime details") {
                        Text(model.serviceDiagnosticsText)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

                if let runtime = model.status?.runtime {
                    HStack(spacing: 14) {
                        runtimePill(
                            title: "Queued",
                            value: "\(runtime.queuedPaths)",
                            tint: .blue
                        )
                        runtimePill(
                            title: "Workers",
                            value: "\(runtime.activeWorkers)/\(max(runtime.maxWorkers, 1))",
                            tint: .orange
                        )
                        runtimePill(
                            title: "Paused",
                            value: runtime.watchPaused ? "Yes" : "No",
                            tint: runtime.watchPaused ? .yellow : .green
                        )
                    }
                }
            }
        } label: {
            Label("Service", systemImage: "bolt.horizontal.circle")
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private var toolchainCard: some View {
        let capabilities = model.status?.capabilities ?? .legacyFallback()

        return GroupBox {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(capabilities.dvMP4SafeMux ? "Dolby Vision safe mux path is ready." : "Core pipeline is ready.")
                            .font(.headline)
                        Text(toolchainSummary(for: capabilities))
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Image(systemName: capabilities.dvMP4SafeMux ? "checkmark.seal.fill" : "wrench.and.screwdriver.fill")
                        .font(.title2)
                        .foregroundStyle(capabilities.dvMP4SafeMux ? .green : .orange)
                }

                if !capabilities.missingTools.isEmpty {
                    Text("Missing optional tools: \(capabilities.missingTools.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                DisclosureGroup("Resolved binaries") {
                    VStack(alignment: .leading, spacing: 8) {
                        toolRow(title: "ffmpeg", path: capabilities.resolved.ffmpegBin)
                        toolRow(title: "DoViMuxer", path: capabilities.resolved.doviMuxerBin ?? "Not available")
                        toolRow(title: "MP4Box", path: capabilities.resolved.mp4boxBin ?? "Not available")
                        toolRow(title: "MediaInfo", path: capabilities.resolved.mediainfoBin ?? "Not available")
                        toolRow(title: "mp4muxer", path: capabilities.resolved.mp4muxerBin ?? "Not available")
                    }
                    .font(.system(.caption, design: .monospaced))
                }
            }
        } label: {
            Label("Toolchain", systemImage: "externaldrive.badge.checkmark")
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private var actionsCard: some View {
        GroupBox {
            HStack {
                Button("Run Library Now") {
                    Task { await model.runBatch() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.isBusy)

                Button("Refresh Logs") {
                    model.refreshLogs()
                }
                .disabled(model.isRefreshing)

                Spacer()

                if let runtime = model.status?.runtime, runtime.watchPaused {
                    Button("Resume Intake") {
                        Task { await model.resumeWatch() }
                    }
                } else {
                    Button("Pause Intake") {
                        Task { await model.pauseWatch() }
                    }
                }
                .disabled(!model.isServiceRunning || model.isBusy)
            }
        } label: {
            Label("Actions", systemImage: "slider.horizontal.3")
        }
    }

    private func recentActivityCard(jobs: [JobRow]) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(jobs.prefix(6))) { job in
                    HStack(alignment: .top, spacing: 12) {
                        Circle()
                            .fill(job.statusColor)
                            .frame(width: 10, height: 10)
                            .padding(.top, 5)

                        VStack(alignment: .leading, spacing: 4) {
                            Text(job.fileName)
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
                            .foregroundStyle(job.statusColor)
                    }

                    if job.id != jobs.prefix(6).last?.id {
                        Divider()
                    }
                }
            }
        } label: {
            Label("Recent Activity", systemImage: "clock.arrow.circlepath")
        }
    }

    private var validationCard: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(model.configValidationErrors) { error in
                    Text("• \(error.field): \(error.message)")
                        .font(.system(.body, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        } label: {
            Label("Configuration Checks", systemImage: "checklist.unchecked")
        }
    }

    private func runtimePill(title: String, value: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.headline)
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
        .padding(.horizontal, 12)
        .background(tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }

    private func toolRow(title: String, path: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(title)
                .foregroundStyle(.secondary)
                .frame(width: 76, alignment: .leading)
            Text(path)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func toolchainSummary(for capabilities: CapabilityStatus) -> String {
        if capabilities.dvMP4SafeMux {
            return "ffmpeg, MediaInfo, MP4Box, DoViMuxer and mp4muxer are all available for the DV-safe MP4 path."
        }
        if capabilities.missingTools.isEmpty {
            return "The runtime resolved the core binaries needed for standard remux and transcode work."
        }
        return "Standard remux and transcode paths stay available, but the optional DV-safe path is missing part of its stack."
    }
}

private struct MetricCard: View {
    let title: String
    let value: Int
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.callout)
                .foregroundStyle(.secondary)
            Text("\(value)")
                .font(.system(size: 24, weight: .bold, design: .rounded))
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        .overlay {
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(tint.opacity(0.12))
        }
    }
}
