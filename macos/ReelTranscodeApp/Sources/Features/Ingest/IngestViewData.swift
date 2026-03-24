import Foundation

struct IngestViewData {
    let runtime: RuntimeStatus
    let summary: JobSummary
    let running: [JobRow]
    let failed: [JobRow]
    let converted: [JobRow]
    let ignored: [JobRow]

    static let empty = IngestViewData(
        runtime: RuntimeStatus(watchRunning: false, watchPaused: false, queuedPaths: 0, activeWorkers: 0, maxWorkers: 0, updatedAt: ""),
        summary: JobSummary(pending: 0, running: 0, success: 0, failed: 0, skipped: 0, total: 0),
        running: [],
        failed: [],
        converted: [],
        ignored: []
    )
}

extension StatusResponse {
    var ingestViewData: IngestViewData {
        IngestViewData(
            runtime: runtime,
            summary: summary,
            running: latestJobs.inProgressRows(),
            failed: latestJobs.withStatus("failed"),
            converted: latestJobs.withStatus("success"),
            ignored: latestJobs.withStatus("skipped")
        )
    }
}
