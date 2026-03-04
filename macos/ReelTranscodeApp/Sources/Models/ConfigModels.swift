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

struct ConfigDocument {
    var watchFolders: [String] = []
    var outputRoot: String = "/Volumes/Media-Optimized"
    var archiveRoot: String = "/Volumes/Media-Archive"
    var stateDB: String = AppPaths.appSupportDirectory.appendingPathComponent("state/reeltranscode.db").path
    var reportsDir: String = AppPaths.appSupportDirectory.appendingPathComponent("reports").path
    var csvSummary: String = AppPaths.appSupportDirectory.appendingPathComponent("reports/summary.csv").path
    var tempDir: String = AppPaths.appSupportDirectory.appendingPathComponent("tmp").path
    var ffmpegBin: String = AppPaths.runtimeDirectory.appendingPathComponent("bin/ffmpeg").path
    var ffprobeBin: String = AppPaths.runtimeDirectory.appendingPathComponent("bin/ffprobe").path
    var profile: PerformanceProfile = .balanced

    mutating func apply(_ profile: PerformanceProfile) {
        self.profile = profile
    }

    func toYAML() -> String {
        let concurrency = profile.appliedConcurrency()
        let retry = profile.appliedRetry()

        let watchFoldersYAML = watchFolders.map { "    - \($0)" }.joined(separator: "\n")

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
        
        subtitles:
          mode: convert_or_externalize
          convert_text_to_mov_text: true
          external_subtitle_format: srt
          preserve_forced_only_when_needed: false
          ocr_image_subtitles: false
        
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
          hevc_tag: hev1
          max_4k_fps: 60
        
        output:
          mode: keep_original
          output_root: \(outputRoot)
          archive_root: \(archiveRoot)
          overwrite: false
          delete_original_after_success: false
        
        concurrency:
          max_workers: \(concurrency.maxWorkers)
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
          ffprobe_bin: \(ffprobeBin)
        
        validation:
          verify_duration_tolerance_seconds: 2.0
          verify_stream_count_delta_max: 4
          run_post_ffprobe: true
        
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
        }

        if let concurrency = config["concurrency"]?.objectValue {
            let workers = concurrency["max_workers"]?.intValue ?? 2
            let sleep = concurrency["io_nice_sleep_seconds"]?.doubleValue ?? 0.0
            if workers <= 1 {
                doc.profile = .lowImpact
            } else if workers >= 4 && sleep == 0.0 {
                doc.profile = .maxThroughput
            } else {
                doc.profile = .balanced
            }
        }

        return doc
    }
}
