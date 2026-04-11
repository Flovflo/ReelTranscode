import Foundation
import Darwin

struct WatchProcessInfo: Equatable, Sendable {
    let pid: Int32
    let executablePath: String
    let configPath: String
    let command: String
}

typealias WatchProcessListProvider = () throws -> String
typealias WatchProcessSignalSender = (_ pid: Int32, _ signal: Int32) -> Int32

final class WatchProcessService: @unchecked Sendable {
    private let processListProvider: WatchProcessListProvider
    private let signalSender: WatchProcessSignalSender
    private let sleeper: (TimeInterval) -> Void

    init(
        processListProvider: WatchProcessListProvider? = nil,
        signalSender: WatchProcessSignalSender? = nil,
        sleeper: ((TimeInterval) -> Void)? = nil
    ) {
        self.processListProvider = processListProvider ?? Self.defaultProcessList
        self.signalSender = signalSender ?? Darwin.kill
        self.sleeper = sleeper ?? { Thread.sleep(forTimeInterval: $0) }
    }

    func findWatchProcesses(configPath: String) throws -> [WatchProcessInfo] {
        try processListProvider()
            .split(whereSeparator: \.isNewline)
            .compactMap { Self.parseProcessLine(String($0), configPath: configPath) }
    }

    @discardableResult
    func stopWatchProcesses(configPath: String, excluding excludedPIDs: Set<Int32> = []) throws -> [WatchProcessInfo] {
        let processes = try findWatchProcesses(configPath: configPath)
            .filter { !excludedPIDs.contains($0.pid) }
        try terminate(processes)
        return processes
    }

    func terminate(_ processes: [WatchProcessInfo]) throws {
        guard !processes.isEmpty else { return }

        for process in processes where isProcessAlive(process.pid) {
            _ = signalSender(process.pid, SIGTERM)
        }

        if waitForExit(of: processes, timeout: 2) {
            return
        }

        for process in processes where isProcessAlive(process.pid) {
            _ = signalSender(process.pid, SIGKILL)
        }
        _ = waitForExit(of: processes, timeout: 1)
    }

    private func waitForExit(of processes: [WatchProcessInfo], timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if processes.allSatisfy({ !isProcessAlive($0.pid) }) {
                return true
            }
            sleeper(0.05)
        }
        return processes.allSatisfy { !isProcessAlive($0.pid) }
    }

    private func isProcessAlive(_ pid: Int32) -> Bool {
        errno = 0
        let result = signalSender(pid, 0)
        if result == 0 {
            return true
        }
        return errno == EPERM
    }

    private static func parseProcessLine(_ line: String, configPath: String) -> WatchProcessInfo? {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        let pidToken = trimmed.prefix { !$0.isWhitespace }
        guard let pid = Int32(pidToken) else { return nil }

        let commandStart = trimmed.index(trimmed.startIndex, offsetBy: pidToken.count)
        let command = trimmed[commandStart...].trimmingCharacters(in: .whitespaces)
        let marker = " --config \(configPath) watch"
        guard let markerRange = command.range(of: marker),
              command.contains("ReelTranscodeCore") else {
            return nil
        }

        let executablePath = String(command[..<markerRange.lowerBound]).trimmingCharacters(in: .whitespaces)
        guard executablePath.hasSuffix("/ReelTranscodeCore") else { return nil }

        return WatchProcessInfo(
            pid: pid,
            executablePath: executablePath,
            configPath: configPath,
            command: command
        )
    }

    private static func defaultProcessList() throws -> String {
        let process = Process()
        let stdout = Pipe()
        let stderr = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/ps")
        process.arguments = ["-axo", "pid=,command="]
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        process.waitUntilExit()

        let stdoutText = String(decoding: stdout.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        let stderrText = String(decoding: stderr.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)

        guard process.terminationStatus == 0 else {
            throw NSError(
                domain: "ReelTranscodeApp",
                code: Int(process.terminationStatus),
                userInfo: [
                    NSLocalizedDescriptionKey: "Failed to inspect running watch processes: \(stderrText.trimmingCharacters(in: .whitespacesAndNewlines))"
                ]
            )
        }

        return stdoutText
    }
}
