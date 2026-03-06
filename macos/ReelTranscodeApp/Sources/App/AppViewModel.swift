import Foundation
import SwiftUI
import AppKit

enum SidebarSection: String, CaseIterable, Identifiable {
    case dashboard = "Dashboard"
    case jobs = "Jobs"
    case configuration = "Configuration"
    case logs = "Logs"

    var id: String { rawValue }
}

@MainActor
final class AppViewModel: ObservableObject {
    @Published var selectedSection: SidebarSection? = .dashboard
    @Published var status: StatusResponse?
    @Published var config = ConfigDocument()
    @Published var configValidationErrors: [ConfigValidationError] = []
    @Published var logsText = ""
    @Published var onboardingRequired = true
    @Published var isBusy = false
    @Published var isServiceRunning = false
    @Published var serviceStatusText = ""
    @Published var lastError: String?

    private let backendRunner = BackendRunner()
    private let runtimeInstaller = RuntimeInstaller()
    private let launchdService = LaunchdService()
    private let logReader = LogReader()
    private var inAppWatchProcess: Process?
    private var inAppWatchStdoutHandle: FileHandle?
    private var inAppWatchStderrHandle: FileHandle?

    func bootstrap() async {
        do {
            try runtimeInstaller.prepareDirectories()
            try runtimeInstaller.installEmbeddedRuntimeIfAvailable()
            onboardingRequired = !FileManager.default.fileExists(atPath: AppPaths.configFileURL.path)
            if !onboardingRequired {
                await loadConfigFromBackendExport()
                do {
                    try persistCurrentConfig()
                } catch {
                    // Keep app usable even if tooling binaries are temporarily unavailable.
                }
                await refreshStatus()
                refreshLogs()
                refreshLaunchdStatus()
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    func completeOnboarding() async {
        lastError = nil
        await saveConfig()
        await validateConfig()
        if lastError == nil && configValidationErrors.isEmpty {
            onboardingRequired = false
            refreshLaunchdStatus()
            await refreshStatus()
        }
    }

    func runBatch() async {
        do {
            try persistCurrentConfig()
        } catch {
            lastError = error.localizedDescription
            return
        }
        await runBackendCommand(arguments: ["--config", AppPaths.configFileURL.path, "batch"])
        await refreshStatus()
    }

    func refreshStatus() async {
        do {
            let response: StatusResponse = try await backendRunner.runJSON(
                arguments: ["--config", AppPaths.configFileURL.path, "status", "--json", "--limit", "100"],
                as: StatusResponse.self
            )
            status = response
        } catch {
            lastError = error.localizedDescription
        }
    }

    func validateConfig() async {
        do {
            let response: ConfigValidateResponse = try await backendRunner.runJSON(
                arguments: ["--config", AppPaths.configFileURL.path, "config-validate", "--json"],
                as: ConfigValidateResponse.self
            )
            configValidationErrors = response.errors
        } catch {
            lastError = error.localizedDescription
        }
    }

    func saveConfig() async {
        do {
            try persistCurrentConfig()
            await loadConfigFromBackendExport()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func loadConfigFromBackendExport() async {
        do {
            let response: ConfigExportResponse = try await backendRunner.runJSON(
                arguments: ["--config", AppPaths.configFileURL.path, "config-export", "--json"],
                as: ConfigExportResponse.self
            )
            config = ConfigDocument.fromExportedConfig(response.config)
        } catch {
            // Keep editable in-memory config if backend export is unavailable.
        }
    }

    func startWatchService() {
        do {
            try persistCurrentConfig()
            try resetWatchLogsForNewSession()

            guard FileManager.default.fileExists(atPath: AppPaths.configFileURL.path) else {
                throw NSError(
                    domain: "ReelTranscodeApp",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "Config file not found. Save your configuration first."]
                )
            }

            let resolvedExecutableURL = try BackendRunner.requireExecutableURL()

            try launchdService.installOrUpdateWatchAgent(
                executablePath: resolvedExecutableURL.path,
                configPath: AppPaths.configFileURL.path,
                workingDirectory: AppPaths.appSupportDirectory.path
            )
            do {
                try launchdService.start()
                stopInAppWatchProcess()
                refreshLaunchdStatus()
            } catch let launchdError {
                do {
                    try startInAppWatchProcess(executableURL: resolvedExecutableURL)
                    isServiceRunning = true
                    serviceStatusText = "launchd unavailable, running in-app watch fallback.\n\(launchdError.localizedDescription)"
                    lastError = nil
                } catch let fallbackError {
                    throw NSError(
                        domain: "ReelTranscodeApp",
                        code: 2,
                        userInfo: [
                            NSLocalizedDescriptionKey: "launchd start failed: \(launchdError.localizedDescription)\nFallback failed: \(fallbackError.localizedDescription)"
                        ]
                    )
                }
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    func stopWatchService() {
        var errors: [String] = []
        do {
            try launchdService.stop()
        } catch {
            errors.append(error.localizedDescription)
        }
        stopInAppWatchProcess()
        refreshLaunchdStatus()
        if !errors.isEmpty {
            lastError = errors.joined(separator: "\n")
        }
    }

    func refreshLaunchdStatus() {
        if let process = inAppWatchProcess, process.isRunning {
            isServiceRunning = true
            serviceStatusText = "In-app watch process running (fallback mode). PID \(process.processIdentifier)"
            return
        }
        do {
            let status = try launchdService.status()
            isServiceRunning = status.running
            serviceStatusText = status.rawOutput
        } catch {
            lastError = error.localizedDescription
        }
    }

    func refreshLogs() {
        logsText = logReader.combinedWatchLogs()
    }

    func pickFolder() -> String? {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true

        if panel.runModal() == .OK {
            return panel.url?.path
        }
        return nil
    }

    private func runBackendCommand(arguments: [String]) async {
        isBusy = true
        defer { isBusy = false }

        do {
            _ = try await backendRunner.run(arguments: arguments)
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func persistCurrentConfig() throws {
        try runtimeInstaller.prepareDirectories()
        try runtimeInstaller.installEmbeddedRuntimeIfAvailable()

        guard let ffmpegURL = BackendRunner.ffmpegBinaryURL(),
              let ffprobeURL = BackendRunner.ffprobeBinaryURL() else {
            throw NSError(
                domain: "ReelTranscodeApp",
                code: 3,
                userInfo: [NSLocalizedDescriptionKey: "No working ffmpeg/ffprobe found. Install ffmpeg or provide valid binaries."]
            )
        }
        config.ffmpegBin = ffmpegURL.path
        config.ffprobeBin = ffprobeURL.path
        config.doviMuxerBin = BackendRunner.doviMuxerBinaryURL()?.path ?? ""
        config.mp4boxBin = BackendRunner.mp4boxBinaryURL()?.path ?? ""
        config.mediainfoBin = BackendRunner.mediainfoBinaryURL()?.path ?? ""
        config.mp4muxerBin = BackendRunner.mp4muxerBinaryURL()?.path ?? ""
        try config.toYAML().write(to: AppPaths.configFileURL, atomically: true, encoding: .utf8)
    }

    private func startInAppWatchProcess(executableURL: URL) throws {
        if let process = inAppWatchProcess, process.isRunning {
            return
        }

        try runtimeInstaller.prepareDirectories()

        let stdoutURL = AppPaths.watchStdoutURL
        let stderrURL = AppPaths.watchStderrURL
        if !FileManager.default.fileExists(atPath: stdoutURL.path) {
            FileManager.default.createFile(atPath: stdoutURL.path, contents: Data())
        }
        if !FileManager.default.fileExists(atPath: stderrURL.path) {
            FileManager.default.createFile(atPath: stderrURL.path, contents: Data())
        }

        let stdoutHandle = try FileHandle(forWritingTo: stdoutURL)
        let stderrHandle = try FileHandle(forWritingTo: stderrURL)
        stdoutHandle.seekToEndOfFile()
        stderrHandle.seekToEndOfFile()

        let process = Process()
        process.executableURL = executableURL
        process.arguments = ["--config", AppPaths.configFileURL.path, "watch"]
        process.currentDirectoryURL = AppPaths.appSupportDirectory
        process.standardOutput = stdoutHandle
        process.standardError = stderrHandle
        process.terminationHandler = { [weak self] proc in
            Task { @MainActor in
                guard let self else { return }
                if self.inAppWatchProcess === proc {
                    self.inAppWatchProcess = nil
                    self.inAppWatchStdoutHandle?.closeFile()
                    self.inAppWatchStderrHandle?.closeFile()
                    self.inAppWatchStdoutHandle = nil
                    self.inAppWatchStderrHandle = nil
                    self.refreshLaunchdStatus()
                }
            }
        }
        try process.run()

        inAppWatchStdoutHandle = stdoutHandle
        inAppWatchStderrHandle = stderrHandle
        inAppWatchProcess = process
    }

    private func stopInAppWatchProcess() {
        guard let process = inAppWatchProcess else { return }
        if process.isRunning {
            process.terminate()
        }
        inAppWatchProcess = nil
        inAppWatchStdoutHandle?.closeFile()
        inAppWatchStderrHandle?.closeFile()
        inAppWatchStdoutHandle = nil
        inAppWatchStderrHandle = nil
    }

    private func resetWatchLogsForNewSession() throws {
        try runtimeInstaller.prepareDirectories()
        let files = [AppPaths.watchStdoutURL, AppPaths.watchStderrURL]
        for file in files {
            if FileManager.default.fileExists(atPath: file.path) {
                let handle = try FileHandle(forWritingTo: file)
                try handle.truncate(atOffset: 0)
                try handle.close()
            } else {
                FileManager.default.createFile(atPath: file.path, contents: Data())
            }
        }
    }
}
