import Foundation
import SwiftUI
import AppKit
import Darwin

enum SidebarSection: String, CaseIterable, Identifiable {
    case dashboard = "Dashboard"
    case ingest = "Library Ingest"
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
    @Published var isRefreshing = false
    @Published var isServiceRunning = false
    @Published var serviceStatusText = ""
    @Published var serviceDiagnosticsText = ""
    @Published var lastRefreshAt: Date?
    @Published var lastError: String?

    private let backendRunner = BackendRunner()
    private let runtimeInstaller = RuntimeInstaller()
    private let launchdService = LaunchdService()
    private let logReader = LogReader()
    private let watchProcessService = WatchProcessService()
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
                await refreshAll()
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
            await refreshAll(reportErrors: true)
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
        await refreshAll(reportErrors: true)
    }

    func refreshAll(reportErrors: Bool = false) async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer {
            isRefreshing = false
            lastRefreshAt = Date()
        }

        await refreshStatus(reportErrors: reportErrors)
        refreshLogs()
    }

    func runAutomaticRefreshLoop() async {
        guard !onboardingRequired else { return }
        while !Task.isCancelled && !onboardingRequired {
            await refreshAll()
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        }
    }

    func refreshStatus(reportErrors: Bool = false) async {
        do {
            let response: StatusResponse = try await backendRunner.runJSON(
                arguments: ["--config", AppPaths.configFileURL.path, "status", "--json", "--limit", "250"],
                as: StatusResponse.self
            )
            status = response
        } catch {
            if reportErrors {
                lastError = error.localizedDescription
            }
        }
        await refreshLaunchdStatusAsync()
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
        Task {
            await performStartWatchService()
        }
    }

    func stopWatchService() {
        Task {
            await performStopWatchService()
        }
    }

    func refreshLaunchdStatus() {
        Task {
            await refreshLaunchdStatusAsync()
        }
    }

    private func performStartWatchService() async {
        guard !isBusy else { return }
        isBusy = true
        lastError = nil
        serviceStatusText = "Starting watch service..."
        serviceDiagnosticsText = ""
        defer { isBusy = false }

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
            let existingLaunchdStatus = try? await runBlocking {
                try self.launchdService.status()
            }
            if existingLaunchdStatus?.running != true {
                _ = try await stopMatchingWatchProcesses()
            }

            try launchdService.installOrUpdateWatchAgent(
                executablePath: resolvedExecutableURL.path,
                configPath: AppPaths.configFileURL.path,
                workingDirectory: AppPaths.appSupportDirectory.path
            )
            do {
                let launchdService = self.launchdService
                try await runBlocking {
                    try launchdService.start()
                }
                let detachedFallback = detachInAppWatchProcess()
                try await runBlocking {
                    Self.stopDetachedInAppWatchProcess(detachedFallback)
                }
                lastError = nil
                await refreshAll(reportErrors: true)
            } catch let launchdError {
                do {
                    try startInAppWatchProcess(executableURL: resolvedExecutableURL)
                    isServiceRunning = true
                    serviceStatusText = "Watch service is running inside the app because launchd was unavailable."
                    serviceDiagnosticsText = launchdError.localizedDescription
                    lastError = nil
                    await refreshAll(reportErrors: true)
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

    private func performStopWatchService() async {
        guard !isBusy else { return }
        isBusy = true
        lastError = nil
        serviceStatusText = "Stopping watch service..."
        serviceDiagnosticsText = ""
        let detachedFallback = detachInAppWatchProcess()
        defer { isBusy = false }

        var errors: [String] = []
        do {
            try await runBlocking {
                Self.stopDetachedInAppWatchProcess(detachedFallback)
            }
        } catch {
            errors.append(error.localizedDescription)
        }

        do {
            let launchdService = self.launchdService
            try await runBlocking {
                try launchdService.stop()
            }
        } catch {
            errors.append(error.localizedDescription)
        }

        do {
            _ = try await stopMatchingWatchProcesses()
        } catch {
            errors.append(error.localizedDescription)
        }

        await refreshAll(reportErrors: true)
        if !errors.isEmpty {
            lastError = errors.joined(separator: "\n")
        }
    }

    private func refreshLaunchdStatusAsync() async {
        if let process = inAppWatchProcess, process.isRunning {
            isServiceRunning = true
            serviceStatusText = "Watch service is running inside the app (fallback mode)."
            serviceDiagnosticsText = "Fallback PID \(process.processIdentifier)"
            reconcileStatusWithObservedServiceState()
            return
        }

        let matchingProcesses = (try? await findMatchingWatchProcesses()) ?? []
        do {
            let launchdService = self.launchdService
            let status = try await runBlocking {
                try launchdService.status()
            }
            if status.running {
                isServiceRunning = true
                serviceStatusText = status.summary
                serviceDiagnosticsText = combinedDiagnostics(
                    primary: status.technicalDetails,
                    matchingProcesses: matchingProcesses,
                    label: "Observed watch process"
                )
            } else if !matchingProcesses.isEmpty {
                isServiceRunning = true
                serviceStatusText = matchingProcesses.count == 1
                    ? "Watch service is running from another ReelTranscode build."
                    : "Multiple watch processes are running outside launchd."
                serviceDiagnosticsText = combinedDiagnostics(
                    primary: status.technicalDetails,
                    matchingProcesses: matchingProcesses,
                    label: "Detached watch process"
                )
            } else {
                isServiceRunning = false
                serviceStatusText = status.summary
                serviceDiagnosticsText = status.technicalDetails ?? ""
            }
        } catch {
            if !matchingProcesses.isEmpty {
                isServiceRunning = true
                serviceStatusText = matchingProcesses.count == 1
                    ? "Watch service is running from another ReelTranscode build."
                    : "Multiple watch processes are running outside launchd."
                serviceDiagnosticsText = combinedDiagnostics(
                    primary: error.localizedDescription,
                    matchingProcesses: matchingProcesses,
                    label: "Detached watch process"
                )
            } else {
                isServiceRunning = false
                serviceDiagnosticsText = error.localizedDescription
                if serviceStatusText.isEmpty {
                    serviceStatusText = "Watch service status unavailable."
                }
            }
        }
        reconcileStatusWithObservedServiceState()
    }

    func refreshLogs() {
        logsText = logReader.combinedWatchLogs()
    }

    func pauseWatch() async {
        await runBackendCommand(arguments: ["--config", AppPaths.configFileURL.path, "watch-pause"])
        await refreshAll(reportErrors: true)
    }

    func resumeWatch() async {
        await runBackendCommand(arguments: ["--config", AppPaths.configFileURL.path, "watch-resume"])
        await refreshAll(reportErrors: true)
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
        config.normalizeManagedPathsForPersistence()
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

    private func reconcileStatusWithObservedServiceState() {
        guard let current = status else { return }

        let mergedRuntime = current.runtime.with(
            watchRunning: isServiceRunning,
            queuedPaths: max(current.runtime.queuedPaths, current.summary.pending),
            activeWorkers: max(current.runtime.activeWorkers, current.summary.running),
            maxWorkers: max(current.runtime.maxWorkers, current.runtime.activeWorkers, current.summary.running, 1)
        )
        status = current.withRuntime(mergedRuntime)
    }

    private func findMatchingWatchProcesses() async throws -> [WatchProcessInfo] {
        let excludedPIDs = Set(inAppWatchProcess.map { [$0.processIdentifier] } ?? [])
        let watchProcessService = self.watchProcessService
        return try await runBlocking {
            try watchProcessService.findWatchProcesses(configPath: AppPaths.configFileURL.path)
                .filter { !excludedPIDs.contains($0.pid) }
        }
    }

    private func stopMatchingWatchProcesses() async throws -> [WatchProcessInfo] {
        let excludedPIDs = Set(inAppWatchProcess.map { [$0.processIdentifier] } ?? [])
        let watchProcessService = self.watchProcessService
        return try await runBlocking {
            try watchProcessService.stopWatchProcesses(
                configPath: AppPaths.configFileURL.path,
                excluding: excludedPIDs
            )
        }
    }

    private func combinedDiagnostics(
        primary: String?,
        matchingProcesses: [WatchProcessInfo],
        label: String
    ) -> String {
        var sections: [String] = []

        if let primary, !primary.isEmpty {
            sections.append(primary)
        }
        if !matchingProcesses.isEmpty {
            let details = matchingProcesses
                .map { "\(label) PID \($0.pid)\n\($0.executablePath)" }
                .joined(separator: "\n\n")
            sections.append(details)
        }
        return sections.joined(separator: "\n\n")
    }

    private func runBlocking<T: Sendable>(_ operation: @escaping @Sendable () throws -> T) async throws -> T {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    continuation.resume(returning: try operation())
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private func detachInAppWatchProcess() -> DetachedWatchProcess? {
        guard let process = inAppWatchProcess else { return nil }
        let detached = DetachedWatchProcess(
            process: process,
            stdoutHandle: inAppWatchStdoutHandle,
            stderrHandle: inAppWatchStderrHandle
        )
        inAppWatchProcess = nil
        inAppWatchStdoutHandle = nil
        inAppWatchStderrHandle = nil
        return detached
    }

    nonisolated private static func stopDetachedInAppWatchProcess(_ detached: DetachedWatchProcess?) {
        guard let detached else { return }
        defer {
            detached.stdoutHandle?.closeFile()
            detached.stderrHandle?.closeFile()
        }

        guard detached.process.isRunning else { return }
        detached.process.terminate()
        if waitForDetachedProcessExit(detached.process, timeout: 2) {
            return
        }

        kill(detached.process.processIdentifier, SIGKILL)
        _ = waitForDetachedProcessExit(detached.process, timeout: 1)
    }

    nonisolated private static func waitForDetachedProcessExit(_ process: Process, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        return !process.isRunning
    }
}

private struct DetachedWatchProcess: @unchecked Sendable {
    let process: Process
    let stdoutHandle: FileHandle?
    let stderrHandle: FileHandle?
}
