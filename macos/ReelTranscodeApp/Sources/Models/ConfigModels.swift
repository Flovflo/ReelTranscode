import Foundation

enum PerformanceProfile: String, CaseIterable, Identifiable {
    case balanced = "Balanced"
    case maxThroughput = "Max Throughput"
    case lowImpact = "Low Impact"

    var id: String { rawValue }

    func appliedConcurrency() -> (maxWorkers: Int, ioNiceSleep: Double) {
        switch self {
        case .balanced:
            return (2, 0.0)
        case .maxThroughput:
            return (max(4, ProcessInfo.processInfo.activeProcessorCount - 1), 0.0)
        case .lowImpact:
            return (1, 0.25)
        }
    }

    func appliedRetry() -> (maxAttempts: Int, initialBackoff: Double, maxBackoff: Double) {
        switch self {
        case .balanced:
            return (3, 5.0, 90.0)
        case .maxThroughput:
            return (2, 3.0, 30.0)
        case .lowImpact:
            return (4, 8.0, 120.0)
        }
    }
}

enum OutputBehavior: String, CaseIterable, Identifiable, Equatable, Sendable {
    case keepOriginals
    case moveToOptimized
    case replaceInPlace
    case archiveOriginals

    var id: String { rawValue }

    var title: String {
        switch self {
        case .keepOriginals:
            return "Keep Originals"
        case .moveToOptimized:
            return "Move To Optimized"
        case .replaceInPlace:
            return "Replace In Place"
        case .archiveOriginals:
            return "Archive Originals"
        }
    }

    var summary: String {
        switch self {
        case .keepOriginals:
            return "Write the optimized MP4 to a separate library and keep the source untouched."
        case .moveToOptimized:
            return "Write the optimized MP4 to a separate library, then delete the source only after validation succeeds."
        case .replaceInPlace:
            return "Replace the source file in its original folder after a validated publish."
        case .archiveOriginals:
            return "Publish the optimized MP4 and move the untouched source into an archive folder."
        }
    }

    var usesSeparateOutputRoot: Bool {
        self != .replaceInPlace
    }

    var usesArchiveRoot: Bool {
        self == .archiveOriginals
    }

    var yamlMode: String {
        switch self {
        case .keepOriginals, .moveToOptimized:
            return "keep_original"
        case .replaceInPlace:
            return "replace_original"
        case .archiveOriginals:
            return "archive_original"
        }
    }

    var deleteOriginalAfterSuccess: Bool {
        self == .moveToOptimized
    }

    static func fromExported(mode: String, deleteOriginalAfterSuccess: Bool) -> OutputBehavior {
        switch mode {
        case "replace_original":
            return .replaceInPlace
        case "archive_original":
            return .archiveOriginals
        default:
            return deleteOriginalAfterSuccess ? .moveToOptimized : .keepOriginals
        }
    }
}

struct ConfigDocument {
    var watchFolders: [String] = []
    var outputBehavior: OutputBehavior = .keepOriginals
    var outputRoot: String = "/Volumes/Media-Optimized"
    var archiveRoot: String = "/Volumes/Media-Archive"
    var stateDB: String = AppPaths.appSupportDirectory.appendingPathComponent("state/reeltranscode.db").path
    var reportsDir: String = AppPaths.appSupportDirectory.appendingPathComponent("reports").path
    var csvSummary: String = AppPaths.appSupportDirectory.appendingPathComponent("reports/summary.csv").path
    var tempDir: String = AppPaths.appSupportDirectory.appendingPathComponent("tmp").path
    var ffmpegBin: String = AppPaths.runtimeDirectory.appendingPathComponent("bin/ffmpeg").path
    var ffprobeBin: String = AppPaths.runtimeDirectory.appendingPathComponent("bin/ffprobe").path
    var doviMuxerBin: String = ""
    var mp4boxBin: String = ""
    var mediainfoBin: String = ""
    var mp4muxerBin: String = ""
    var profile: PerformanceProfile = .balanced
    var maxWorkers: Int = 2

    mutating func apply(_ profile: PerformanceProfile) {
        self.profile = profile
        self.maxWorkers = max(1, profile.appliedConcurrency().maxWorkers)
    }

    func toYAML() -> String {
        let concurrency = profile.appliedConcurrency()
        let retry = profile.appliedRetry()

        let watchFoldersYAML = watchFolders.map { "    - \($0)" }.joined(separator: "\n")
        let optionalToolLines = [
            doviMuxerBin.isEmpty ? nil : "  dovi_muxer_bin: \(doviMuxerBin)",
            mp4boxBin.isEmpty ? nil : "  mp4box_bin: \(mp4boxBin)",
            mediainfoBin.isEmpty ? nil : "  mediainfo_bin: \(mediainfoBin)",
            mp4muxerBin.isEmpty ? nil : "  mp4muxer_bin: \(mp4muxerBin)"
        ].compactMap { $0 }.joined(separator: "\n")

        return """
        dry_run: false
        
        watch:
          folders:
        \(watchFoldersYAML.isEmpty ? "    - /Volumes/Media" : watchFoldersYAML)
          recursive: true
          allowed_extensions: [.mkv, .mp4, .mov, .m4v, .ts, .m2ts]
          stable_wait_seconds: 300
          stable_checks: 3
          poll_interval_seconds: 10
        
        remux:
          preferred_container: mp4
          faststart: true
          keep_chapters: true
          keep_attachments: false
        
        audio:
          preferred_codec_multichannel: eac3
          preferred_codec_stereo: aac
          fallback_codec: ac3
          max_channels: 8
          preferred_languages: [fra, eng]
          keep_original_compatible_tracks: true
          ensure_aac_fallback_stereo_when_missing: true
        
        subtitles:
          mode: convert_or_externalize
          convert_text_to_mov_text: true
          external_subtitle_format: srt
          preserve_forced_only_when_needed: false
          ocr_image_subtitles: true
          drop_incompatible_image_subtitles: false
        
        dolby_vision:
          preserve_when_safe: true
          safe_profiles: ["8.1"]
          remux_dv_from_mkv_to_mp4_is_safe: false
          fragile_fallback: preserve_hdr10
        
        video:
          preferred_codec: hevc
          fallback_codec: h264
          force_cfr: false
          keyframe_interval_seconds: 2
          hevc_tag: hvc1
          max_4k_fps: 60
        
        output:
          mode: \(outputBehavior.yamlMode)
          output_root: \(outputRoot)
          archive_root: \(archiveRoot)
          overwrite: false
          delete_original_after_success: \(outputBehavior.deleteOriginalAfterSuccess ? "true" : "false")
        
        concurrency:
          max_workers: \(max(1, maxWorkers))
          io_nice_sleep_seconds: \(concurrency.ioNiceSleep)
        
        retry:
          max_attempts: \(retry.maxAttempts)
          backoff_initial_seconds: \(retry.initialBackoff)
          backoff_max_seconds: \(retry.maxBackoff)
        
        paths:
          state_db: \(stateDB)
          reports_dir: \(reportsDir)
          csv_summary: \(csvSummary)
          temp_dir: \(tempDir)
        
        tooling:
          ffmpeg_bin: \(ffmpegBin)
          ffprobe_bin: \(ffprobeBin)\(optionalToolLines.isEmpty ? "" : "\n\(optionalToolLines)")
        
        validation:
          verify_duration_tolerance_seconds: 2.0
          verify_stream_count_delta_max: 4
          run_post_ffprobe: true
          require_dv_preservation: true

        logging:
          level: INFO
          json_logs: false
        """
    }

    static func fromExportedConfig(_ config: [String: JSONValue]) -> ConfigDocument {
        var doc = ConfigDocument()

        if let watch = config["watch"]?.objectValue,
           let folders = watch["folders"]?.arrayValue {
            doc.watchFolders = folders.compactMap { $0.stringValue }
        }

        if let output = config["output"]?.objectValue {
            let mode = output["mode"]?.stringValue ?? "keep_original"
            let deleteOriginalAfterSuccess = output["delete_original_after_success"]?.boolValue ?? false
            doc.outputBehavior = .fromExported(
                mode: mode,
                deleteOriginalAfterSuccess: deleteOriginalAfterSuccess
            )
            doc.outputRoot = output["output_root"]?.stringValue ?? doc.outputRoot
            doc.archiveRoot = output["archive_root"]?.stringValue ?? doc.archiveRoot
        }

        if let paths = config["paths"]?.objectValue {
            doc.stateDB = paths["state_db"]?.stringValue ?? doc.stateDB
            doc.reportsDir = paths["reports_dir"]?.stringValue ?? doc.reportsDir
            doc.csvSummary = paths["csv_summary"]?.stringValue ?? doc.csvSummary
            doc.tempDir = paths["temp_dir"]?.stringValue ?? doc.tempDir
        }

        if let tooling = config["tooling"]?.objectValue {
            doc.ffmpegBin = tooling["ffmpeg_bin"]?.stringValue ?? doc.ffmpegBin
            doc.ffprobeBin = tooling["ffprobe_bin"]?.stringValue ?? doc.ffprobeBin
            doc.doviMuxerBin = tooling["dovi_muxer_bin"]?.stringValue ?? doc.doviMuxerBin
            doc.mp4boxBin = tooling["mp4box_bin"]?.stringValue ?? doc.mp4boxBin
            doc.mediainfoBin = tooling["mediainfo_bin"]?.stringValue ?? doc.mediainfoBin
            doc.mp4muxerBin = tooling["mp4muxer_bin"]?.stringValue ?? doc.mp4muxerBin
        }

        if let concurrency = config["concurrency"]?.objectValue {
            let workers = concurrency["max_workers"]?.intValue ?? 2
            let sleep = concurrency["io_nice_sleep_seconds"]?.doubleValue ?? 0.0
            doc.maxWorkers = max(1, workers)
            if workers <= 1 {
                doc.profile = .lowImpact
            } else if workers >= 4 && sleep == 0.0 {
                doc.profile = .maxThroughput
            } else {
                doc.profile = .balanced
            }
        }

        doc.normalizeManagedPathsForPersistence()
        return doc
    }

    mutating func normalizeManagedPathsForPersistence() {
        stateDB = Self.normalizedManagedPath(
            stateDB,
            defaultURL: AppPaths.appSupportDirectory.appendingPathComponent("state/reeltranscode.db")
        )
        reportsDir = Self.normalizedManagedPath(
            reportsDir,
            defaultURL: AppPaths.appSupportDirectory.appendingPathComponent("reports")
        )
        csvSummary = Self.normalizedManagedPath(
            csvSummary,
            defaultURL: AppPaths.appSupportDirectory.appendingPathComponent("reports/summary.csv")
        )
        tempDir = Self.normalizedManagedPath(
            tempDir,
            defaultURL: AppPaths.appSupportDirectory.appendingPathComponent("tmp")
        )
    }

    private static func normalizedManagedPath(_ rawValue: String, defaultURL: URL) -> String {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return defaultURL.standardizedFileURL.path
        }

        let expanded = NSString(string: trimmed).expandingTildeInPath
        if expanded.hasPrefix("/") {
            return URL(fileURLWithPath: expanded).standardizedFileURL.path
        }

        return AppPaths.appSupportDirectory
            .appendingPathComponent(expanded)
            .standardizedFileURL
            .path
    }
}
