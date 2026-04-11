import XCTest
@testable import ReelTranscodeApp

final class ConfigModelsTests: XCTestCase {
    func testToYAMLMovesSourceAfterValidatedPublishWhenBehaviorIsMoveToOptimized() {
        var config = ConfigDocument()
        config.outputBehavior = .moveToOptimized
        config.outputRoot = "/Volumes/Optimized"

        let yaml = config.toYAML()

        XCTAssertTrue(yaml.contains("mode: keep_original"))
        XCTAssertTrue(yaml.contains("output_root: /Volumes/Optimized"))
        XCTAssertTrue(yaml.contains("delete_original_after_success: true"))
    }

    func testFromExportedConfigMapsMoveToOptimizedBehavior() {
        let config = ConfigDocument.fromExportedConfig(
            [
                "output": .object(
                    [
                        "mode": .string("keep_original"),
                        "delete_original_after_success": .bool(true),
                        "output_root": .string("/Volumes/Optimized"),
                    ]
                )
            ]
        )

        XCTAssertEqual(config.outputBehavior, .moveToOptimized)
        XCTAssertEqual(config.outputRoot, "/Volumes/Optimized")
    }

    func testFromExportedConfigPreservesArchiveMode() {
        let config = ConfigDocument.fromExportedConfig(
            [
                "output": .object(
                    [
                        "mode": .string("archive_original"),
                        "archive_root": .string("/Volumes/Archive"),
                    ]
                )
            ]
        )

        XCTAssertEqual(config.outputBehavior, .archiveOriginals)
        XCTAssertEqual(config.archiveRoot, "/Volumes/Archive")
    }

    func testFromExportedConfigNormalizesRelativeManagedPathsIntoAppSupport() {
        let config = ConfigDocument.fromExportedConfig(
            [
                "paths": .object(
                    [
                        "state_db": .string("state/reeltranscode.db"),
                        "reports_dir": .string("reports"),
                        "csv_summary": .string("reports/summary.csv"),
                        "temp_dir": .string("tmp"),
                    ]
                )
            ]
        )

        XCTAssertEqual(
            config.stateDB,
            AppPaths.appSupportDirectory.appendingPathComponent("state/reeltranscode.db").path
        )
        XCTAssertEqual(
            config.reportsDir,
            AppPaths.appSupportDirectory.appendingPathComponent("reports").path
        )
        XCTAssertEqual(
            config.csvSummary,
            AppPaths.appSupportDirectory.appendingPathComponent("reports/summary.csv").path
        )
        XCTAssertEqual(
            config.tempDir,
            AppPaths.appSupportDirectory.appendingPathComponent("tmp").path
        )
    }
}
