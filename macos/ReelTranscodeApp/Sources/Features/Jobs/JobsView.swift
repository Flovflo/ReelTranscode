import SwiftUI

struct JobsView: View {
    @EnvironmentObject private var model: AppViewModel
    @State private var searchText = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Jobs")
                    .font(.largeTitle.weight(.semibold))

                Text("Suivi rapide des conversions, erreurs et fichiers ignores pendant les gros batches.")
                    .font(.callout)
                    .foregroundStyle(.secondary)

                HStack(spacing: 12) {
                    statPill(title: "Queued", value: summary.pending, tint: .blue)
                    statPill(title: "Running", value: summary.running, tint: .orange)
                    statPill(title: "Converted", value: summary.success, tint: .green)
                    statPill(title: "Errors", value: summary.failed, tint: .red)
                    statPill(title: "Ignored", value: summary.skipped, tint: .secondary)
                }

                TextField("Search file, folder or error", text: $searchText)
                    .textFieldStyle(.roundedBorder)

                if filteredRows.isEmpty {
                    ContentUnavailableView(
                        "No Jobs Yet",
                        systemImage: "tray",
                        description: Text("Run a batch or start watch mode.")
                    )
                } else {
                    jobSection(title: "In Progress", rows: runningRows)
                    jobSection(title: "Errors", rows: failedRows)
                    jobSection(title: "Recently Converted", rows: successRows)
                    jobSection(title: "Ignored / Whitelisted", rows: skippedRows)
                }
            }
            .padding(20)
        }
    }

    private var summary: JobSummary {
        model.status?.summary ?? JobSummary(pending: 0, running: 0, success: 0, failed: 0, skipped: 0, total: 0)
    }

    private var filteredRows: [JobRow] {
        guard let rows = model.status?.latestJobs else { return [] }
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !query.isEmpty else { return rows }
        return rows.filter { row in
            row.sourcePath.lowercased().contains(query)
                || (row.targetPath?.lowercased().contains(query) ?? false)
                || row.status.lowercased().contains(query)
                || row.caseLabel.lowercased().contains(query)
                || (row.errorMessage?.lowercased().contains(query) ?? false)
        }
    }

    private var runningRows: [JobRow] {
        filteredRows.filter { $0.status.lowercased() == "running" || $0.status.lowercased() == "pending" }
    }

    private var failedRows: [JobRow] {
        filteredRows.filter { $0.status.lowercased() == "failed" }
    }

    private var successRows: [JobRow] {
        filteredRows.filter { $0.status.lowercased() == "success" }
    }

    private var skippedRows: [JobRow] {
        filteredRows.filter { $0.status.lowercased() == "skipped" }
    }

    @ViewBuilder
    private func jobSection(title: String, rows: [JobRow]) -> some View {
        if !rows.isEmpty {
            GroupBox(title) {
                VStack(spacing: 10) {
                    ForEach(rows) { row in
                        JobRowCard(row: row)
                        if row.id != rows.last?.id {
                            Divider()
                        }
                    }
                }
            }
        }
    }

    private func statPill(title: String, value: Int, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("\(value)")
                .font(.title3.weight(.semibold))
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
        .padding(.horizontal, 12)
        .background(tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct JobRowCard: View {
    let row: JobRow

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(row.statusColor)
                .frame(width: 10, height: 10)
                .padding(.top, 6)

            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(row.fileName)
                        .font(.headline)
                        .lineLimit(1)
                    Text(row.statusLabel)
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(row.statusColor.opacity(0.12), in: Capsule())
                        .foregroundStyle(row.statusColor)
                }

                Text(row.sourceDirectory)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                HStack(spacing: 10) {
                    Label(row.caseLabel, systemImage: "tag")
                    Text(row.strategy)
                }
                .font(.caption)
                .foregroundStyle(.secondary)

                if let targetPath = row.targetPath {
                    Label(targetPath, systemImage: "arrow.right.circle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                if let errorMessage = row.errorMessage, !errorMessage.isEmpty {
                    Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.red)
                        .lineLimit(3)
                }

                HStack(spacing: 12) {
                    Text("Started \(row.startedAt)")
                    if let finishedAt = row.finishedAt {
                        Text("Finished \(finishedAt)")
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }

            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private extension JobRow {
    var fileName: String {
        URL(fileURLWithPath: sourcePath).lastPathComponent
    }

    var sourceDirectory: String {
        URL(fileURLWithPath: sourcePath).deletingLastPathComponent().path
    }

    var statusLabel: String {
        switch status.lowercased() {
        case "success":
            return "Converted"
        case "failed":
            return "Error"
        case "running":
            return "In Progress"
        case "pending":
            return "Queued"
        case "skipped":
            return "Ignored"
        default:
            return status.capitalized
        }
    }

    var statusColor: Color {
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
