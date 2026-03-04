import Foundation
import Darwin

struct LaunchdStatus {
    let installed: Bool
    let running: Bool
    let rawOutput: String
}

final class LaunchdService {
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
        _ = try runLaunchctl(["bootstrap", domain, AppPaths.launchAgentPlistURL.path])
        _ = try runLaunchctl(["kickstart", "-k", "\(domain)/\(AppPaths.launchAgentLabel)"])
    }

    func stop() throws {
        let domain = "gui/\(getuid())"
        _ = try runLaunchctl(["bootout", domain, AppPaths.launchAgentPlistURL.path], allowFailure: true)
    }

    func status() throws -> LaunchdStatus {
        let plistExists = FileManager.default.fileExists(atPath: AppPaths.launchAgentPlistURL.path)
        let domain = "gui/\(getuid())/\(AppPaths.launchAgentLabel)"
        let result = try runLaunchctl(["print", domain], allowFailure: true)
        let running = result.stdout.contains("state = running") || result.stdout.contains("last exit code = 0")
        return LaunchdStatus(installed: plistExists, running: running, rawOutput: result.stdout + result.stderr)
    }

    private func runLaunchctl(_ args: [String], allowFailure: Bool = false) throws -> CommandResult {
        let process = Process()
        let out = Pipe()
        let err = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = args
        process.standardOutput = out
        process.standardError = err
        try process.run()
        process.waitUntilExit()

        let stdout = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        let stderr = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        let result = CommandResult(stdout: stdout, stderr: stderr, exitCode: process.terminationStatus)

        if !allowFailure && process.terminationStatus != 0 {
            throw BackendRunnerError.nonZeroExit(code: process.terminationStatus, stderr: stderr)
        }
        return result
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
            <string>Background</string>
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
