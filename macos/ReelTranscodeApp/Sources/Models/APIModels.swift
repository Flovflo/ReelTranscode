import Foundation

struct StatusResponse: Decodable, Sendable {
    let apiVersion: Int
    let summary: JobSummary
    let latestJobs: [JobRow]
    let paths: StatusPaths
    let runtime: RuntimeStatus
    let capabilities: CapabilityStatus

    enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case summary
        case latestJobs = "latest_jobs"
        case paths
        case runtime
        case capabilities
    }

    init(
        apiVersion: Int,
        summary: JobSummary,
        latestJobs: [JobRow],
        paths: StatusPaths,
        runtime: RuntimeStatus,
        capabilities: CapabilityStatus
    ) {
        self.apiVersion = apiVersion
        self.summary = summary
        self.latestJobs = latestJobs
        self.paths = paths
        self.runtime = runtime
        self.capabilities = capabilities
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let apiVersion = try container.decode(Int.self, forKey: .apiVersion)
        let summary = try container.decode(JobSummary.self, forKey: .summary)
        let latestJobs = try container.decodeIfPresent([JobRow].self, forKey: .latestJobs) ?? []
        let paths = try container.decode(StatusPaths.self, forKey: .paths)
        let runtime = try container.decodeIfPresent(RuntimeStatus.self, forKey: .runtime)
            ?? RuntimeStatus.legacyFallback(summary: summary)
        let capabilities = try container.decodeIfPresent(CapabilityStatus.self, forKey: .capabilities)
            ?? .legacyFallback()

        self.init(
            apiVersion: apiVersion,
            summary: summary,
            latestJobs: latestJobs,
            paths: paths,
            runtime: runtime,
            capabilities: capabilities
        )
    }

    func withRuntime(_ runtime: RuntimeStatus) -> StatusResponse {
        StatusResponse(
            apiVersion: apiVersion,
            summary: summary,
            latestJobs: latestJobs,
            paths: paths,
            runtime: runtime,
            capabilities: capabilities
        )
    }
}

struct JobSummary: Decodable, Sendable {
    let pending: Int
    let running: Int
    let success: Int
    let failed: Int
    let skipped: Int
    let total: Int
}

struct JobRow: Decodable, Identifiable, Sendable {
    let jobID: String
    let status: String
    let caseLabel: String
    let strategy: String
    let sourcePath: String
    let targetPath: String?
    let startedAt: String
    let finishedAt: String?
    let errorClass: String?
    let errorMessage: String?

    var id: String { jobID }

    enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case status
        case caseLabel = "case_label"
        case strategy
        case sourcePath = "source_path"
        case targetPath = "target_path"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case errorClass = "error_class"
        case errorMessage = "error_message"
    }
}

struct StatusPaths: Decodable, Sendable {
    let stateDB: String
    let reportsDir: String
    let csvSummary: String

    enum CodingKeys: String, CodingKey {
        case stateDB = "state_db"
        case reportsDir = "reports_dir"
        case csvSummary = "csv_summary"
    }
}

struct RuntimeStatus: Decodable, Sendable {
    let watchRunning: Bool
    let watchPaused: Bool
    let queuedPaths: Int
    let activeWorkers: Int
    let maxWorkers: Int
    let updatedAt: String

    enum CodingKeys: String, CodingKey {
        case watchRunning = "watch_running"
        case watchPaused = "watch_paused"
        case queuedPaths = "queued_paths"
        case activeWorkers = "active_workers"
        case maxWorkers = "max_workers"
        case updatedAt = "updated_at"
    }

    static func legacyFallback(summary: JobSummary) -> RuntimeStatus {
        RuntimeStatus(
            watchRunning: summary.pending > 0 || summary.running > 0,
            watchPaused: false,
            queuedPaths: summary.pending,
            activeWorkers: summary.running,
            maxWorkers: max(summary.running, 1),
            updatedAt: ""
        )
    }

    func with(
        watchRunning: Bool? = nil,
        watchPaused: Bool? = nil,
        queuedPaths: Int? = nil,
        activeWorkers: Int? = nil,
        maxWorkers: Int? = nil,
        updatedAt: String? = nil
    ) -> RuntimeStatus {
        RuntimeStatus(
            watchRunning: watchRunning ?? self.watchRunning,
            watchPaused: watchPaused ?? self.watchPaused,
            queuedPaths: queuedPaths ?? self.queuedPaths,
            activeWorkers: activeWorkers ?? self.activeWorkers,
            maxWorkers: maxWorkers ?? self.maxWorkers,
            updatedAt: updatedAt ?? self.updatedAt
        )
    }
}

struct CapabilityStatus: Decodable, Sendable {
    let dvMP4SafeMux: Bool
    let missingTools: [String]
    let resolved: ResolvedToolchain

    enum CodingKeys: String, CodingKey {
        case dvMP4SafeMux = "dv_mp4_safe_mux"
        case missingTools = "missing_tools"
        case resolved
    }

    static func legacyFallback() -> CapabilityStatus {
        CapabilityStatus(
            dvMP4SafeMux: false,
            missingTools: [],
            resolved: ResolvedToolchain(
                ffmpegBin: "",
                doviMuxerBin: nil,
                mp4boxBin: nil,
                mediainfoBin: nil,
                mp4muxerBin: nil
            )
        )
    }
}

struct ResolvedToolchain: Decodable, Sendable {
    let ffmpegBin: String
    let doviMuxerBin: String?
    let mp4boxBin: String?
    let mediainfoBin: String?
    let mp4muxerBin: String?

    enum CodingKeys: String, CodingKey {
        case ffmpegBin = "ffmpeg_bin"
        case doviMuxerBin = "dovi_muxer_bin"
        case mp4boxBin = "mp4box_bin"
        case mediainfoBin = "mediainfo_bin"
        case mp4muxerBin = "mp4muxer_bin"
    }
}

struct ConfigExportResponse: Decodable, Sendable {
    let apiVersion: Int
    let config: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case config
    }
}

struct ConfigValidateResponse: Decodable, Sendable {
    let apiVersion: Int
    let valid: Bool
    let errors: [ConfigValidationError]

    enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case valid
        case errors
    }
}

struct ConfigValidationError: Decodable, Identifiable, Sendable {
    let field: String
    let message: String

    var id: String { "\(field)|\(message)" }
}

enum JSONValue: Decodable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
        }
    }

    var stringValue: String? {
        guard case let .string(value) = self else { return nil }
        return value
    }

    var intValue: Int? {
        guard case let .number(value) = self else { return nil }
        return Int(value)
    }

    var doubleValue: Double? {
        guard case let .number(value) = self else { return nil }
        return value
    }

    var boolValue: Bool? {
        guard case let .bool(value) = self else { return nil }
        return value
    }

    var objectValue: [String: JSONValue]? {
        guard case let .object(value) = self else { return nil }
        return value
    }

    var arrayValue: [JSONValue]? {
        guard case let .array(value) = self else { return nil }
        return value
    }
}
