import SwiftUI

struct IngestMetricsSection: View {
    let data: IngestViewData

    private let columns = [GridItem(.adaptive(minimum: 150), spacing: 12)]

    var body: some View {
        LazyVGrid(columns: columns, spacing: 12) {
            metricCard(title: "Queued", value: data.runtime.queuedPaths, tint: .blue)
            metricCard(title: "Running", value: max(data.summary.running, data.runtime.activeWorkers), tint: .orange)
            metricCard(title: "Converted", value: data.summary.success, tint: .green)
            metricCard(title: "Errors", value: data.summary.failed, tint: .red)
            metricCard(title: "Ignored", value: data.summary.skipped, tint: .secondary)
            metricCard(title: "Seen", value: data.summary.total, tint: .primary)
        }
    }

    private func metricCard(title: String, value: Int, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("\(value)")
                .font(.title2.weight(.semibold))
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }
}
