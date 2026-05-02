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

enum TempWorkspaceStrategy: String, CaseIterable, Identifiable, Equatable, Sendable {
    case sourceFirst = "source_first"
    case configuredFirst = "configured_first"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .sourceFirst:
            return "Near Source"
        case .configuredFirst:
            return "Dedicated Scratch"
        }
    }

    var summary: String {
        switch self {
        case .sourceFirst:
            return "Prefer a .reeltranscode-tmp folder next to the source media, then fall back to the configured scratch root if space is tight."
        case .configuredFirst:
            return "Prefer the configured scratch root first, then fall back to the source volume only when needed."
        }
    }
}

struct ConfigDocument {
    var watchFolders: [String] = []
    var priorityFolders: [String] = []
    var outputBehavior: OutputBehavior = .keepOriginals
    var outputRoot: String = "/Volumes/Media-Optimized"
    var archiveRoot: String = "/Volumes/Media-Archive"
    var stateDB: String = AppPaths.appSupportDirectory.appendingPathComponent("state/reeltranscode.db").path
    var reportsDir: String = AppPaths.appSupportDirectory.appendingPathComponent("reports").path
    var csvSummary: String = AppPaths.appSupportDirectory.appendingPathComponent("reports/summary.csv").path
    var tempDir: String = AppPaths.appSupportDirectory.appendingPathComponent("tmp").path
    var tempWorkspaceStrategy: TempWorkspaceStrategy = .sourceFirst
    var tempDirOverrides: [String: String] = [:]
    var ffmpegBin: String = AppPaths.runtimeDirectory.appendingPathComponent("bin/ffmpeg").path
    var ffprobeBin: String = AppPaths.runtimeDirectory.appendingPathComponent("bin/ffprobe").path
    var doviMuxerBin: String = ""
    var mp4boxBin: String = ""
    var mediainfoBin: String = ""
    var mp4muxerBin: String = ""
    var profile: PerformanceProfile = .lowImpact
    var maxWorkers: Int = 1
    var hardwareEncoder: String = "auto"
    var encoderThreads: Int = 1
    var videotoolboxBitrateMultiplier: Double = 1.0
    var videotoolboxMinBitrateKbps: Int = 2500
    var videotoolboxMaxBitrateKbps: Int = 80000

    mutating func apply(_ profile: PerformanceProfile) {
        self.profile = profile
        self.maxWorkers = max(1, profile.appliedConcurrency().maxWorkers)
        self.encoderThreads = profile == .lowImpact ? 1 : 0
    }

    func toYAML() -> String {
        let concurrency = profile.appliedConcurrency()
        let retry = profile.appliedRetry()
        let normalizedWatchFolders = normalizedWatchFolders()
        let normalizedPriorityFolders = normalizedPriorityFolders()
        var tooling: [String: Any] = [
            "ffmpeg_bin": ffmpegBin,
            "ffprobe_bin": ffprobeBin,
        ]
        if let doviMuxerBin = normalizedOptionalPath(doviMuxerBin) {
            tooling["dovi_muxer_bin"] = doviMuxerBin
        }
        if let mp4boxBin = normalizedOptionalPath(mp4boxBin) {
            tooling["mp4box_bin"] = mp4boxBin
        }
        if let mediainfoBin = normalizedOptionalPath(mediainfoBin) {
            tooling["mediainfo_bin"] = mediainfoBin
        }
        if let mp4muxerBin = normalizedOptionalPath(mp4muxerBin) {
            tooling["mp4muxer_bin"] = mp4muxerBin
        }

        var pathsPayload: [String: Any] = [
            "state_db": stateDB,
            "reports_dir": reportsDir,
            "csv_summary": csvSummary,
            "temp_dir": tempDir,
            "temp_dir_strategy": tempWorkspaceStrategy.rawValue,
        ]
        let tempDirOverrides = normalizedTempDirOverrides(
            for: normalizedWatchFolders + normalizedPriorityFolders
        )
        if !tempDirOverrides.isEmpty {
            pathsPayload["temp_dir_overrides"] = tempDirOverrides
        }

        var outputPayload: [String: Any] = [
            "mode": outputBehavior.yamlMode,
            "output_root": outputRoot,
            "archive_root": archiveRoot,
            "overwrite": false,
            "delete_original_after_success": outputBehavior.deleteOriginalAfterSuccess,
        ]
        let outputRootOverrides = Dictionary(uniqueKeysWithValues: normalizedPriorityFolders.map { ($0, $0) })
        if !outputRootOverrides.isEmpty {
            outputPayload["output_root_overrides"] = outputRootOverrides
        }
        let deleteOriginalRoots = normalizedPriorityFolders + (
            outputBehavior.deleteOriginalAfterSuccess ? normalizedWatchFolders : []
        )
        if !deleteOriginalRoots.isEmpty {
            outputPayload["delete_original_after_success_roots"] = deleteOriginalRoots
        }

        var watchPayload: [String: Any] = [
            "folders": normalizedWatchFolders,
            "recursive": true,
            "use_filesystem_events": false,
            "allowed_extensions": [".mkv", ".mp4", ".mov", ".m4v", ".ts", ".m2ts", ".avi"],
            "priority_extensions": [".mkv", ".mov", ".m4v", ".ts", ".m2ts", ".avi"],
            "stable_wait_seconds": 300,
            "stable_checks": 3,
            "poll_interval_seconds": 10,
            "rescan_interval_seconds": 300,
        ]
        if !normalizedPriorityFolders.isEmpty {
            watchPayload["priority_folders"] = normalizedPriorityFolders
        }

        let vtMinBitrate = max(1, videotoolboxMinBitrateKbps)
        let vtMaxBitrate = max(vtMinBitrate, videotoolboxMaxBitrateKbps)

        let payload: [String: Any] = [
            "dry_run": false,
            "watch": watchPayload,
            "remux": [
                "preferred_container": "mp4",
                "faststart": true,
                "keep_chapters": true,
                "keep_attachments": false,
            ],
            "audio": [
                "preferred_codec_multichannel": "eac3",
                "preferred_codec_stereo": "aac",
                "fallback_codec": "ac3",
                "max_channels": 8,
                "preferred_languages": ["fra", "eng"],
                "keep_original_compatible_tracks": true,
                "ensure_aac_fallback_stereo_when_missing": true,
            ],
            "subtitles": [
                "mode": "convert_or_externalize",
                "convert_text_to_mov_text": true,
                "external_subtitle_format": "srt",
                "preserve_forced_only_when_needed": false,
                "ocr_image_subtitles": true,
                "drop_incompatible_image_subtitles": false,
            ],
            "dolby_vision": [
                "preserve_when_safe": true,
                "safe_profiles": ["8.1"],
                "remux_dv_from_mkv_to_mp4_is_safe": true,
                "fragile_fallback": "preserve_hdr10",
            ],
            "video": [
                "preferred_codec": "hevc",
                "fallback_codec": "h264",
                "force_cfr": false,
                "hardware_encoder": normalizedHardwareEncoder(),
                "encoder_threads": max(0, encoderThreads),
                "videotoolbox_bitrate_multiplier": max(0.1, videotoolboxBitrateMultiplier),
                "videotoolbox_min_bitrate_kbps": vtMinBitrate,
                "videotoolbox_max_bitrate_kbps": vtMaxBitrate,
                "keyframe_interval_seconds": 2,
                "hevc_tag": "hvc1",
                "max_4k_fps": 60,
            ],
            "output": outputPayload,
            "concurrency": [
                "max_workers": max(1, maxWorkers),
                "io_nice_sleep_seconds": concurrency.ioNiceSleep,
            ],
            "retry": [
                "max_attempts": retry.maxAttempts,
                "backoff_initial_seconds": retry.initialBackoff,
                "backoff_max_seconds": retry.maxBackoff,
            ],
            "paths": pathsPayload,
            "tooling": tooling,
            "validation": [
                "verify_duration_tolerance_seconds": 2.0,
                "verify_stream_count_delta_max": 4,
                "run_post_ffprobe": true,
                "require_dv_preservation": true,
            ],
            "logging": [
                "level": "INFO",
                "json_logs": false,
            ],
        ]

        let data: Data
        do {
            data = try JSONSerialization.data(
                withJSONObject: payload,
                options: [.prettyPrinted, .sortedKeys]
            )
        } catch {
            preconditionFailure("ConfigDocument serialization failed unexpectedly: \(error)")
        }
        return String(decoding: data, as: UTF8.self) + "\n"
    }

    private func normalizedWatchFolders() -> [String] {
        watchFolders.isEmpty ? ["/Volumes/Media"] : watchFolders
    }

    private func normalizedPriorityFolders() -> [String] {
        let explicit = priorityFolders
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if !explicit.isEmpty {
            return explicit
        }
        return outputBehavior.usesSeparateOutputRoot ? [outputRoot] : []
    }

    private func normalizedHardwareEncoder() -> String {
        switch hardwareEncoder.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "software", "videotoolbox":
            return hardwareEncoder.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        default:
            return "auto"
        }
    }

    private func normalizedTempDirOverrides(for normalizedProcessFolders: [String]) -> [String: String] {
        let allowedRoots = Set(
            normalizedProcessFolders.map {
                $0.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        )

        return tempDirOverrides.reduce(into: [:]) { result, entry in
            let sourceRoot = entry.key.trimmingCharacters(in: .whitespacesAndNewlines)
            guard allowedRoots.contains(sourceRoot) else { return }
            guard let tempRoot = normalizedOptionalPath(entry.value) else { return }
            result[sourceRoot] = tempRoot
        }
    }

    private func normalizedOptionalPath(_ rawValue: String) -> String? {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    static func fromExportedConfig(_ config: [String: JSONValue]) -> ConfigDocument {
        var doc = ConfigDocument()

        if let watch = config["watch"]?.objectValue,
           let folders = watch["folders"]?.arrayValue {
            doc.watchFolders = folders.compactMap { $0.stringValue }
        }
        if let watch = config["watch"]?.objectValue,
           let priorityFolders = watch["priority_folders"]?.arrayValue {
            doc.priorityFolders = priorityFolders.compactMap { $0.stringValue }
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
            if let rawStrategy = paths["temp_dir_strategy"]?.stringValue,
               let strategy = TempWorkspaceStrategy(rawValue: rawStrategy) {
                doc.tempWorkspaceStrategy = strategy
            }
            if let overrides = paths["temp_dir_overrides"]?.objectValue {
                doc.tempDirOverrides = overrides.reduce(into: [:]) { result, entry in
                    guard let value = entry.value.stringValue else { return }
                    result[entry.key] = value
                }
            }
        }

        if let tooling = config["tooling"]?.objectValue {
            doc.ffmpegBin = tooling["ffmpeg_bin"]?.stringValue ?? doc.ffmpegBin
            doc.ffprobeBin = tooling["ffprobe_bin"]?.stringValue ?? doc.ffprobeBin
            doc.doviMuxerBin = tooling["dovi_muxer_bin"]?.stringValue ?? doc.doviMuxerBin
            doc.mp4boxBin = tooling["mp4box_bin"]?.stringValue ?? doc.mp4boxBin
            doc.mediainfoBin = tooling["mediainfo_bin"]?.stringValue ?? doc.mediainfoBin
            doc.mp4muxerBin = tooling["mp4muxer_bin"]?.stringValue ?? doc.mp4muxerBin
        }

        if let video = config["video"]?.objectValue {
            if let hardwareEncoder = video["hardware_encoder"]?.stringValue {
                doc.hardwareEncoder = hardwareEncoder
            }
            doc.encoderThreads = max(0, video["encoder_threads"]?.intValue ?? doc.encoderThreads)
            doc.videotoolboxBitrateMultiplier = max(
                0.1,
                video["videotoolbox_bitrate_multiplier"]?.doubleValue ?? doc.videotoolboxBitrateMultiplier
            )
            doc.videotoolboxMinBitrateKbps = max(
                1,
                video["videotoolbox_min_bitrate_kbps"]?.intValue ?? doc.videotoolboxMinBitrateKbps
            )
            doc.videotoolboxMaxBitrateKbps = max(
                doc.videotoolboxMinBitrateKbps,
                video["videotoolbox_max_bitrate_kbps"]?.intValue ?? doc.videotoolboxMaxBitrateKbps
            )
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
        tempDirOverrides = tempDirOverrides.reduce(into: [:]) { result, entry in
            let sourceRoot = entry.key.trimmingCharacters(in: .whitespacesAndNewlines)
            let tempRoot = entry.value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !sourceRoot.isEmpty, !tempRoot.isEmpty else { return }
            result[sourceRoot] = Self.normalizedManagedPath(
                tempRoot,
                defaultURL: AppPaths.appSupportDirectory.appendingPathComponent("tmp")
            )
        }
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
