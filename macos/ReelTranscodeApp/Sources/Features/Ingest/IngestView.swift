import SwiftUI

struct IngestView: View {
    @EnvironmentObject private var model: AppViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Library Ingest")
                    .font(.largeTitle.weight(.semibold))

                Text("Pilot large library queues, pause intake without killing the service, and verify what is actively moving through the pipeline.")
                    .font(.callout)
                    .foregroundStyle(.secondary)

                IngestControlsSection(data: ingestData)
                IngestMetricsSection(data: ingestData)

                if ingestData.summary.total == 0 && ingestData.runtime.queuedPaths == 0 {
                    ContentUnavailableView(
                        "No Active Library Work",
                        systemImage: "shippingbox",
                        description: Text("Start watch mode or scan existing folders to build a queue.")
                    )
                } else {
                    IngestJobSections(data: ingestData)
                }
            }
            .padding(20)
        }
    }

    private var ingestData: IngestViewData {
        model.status?.ingestViewData ?? .empty
    }
}
