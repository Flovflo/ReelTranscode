import XCTest
@testable import ReelTranscodeApp

final class ConfigModelsTests: XCTestCase {
    func testToYAMLMovesSourceAfterValidatedPublishWhenBehaviorIsMoveToOptimized() {
        var config = ConfigDocument()
        config.outputBehavior = .moveToOptimized
        config.outputRoot = "/Volumes/Optimized"

        let yaml = config.toYAML()

        XCTAssertTrue(yaml.contains("\"mode\" : \"keep_original\""))
        XCTAssertTrue(yaml.contains("\"output_root\" : \"/Volumes/Optimized\""))
        XCTAssertTrue(yaml.contains("\"delete_original_after_success\" : true"))
    }

    func testToYAMLSerializesReservedCharactersInPathsSafely() throws {
        var config = ConfigDocument()
        config.watchFolders = ["/Volumes/Movies & Series"]
        config.outputRoot = "/Volumes/Media:Optimized/Movies \"4K\""
        config.tempWorkspaceStrategy = .configuredFirst
        config.tempDirOverrides = [
            "/Volumes/Movies & Series": "/Volumes/Speedy_Boy/ReelTranscode/series-tmp",
            "/Volumes/Not-Watched": "/tmp/ignored",
        ]

        let yaml = config.toYAML()
        let object = try JSONSerialization.jsonObject(with: Data(yaml.utf8)) as? [String: Any]
        let watch = object?["watch"] as? [String: Any]
        let output = object?["output"] as? [String: Any]
        let dovi = object?["dolby_vision"] as? [String: Any]
        let video = object?["video"] as? [String: Any]
        let paths = object?["paths"] as? [String: Any]
        let overrides = paths?["temp_dir_overrides"] as? [String: String]

        XCTAssertEqual((watch?["folders"] as? [String])?.first, "/Volumes/Movies & Series")
        XCTAssertEqual((watch?["priority_folders"] as? [String])?.first, "/Volumes/Media:Optimized/Movies \"4K\"")
        XCTAssertTrue(((watch?["allowed_extensions"] as? [String]) ?? []).contains(".avi"))
        XCTAssertTrue(((watch?["priority_extensions"] as? [String]) ?? []).contains(".mkv"))
        XCTAssertEqual(watch?["use_filesystem_events"] as? Bool, false)
        XCTAssertEqual(watch?["rescan_interval_seconds"] as? Int, 300)
        XCTAssertEqual(output?["output_root"] as? String, "/Volumes/Media:Optimized/Movies \"4K\"")
        XCTAssertEqual((output?["output_root_overrides"] as? [String: String])?["/Volumes/Media:Optimized/Movies \"4K\""], "/Volumes/Media:Optimized/Movies \"4K\"")
        XCTAssertTrue(((output?["delete_original_after_success_roots"] as? [String]) ?? []).contains("/Volumes/Media:Optimized/Movies \"4K\""))
        XCTAssertEqual(dovi?["remux_dv_from_mkv_to_mp4_is_safe"] as? Bool, true)
        XCTAssertEqual(video?["hardware_encoder"] as? String, "auto")
        XCTAssertEqual(video?["encoder_threads"] as? Int, 1)
        XCTAssertEqual(video?["videotoolbox_bitrate_multiplier"] as? Double, 1.0)
        XCTAssertEqual(video?["videotoolbox_min_bitrate_kbps"] as? Int, 2500)
        XCTAssertEqual(video?["videotoolbox_max_bitrate_kbps"] as? Int, 80000)
        XCTAssertEqual(paths?["temp_dir_strategy"] as? String, "configured_first")
        XCTAssertEqual(overrides?["/Volumes/Movies & Series"], "/Volumes/Speedy_Boy/ReelTranscode/series-tmp")
        XCTAssertNil(overrides?["/Volumes/Not-Watched"])
    }

    func testFromExportedConfigPreservesPriorityFolders() {
        let config = ConfigDocument.fromExportedConfig(
            [
                "watch": .object(
                    [
                        "priority_folders": .array([.string("/Volumes/Series-opti")]),
                    ]
                )
            ]
        )

        XCTAssertEqual(config.priorityFolders, ["/Volumes/Series-opti"])
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

    func testFromExportedConfigRestoresTempWorkspaceStrategy() {
        let config = ConfigDocument.fromExportedConfig(
            [
                "paths": .object(
                    [
                        "temp_dir_strategy": .string("configured_first"),
                    ]
                )
            ]
        )

        XCTAssertEqual(config.tempWorkspaceStrategy, .configuredFirst)
    }

    func testFromExportedConfigRestoresTempWorkspaceOverrides() {
        let config = ConfigDocument.fromExportedConfig(
            [
                "paths": .object(
                    [
                        "temp_dir_overrides": .object(
                            [
                                "/Volumes/Series": .string("/Volumes/Speedy_Boy/ReelTranscode/series-tmp"),
                            ]
                        ),
                    ]
                )
            ]
        )

        XCTAssertEqual(
            config.tempDirOverrides["/Volumes/Series"],
            "/Volumes/Speedy_Boy/ReelTranscode/series-tmp"
        )
    }

    func testFromExportedConfigRestoresVideoToolboxSettings() {
        let config = ConfigDocument.fromExportedConfig(
            [
                "video": .object(
                    [
                        "hardware_encoder": .string("videotoolbox"),
                        "encoder_threads": .number(2),
                        "videotoolbox_bitrate_multiplier": .number(1.25),
                        "videotoolbox_min_bitrate_kbps": .number(3000),
                        "videotoolbox_max_bitrate_kbps": .number(60000),
                    ]
                )
            ]
        )

        XCTAssertEqual(config.hardwareEncoder, "videotoolbox")
        XCTAssertEqual(config.encoderThreads, 2)
        XCTAssertEqual(config.videotoolboxBitrateMultiplier, 1.25)
        XCTAssertEqual(config.videotoolboxMinBitrateKbps, 3000)
        XCTAssertEqual(config.videotoolboxMaxBitrateKbps, 60000)
    }
}
