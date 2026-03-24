import SwiftUI

extension JobRow {
    var fileName: String {
        URL(fileURLWithPath: sourcePath).lastPathComponent
    }

    var sourceDirectory: String {
        URL(fileURLWithPath: sourcePath).deletingLastPathComponent().path
    }

    var statusLabel: String {
        switch status.lowercased() {
        case "success":
            return "Converted"
        case "failed":
            return "Error"
        case "running":
            return "In Progress"
        case "pending":
            return "Queued"
        case "skipped":
            return "Ignored"
        default:
            return status.capitalized
        }
    }

    var statusColor: Color {
        switch status.lowercased() {
        case "success":
            return .green
        case "failed":
            return .red
        case "running":
            return .orange
        case "pending":
            return .blue
        case "skipped":
            return .secondary
        default:
            return .secondary
        }
    }
}

extension Array where Element == JobRow {
    func withStatus(_ status: String) -> [JobRow] {
        filter { $0.status.lowercased() == status.lowercased() }
    }

    func inProgressRows() -> [JobRow] {
        filter {
            let status = $0.status.lowercased()
            return status == "running" || status == "pending"
        }
    }
}
