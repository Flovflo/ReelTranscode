import SwiftUI

struct IngestJobSections: View {
    let data: IngestViewData

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            section(title: "Now Processing", rows: Array(data.running.prefix(6)))
            section(title: "Latest Errors", rows: Array(data.failed.prefix(4)))
            section(title: "Latest Converted", rows: Array(data.converted.prefix(6)))
        }
    }

    @ViewBuilder
    private func section(title: String, rows: [JobRow]) -> some View {
        if !rows.isEmpty {
            GroupBox(title) {
                VStack(spacing: 10) {
                    ForEach(rows) { row in
                        IngestJobCard(row: row)
                    }
                }
            }
        }
    }
}
