import Foundation

struct StatusResponse: Decodable, Sendable {
    let apiVersion: Int
    let summary: JobSummary
    let latestJobs: [JobRow]
    let paths: StatusPaths

    enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case summary
        case latestJobs = "latest_jobs"
        case paths
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
