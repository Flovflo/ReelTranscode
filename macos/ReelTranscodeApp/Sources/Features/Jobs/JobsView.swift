import SwiftUI

struct JobsView: View {
    @EnvironmentObject private var model: AppViewModel
    @State private var sortOrder = [KeyPathComparator(\JobRow.startedAt, order: .reverse)]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Jobs")
                .font(.largeTitle.weight(.semibold))

            if let rows = model.status?.latestJobs, !rows.isEmpty {
                Table(rows, sortOrder: $sortOrder) {
                    TableColumn("Status", value: \JobRow.status)
                    TableColumn("Case", value: \JobRow.caseLabel)
                    TableColumn("Strategy", value: \JobRow.strategy)
                    TableColumn("Source") { row in
                        Text(row.sourcePath)
                            .lineLimit(1)
                    }
                    TableColumn("Error") { row in
                        Text(row.errorMessage ?? "")
                            .lineLimit(1)
                            .foregroundStyle(.secondary)
                    }
                }
            } else {
                ContentUnavailableView("No Jobs Yet", systemImage: "tray", description: Text("Run a batch or start watch mode."))
            }
        }
        .padding(20)
    }
}
