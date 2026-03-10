import XCTest
@testable import ReelTranscodeApp

final class APIModelsTests: XCTestCase {
    func testStatusResponseDecoding() throws {
        let json = """
        {
          "api_version": 1,
          "summary": {
            "pending": 0,
            "running": 1,
            "success": 10,
            "failed": 1,
            "skipped": 2,
            "total": 14
          },
          "latest_jobs": [
            {
              "job_id": "abc",
              "status": "running",
              "case_label": "A_ALREADY_COMPATIBLE",
              "strategy": "no_op",
              "source_path": "/tmp/movie.mkv",
              "target_path": null,
              "started_at": "2026-03-03T20:00:00Z",
              "finished_at": null,
              "error_class": null,
              "error_message": null
            }
          ],
          "paths": {
            "state_db": "/tmp/state.db",
            "reports_dir": "/tmp/reports",
            "csv_summary": "/tmp/reports/summary.csv"
          }
        }
        """

        let response = try JSONDecoder().decode(StatusResponse.self, from: Data(json.utf8))
        XCTAssertEqual(response.apiVersion, 1)
        XCTAssertEqual(response.summary.running, 1)
        XCTAssertEqual(response.latestJobs.first?.jobID, "abc")
    }
}
