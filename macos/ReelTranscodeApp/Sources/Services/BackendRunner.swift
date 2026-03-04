import Foundation

enum BackendRunnerError: LocalizedError {
    case executableNotFound
    case nonZeroExit(code: Int32, stderr: String)

    var errorDescription: String? {
        switch self {
        case .executableNotFound:
            return "ReelTranscodeCore executable not found"
        case let .nonZeroExit(code, stderr):
            return "Backend command failed (\(code)): \(stderr)"
        }
    }
}

struct CommandResult {
    let stdout: String
    let stderr: String
    let exitCode: Int32
}

actor BackendRunner {
    func run(arguments: [String]) async throws -> CommandResult {
        if let primary = Self.embeddedExecutableURL(), FileManager.default.isExecutableFile(atPath: primary.path) {
            do {
                return try await runProcess(executableURL: primary, arguments: arguments)
            } catch let BackendRunnerError.nonZeroExit(code, stderr) {
                // If cached runtime is corrupted, retry once using alternate embedded location.
                if code == 5 || stderr.localizedCaseInsensitiveContains("bootstrap failed") {
                    if let alternate = Self.alternateEmbeddedExecutableURL(excluding: primary),
                       FileManager.default.isExecutableFile(atPath: alternate.path) {
                        return try await runProcess(executableURL: alternate, arguments: arguments)
                    }
                }
                throw BackendRunnerError.nonZeroExit(code: code, stderr: stderr)
            }
        }

        guard let fallback = URL(string: "file:///usr/bin/env") else {
            throw BackendRunnerError.executableNotFound
        }
        return try await runProcess(executableURL: fallback, arguments: ["python3", "-m", "reeltranscode"] + arguments)
    }

    func runJSON<T: Decodable>(arguments: [String], as type: T.Type) async throws -> T {
        let result = try await run(arguments: arguments)
        let data = Data(result.stdout.utf8)
        return try JSONDecoder().decode(T.self, from: data)
    }

    static func bundleEmbeddedExecutableURL() -> URL? {
        Bundle.main.resourceURL?
            .appendingPathComponent("runtime", isDirectory: true)
            .appendingPathComponent("ReelTranscodeCore", isDirectory: true)
            .appendingPathComponent("ReelTranscodeCore")
    }

    static func appSupportEmbeddedExecutableURL() -> URL {
        AppPaths.runtimeDirectory
            .appendingPathComponent("ReelTranscodeCore", isDirectory: true)
            .appendingPathComponent("ReelTranscodeCore")
    }

    static func embeddedExecutableURL() -> URL? {
        // Always prefer the bundle runtime first. It is immutable and less prone to copy corruption.
        if let bundleExecutable = bundleEmbeddedExecutableURL(), FileManager.default.fileExists(atPath: bundleExecutable.path) {
            return bundleExecutable
        }

        let appSupportExecutable = appSupportEmbeddedExecutableURL()
        if FileManager.default.fileExists(atPath: appSupportExecutable.path) {
            return appSupportExecutable
        }
        return nil
    }

    static func alternateEmbeddedExecutableURL(excluding current: URL) -> URL? {
        let candidates: [URL] = [
            bundleEmbeddedExecutableURL(),
            appSupportEmbeddedExecutableURL()
        ].compactMap { $0 }

        for candidate in candidates where candidate.path != current.path {
            if FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
        }
        return nil
    }

    static func ffmpegBinaryURL() -> URL? {
        resolveToolBinary(toolName: "ffmpeg", versionMarker: "ffmpeg version")
    }

    static func ffprobeBinaryURL() -> URL? {
        resolveToolBinary(toolName: "ffprobe", versionMarker: "ffprobe version")
    }

    private static func resolveToolBinary(toolName: String, versionMarker: String) -> URL? {
        var seen = Set<String>()
        var candidates: [URL] = []

        func appendCandidate(_ url: URL?) {
            guard let url else { return }
            let path = url.path
            guard !seen.contains(path) else { return }
            seen.insert(path)
            candidates.append(url)
        }

        appendCandidate(AppPaths.runtimeDirectory.appendingPathComponent("bin/\(toolName)"))
        appendCandidate(Bundle.main.resourceURL?
            .appendingPathComponent("bin", isDirectory: true)
            .appendingPathComponent(toolName))

        for path in [
            "/opt/homebrew/bin/\(toolName)",
            "/usr/local/bin/\(toolName)",
            "/usr/bin/\(toolName)",
            "/bin/\(toolName)"
        ] {
            appendCandidate(URL(fileURLWithPath: path))
        }

        if let envPath = ProcessInfo.processInfo.environment["PATH"] {
            for dir in envPath.split(separator: ":") where !dir.isEmpty {
                appendCandidate(URL(fileURLWithPath: String(dir)).appendingPathComponent(toolName))
            }
        }

        for candidate in candidates where isWorkingToolBinary(candidate, versionMarker: versionMarker) {
            return candidate
        }

        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate.path) {
            return candidate
        }
        return nil
    }

    private static func isWorkingToolBinary(_ url: URL, versionMarker: String) -> Bool {
        guard FileManager.default.isExecutableFile(atPath: url.path) else {
            return false
        }

        let process = Process()
        let out = Pipe()
        let err = Pipe()
        process.executableURL = url
        process.arguments = ["-version"]
        process.standardOutput = out
        process.standardError = err

        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return false
        }

        guard process.terminationStatus == 0 else {
            return false
        }

        let stdout = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        let stderr = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        let combined = (stdout + stderr).lowercased()
        return combined.contains(versionMarker)
    }

    private func runProcess(executableURL: URL, arguments: [String]) async throws -> CommandResult {
        try await withCheckedThrowingContinuation { continuation in
            let process = Process()
            let stdoutPipe = Pipe()
            let stderrPipe = Pipe()

            process.executableURL = executableURL
            process.arguments = arguments
            process.standardOutput = stdoutPipe
            process.standardError = stderrPipe
            process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser

            process.terminationHandler = { proc in
                let stdoutData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
                let stderrData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
                let stdout = String(decoding: stdoutData, as: UTF8.self)
                let stderr = String(decoding: stderrData, as: UTF8.self)
                let result = CommandResult(stdout: stdout, stderr: stderr, exitCode: proc.terminationStatus)

                if proc.terminationStatus == 0 {
                    continuation.resume(returning: result)
                } else {
                    continuation.resume(throwing: BackendRunnerError.nonZeroExit(code: proc.terminationStatus, stderr: stderr))
                }
            }

            do {
                try process.run()
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
}
