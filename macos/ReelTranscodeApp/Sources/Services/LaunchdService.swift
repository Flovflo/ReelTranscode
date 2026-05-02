import Foundation
import Darwin

struct LaunchdStatus: Sendable {
    let installed: Bool
    let running: Bool
    let summary: String
    let technicalDetails: String?
    let rawOutput: String
}

typealias LaunchctlCommandRunner = (_ args: [String], _ allowFailure: Bool) throws -> CommandResult

final class LaunchdService: @unchecked Sendable {
    private let commandRunner: LaunchctlCommandRunner
    private let sleeper: (TimeInterval) -> Void

    init(
        commandRunner: LaunchctlCommandRunner? = nil,
        sleeper: ((TimeInterval) -> Void)? = nil
    ) {
        self.commandRunner = commandRunner ?? Self.defaultRunLaunchctl
        self.sleeper = sleeper ?? { Thread.sleep(forTimeInterval: $0) }
    }

    func installOrUpdateWatchAgent(executablePath: String, configPath: String, workingDirectory: String) throws {
        try FileManager.default.createDirectory(at: AppPaths.launchAgentsDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.logsDirectory, withIntermediateDirectories: true)

        let plist = Self.generatePlist(
            label: AppPaths.launchAgentLabel,
            executablePath: executablePath,
            configPath: configPath,
            workingDirectory: workingDirectory,
            stdoutPath: AppPaths.watchStdoutURL.path,
            stderrPath: AppPaths.watchStderrURL.path
        )
        let plistData = Data(plist.utf8)
        _ = try PropertyListSerialization.propertyList(from: plistData, options: [], format: nil)
        try plist.write(to: AppPaths.launchAgentPlistURL, atomically: true, encoding: .utf8)
    }

    func start() throws {
        let domain = "gui/\(getuid())"
        _ = try runLaunchctl(["bootout", domain, AppPaths.launchAgentPlistURL.path], allowFailure: true)
        do {
            try bootstrapAndKickstart(domain: domain)
        } catch let BackendRunnerError.nonZeroExit(code, stderr) {
            guard Self.isRecoverableBootstrapFailure(code: code, stderr: stderr),
                  try recoverFromBootstrapFailure(domain: domain) else {
                throw BackendRunnerError.nonZeroExit(code: code, stderr: stderr)
            }
        }
    }

    func stop() throws {
        let domain = "gui/\(getuid())"
        let label = "\(domain)/\(AppPaths.launchAgentLabel)"
        let terminateResult = try runLaunchctl(["kill", "TERM", label], allowFailure: true)
        try Self.throwIfUnexpectedStopFailure(terminateResult)
        try waitForGracefulStop(label: label)

        let bootoutResult = try runLaunchctl(["bootout", domain, AppPaths.launchAgentPlistURL.path], allowFailure: true)
        try Self.throwIfUnexpectedStopFailure(bootoutResult)
    }

    func status() throws -> LaunchdStatus {
        let plistExists = FileManager.default.fileExists(atPath: AppPaths.launchAgentPlistURL.path)
        let domain = "gui/\(getuid())/\(AppPaths.launchAgentLabel)"
        let result = try runLaunchctl(["print", domain], allowFailure: true)
        let rawOutput = Self.trimmedOutput(result.stdout + result.stderr)
        let running = Self.isRunningStatus(rawOutput)
        return LaunchdStatus(
            installed: plistExists,
            running: running,
            summary: Self.describeStatus(installed: plistExists, exitCode: result.exitCode, output: rawOutput),
            technicalDetails: Self.technicalDetails(for: rawOutput),
            rawOutput: rawOutput
        )
    }

    private func bootstrapAndKickstart(domain: String) throws {
        _ = try runLaunchctl(["bootstrap", domain, AppPaths.launchAgentPlistURL.path])
        _ = try runLaunchctl(["kickstart", "-k", "\(domain)/\(AppPaths.launchAgentLabel)"])
    }

    private func recoverFromBootstrapFailure(domain: String) throws -> Bool {
        if try restartKnownAgent(domain: domain) {
            return true
        }

        var lastRecoverableError: BackendRunnerError?
        for delay in [0.25, 0.75] {
            sleeper(delay)

            do {
                try bootstrapAndKickstart(domain: domain)
                return true
            } catch let BackendRunnerError.nonZeroExit(code, stderr) {
                guard Self.isRecoverableBootstrapFailure(code: code, stderr: stderr) else {
                    throw BackendRunnerError.nonZeroExit(code: code, stderr: stderr)
                }
                lastRecoverableError = BackendRunnerError.nonZeroExit(code: code, stderr: stderr)
                if try restartKnownAgent(domain: domain) {
                    return true
                }
            }
        }

        if try legacyLoad(domain: domain) {
            return true
        }

        if let lastRecoverableError {
            throw lastRecoverableError
        }
        return false
    }

    private func restartKnownAgent(domain: String) throws -> Bool {
        let label = "\(domain)/\(AppPaths.launchAgentLabel)"
        let current = try runLaunchctl(["print", label], allowFailure: true)
        guard current.exitCode == 0 else {
            return false
        }
        if Self.isRunningStatus(current.stdout + current.stderr) {
            return true
        }

        _ = try runLaunchctl(["kickstart", "-k", label], allowFailure: true)
        let restarted = try runLaunchctl(["print", label], allowFailure: true)
        return restarted.exitCode == 0 && Self.isRunningStatus(restarted.stdout + restarted.stderr)
    }

    private func legacyLoad(domain: String) throws -> Bool {
        let label = "\(domain)/\(AppPaths.launchAgentLabel)"
        _ = try runLaunchctl(["unload", AppPaths.launchAgentPlistURL.path], allowFailure: true)
        _ = try runLaunchctl(["load", "-w", AppPaths.launchAgentPlistURL.path], allowFailure: true)

        let loaded = try runLaunchctl(["print", label], allowFailure: true)
        if loaded.exitCode == 0 && Self.isRunningStatus(loaded.stdout + loaded.stderr) {
            return true
        }

        _ = try runLaunchctl(["kickstart", "-k", label], allowFailure: true)
        let restarted = try runLaunchctl(["print", label], allowFailure: true)
        return restarted.exitCode == 0 && Self.isRunningStatus(restarted.stdout + restarted.stderr)
    }

    private func waitForGracefulStop(label: String) throws {
        for delay in [0.2, 0.3, 0.5] {
            let current = try runLaunchctl(["print", label], allowFailure: true)
            let output = current.stdout + current.stderr
            if Self.isStoppedStatus(exitCode: current.exitCode, output: output) {
                return
            }
            sleeper(delay)
        }
    }

    private func runLaunchctl(_ args: [String], allowFailure: Bool = false) throws -> CommandResult {
        try commandRunner(args, allowFailure)
    }

    private static func defaultRunLaunchctl(_ args: [String], allowFailure: Bool = false) throws -> CommandResult {
        let process = Process()
        let out = Pipe()
        let err = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = args
        process.standardOutput = out
        process.standardError = err
        try process.run()
        let timeoutSeconds: TimeInterval = 5
        let stdoutBuffer = LockedDataBuffer()
        let stderrBuffer = LockedDataBuffer()

        out.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            stdoutBuffer.append(data)
        }

        err.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            stderrBuffer.append(data)
        }

        if !waitForExit(process, timeout: timeoutSeconds) {
            process.terminate()
            if !waitForExit(process, timeout: 0.5), process.isRunning {
                kill(process.processIdentifier, SIGKILL)
                _ = waitForExit(process, timeout: 0.5)
            }
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            let command = (["/bin/launchctl"] + args).joined(separator: " ")
            throw BackendRunnerError.nonZeroExit(
                code: 124,
                stderr: "launchctl timed out after \(timeoutSeconds)s: \(command)"
            )
        }

        process.waitUntilExit()
        out.fileHandleForReading.readabilityHandler = nil
        err.fileHandleForReading.readabilityHandler = nil
        stdoutBuffer.append(out.fileHandleForReading.readDataToEndOfFile())
        stderrBuffer.append(err.fileHandleForReading.readDataToEndOfFile())
        let stdout = String(decoding: stdoutBuffer.data, as: UTF8.self)
        let stderr = String(decoding: stderrBuffer.data, as: UTF8.self)
        let result = CommandResult(stdout: stdout, stderr: stderr, exitCode: process.terminationStatus)

        if !allowFailure && process.terminationStatus != 0 {
            throw BackendRunnerError.nonZeroExit(code: process.terminationStatus, stderr: stderr)
        }
        return result
    }

    private static func waitForExit(_ process: Process, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        return !process.isRunning
    }

    private static func isRecoverableBootstrapFailure(code: Int32, stderr: String) -> Bool {
        let normalized = stderr.lowercased()
        return code == 5
            || normalized.contains("bootstrap failed")
            || normalized.contains("input/output error")
    }

    private static func isRunningStatus(_ output: String) -> Bool {
        output.lowercased().contains("state = running")
    }

    private static func describeStatus(installed: Bool, exitCode: Int32, output: String) -> String {
        let normalized = output.lowercased()
        if isRunningStatus(output) {
            return "Watch service is running in the background."
        }
        if normalized.contains("state = spawn scheduled")
            || normalized.contains("state = waiting")
            || normalized.contains("state = launching") {
            return "Watch service is starting in the background."
        }
        if !installed {
            return "Watch service is not installed yet."
        }
        if exitCode == 113 || isMissingServiceOutput(output) {
            return "Watch service is stopped."
        }
        if normalized.contains("state = exited") || normalized.contains("state = throttled") {
            return "Watch service is stopped."
        }
        if exitCode == 0 {
            return "Watch service is installed, but not currently running."
        }
        return "Watch service status could not be confirmed."
    }

    private static func isStoppedStatus(exitCode: Int32, output: String) -> Bool {
        let normalized = output.lowercased()
        if exitCode == 113 || isMissingServiceOutput(output) {
            return true
        }
        return normalized.contains("state = exited")
            || normalized.contains("state = throttled")
            || normalized.contains("state = suspended")
    }

    private static func technicalDetails(for output: String) -> String? {
        let trimmed = trimmedOutput(output)
        guard !trimmed.isEmpty, !isMissingServiceOutput(trimmed) else {
            return nil
        }
        return trimmed
    }

    private static func trimmedOutput(_ output: String) -> String {
        output.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func isMissingServiceOutput(_ output: String) -> Bool {
        let normalized = output.lowercased()
        return normalized.contains("could not find service")
            || normalized.contains("could not find specified service")
            || normalized.contains("unknown service")
    }

    private static func throwIfUnexpectedStopFailure(_ result: CommandResult) throws {
        guard result.exitCode != 0 else {
            return
        }

        let combinedOutput = trimmedOutput(result.stdout + result.stderr)
        guard !isMissingServiceOutput(combinedOutput), result.exitCode != 113 else {
            return
        }

        throw BackendRunnerError.nonZeroExit(code: result.exitCode, stderr: combinedOutput)
    }

    static func generatePlist(
        label: String,
        executablePath: String,
        configPath: String,
        workingDirectory: String,
        stdoutPath: String,
        stderrPath: String
    ) -> String {
        let safeLabel = xmlEscaped(label)
        let safeExecutablePath = xmlEscaped(executablePath)
        let safeConfigPath = xmlEscaped(configPath)
        let safeWorkingDirectory = xmlEscaped(workingDirectory)
        let safeStdoutPath = xmlEscaped(stdoutPath)
        let safeStderrPath = xmlEscaped(stderrPath)

        return """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
          <dict>
            <key>Label</key>
            <string>\(safeLabel)</string>
            <key>ProgramArguments</key>
            <array>
              <string>\(safeExecutablePath)</string>
              <string>--config</string>
              <string>\(safeConfigPath)</string>
              <string>watch</string>
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <dict>
              <key>SuccessfulExit</key>
              <false/>
            </dict>
            <key>WorkingDirectory</key>
            <string>\(safeWorkingDirectory)</string>
            <key>StandardOutPath</key>
            <string>\(safeStdoutPath)</string>
            <key>StandardErrorPath</key>
            <string>\(safeStderrPath)</string>
            <key>ProcessType</key>
            <string>Standard</string>
          </dict>
        </plist>
        """
    }

    private static func xmlEscaped(_ value: String) -> String {
        value
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\"", with: "&quot;")
            .replacingOccurrences(of: "'", with: "&apos;")
    }
}

private final class LockedDataBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var storage = Data()

    var data: Data {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    func append(_ data: Data) {
        lock.lock()
        storage.append(data)
        lock.unlock()
    }
}
