import XCTest
@testable import ReelTranscodeApp

final class LaunchdServiceTests: XCTestCase {
    func testStatusSummarizesRunningAgentForDashboard() throws {
        let service = LaunchdService(
            commandRunner: { args, _ in
                XCTAssertEqual(args, ["print", "gui/\(getuid())/\(AppPaths.launchAgentLabel)"])
                return CommandResult(
                    stdout: """
                    gui/\(getuid())/\(AppPaths.launchAgentLabel) = {
                        active count = 1
                        state = running
                    }
                    """,
                    stderr: "",
                    exitCode: 0
                )
            }
        )

        let status = try service.status()

        XCTAssertTrue(status.installed)
        XCTAssertTrue(status.running)
        XCTAssertEqual(status.summary, "Watch service is running in the background.")
        XCTAssertTrue(status.rawOutput.contains("state = running"))
    }

    func testStatusSummarizesMissingRunningServiceAsStopped() throws {
        let service = LaunchdService(
            commandRunner: { args, _ in
                XCTAssertEqual(args, ["print", "gui/\(getuid())/\(AppPaths.launchAgentLabel)"])
                return CommandResult(
                    stdout: "",
                    stderr: "Could not find service \"\(AppPaths.launchAgentLabel)\" in domain for user gui: \(getuid())",
                    exitCode: 113
                )
            }
        )

        let status = try service.status()

        XCTAssertTrue(status.installed)
        XCTAssertFalse(status.running)
        XCTAssertEqual(status.summary, "Watch service is stopped.")
    }

    func testStartRetriesBootstrapAfterRecoverableBootstrapError() throws {
        var steps = [
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
            CommandStep(error: BackendRunnerError.nonZeroExit(
                code: 5,
                stderr: "Bootstrap failed: 5: Input/output error"
            )),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 113)),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
        ]
        var calls: [[String]] = []
        var sleptIntervals: [TimeInterval] = []

        let service = LaunchdService(
            commandRunner: { args, _ in
                calls.append(args)
                guard !steps.isEmpty else {
                    XCTFail("Unexpected launchctl command: \(args)")
                    return CommandResult(stdout: "", stderr: "", exitCode: 1)
                }
                return try steps.removeFirst().resolve()
            },
            sleeper: { sleptIntervals.append($0) }
        )

        try service.start()

        XCTAssertEqual(calls.filter { $0.first == "bootstrap" }.count, 2)
        XCTAssertEqual(calls.filter { $0.first == "kickstart" }.count, 1)
        XCTAssertEqual(sleptIntervals, [0.25])
    }

    func testStartAcceptsAlreadyRunningAgentAfterRecoverableBootstrapError() throws {
        var steps = [
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
            CommandStep(error: BackendRunnerError.nonZeroExit(
                code: 5,
                stderr: "Bootstrap failed: 5: Input/output error"
            )),
            CommandStep(result: CommandResult(
                stdout: "state = running\nlast exit code = 0\n",
                stderr: "",
                exitCode: 0
            )),
        ]
        var calls: [[String]] = []
        var sleptIntervals: [TimeInterval] = []

        let service = LaunchdService(
            commandRunner: { args, _ in
                calls.append(args)
                guard !steps.isEmpty else {
                    XCTFail("Unexpected launchctl command: \(args)")
                    return CommandResult(stdout: "", stderr: "", exitCode: 1)
                }
                return try steps.removeFirst().resolve()
            },
            sleeper: { sleptIntervals.append($0) }
        )

        try service.start()

        XCTAssertEqual(calls.map(\.first), ["bootout", "bootstrap", "print"])
        XCTAssertTrue(sleptIntervals.isEmpty)
    }

    func testStartFallsBackToLegacyLoadWhenBootstrapKeepsFailing() throws {
        var steps = [
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
            CommandStep(error: BackendRunnerError.nonZeroExit(
                code: 5,
                stderr: "Bootstrap failed: 5: Input/output error"
            )),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 113)),
            CommandStep(error: BackendRunnerError.nonZeroExit(
                code: 5,
                stderr: "Bootstrap failed: 5: Input/output error"
            )),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 113)),
            CommandStep(error: BackendRunnerError.nonZeroExit(
                code: 5,
                stderr: "Bootstrap failed: 5: Input/output error"
            )),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 113)),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
            CommandStep(result: CommandResult(
                stdout: "state = running\nlast exit code = 0\n",
                stderr: "",
                exitCode: 0
            )),
        ]
        var calls: [[String]] = []
        var sleptIntervals: [TimeInterval] = []

        let service = LaunchdService(
            commandRunner: { args, _ in
                calls.append(args)
                guard !steps.isEmpty else {
                    XCTFail("Unexpected launchctl command: \(args)")
                    return CommandResult(stdout: "", stderr: "", exitCode: 1)
                }
                return try steps.removeFirst().resolve()
            },
            sleeper: { sleptIntervals.append($0) }
        )

        try service.start()

        XCTAssertEqual(
            calls.map(\.first),
            ["bootout", "bootstrap", "print", "bootstrap", "print", "bootstrap", "print", "unload", "load", "print"]
        )
        XCTAssertEqual(sleptIntervals, [0.25, 0.75])
    }

    func testStopSendsTerminateBeforeBootout() throws {
        var steps = [
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
            CommandStep(result: CommandResult(
                stdout: "",
                stderr: "Could not find service \"\(AppPaths.launchAgentLabel)\" in domain for user gui: \(getuid())",
                exitCode: 113
            )),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
        ]
        var calls: [[String]] = []

        let service = LaunchdService(
            commandRunner: { args, _ in
                calls.append(args)
                guard !steps.isEmpty else {
                    XCTFail("Unexpected launchctl command: \(args)")
                    return CommandResult(stdout: "", stderr: "", exitCode: 1)
                }
                return try steps.removeFirst().resolve()
            }
        )

        try service.stop()

        XCTAssertEqual(calls.map(\.first), ["kill", "print", "bootout"])
        XCTAssertEqual(calls.first?[1], "TERM")
    }

    func testStopIgnoresMissingServiceErrors() throws {
        var steps = [
            CommandStep(result: CommandResult(
                stdout: "",
                stderr: "Could not find service \"\(AppPaths.launchAgentLabel)\" in domain for user gui: \(getuid())",
                exitCode: 113
            )),
            CommandStep(result: CommandResult(
                stdout: "",
                stderr: "Could not find service \"\(AppPaths.launchAgentLabel)\" in domain for user gui: \(getuid())",
                exitCode: 113
            )),
            CommandStep(result: CommandResult(
                stdout: "",
                stderr: "Boot-out failed: 113: Could not find specified service",
                exitCode: 113
            )),
        ]
        var calls: [[String]] = []

        let service = LaunchdService(
            commandRunner: { args, _ in
                calls.append(args)
                guard !steps.isEmpty else {
                    XCTFail("Unexpected launchctl command: \(args)")
                    return CommandResult(stdout: "", stderr: "", exitCode: 1)
                }
                return try steps.removeFirst().resolve()
            }
        )

        XCTAssertNoThrow(try service.stop())
        XCTAssertEqual(calls.map(\.first), ["kill", "print", "bootout"])
    }

    func testStopWaitsBrieflyForGracefulExitBeforeBootout() throws {
        var steps = [
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
            CommandStep(result: CommandResult(stdout: "state = running\n", stderr: "", exitCode: 0)),
            CommandStep(result: CommandResult(
                stdout: "",
                stderr: "Could not find service \"\(AppPaths.launchAgentLabel)\" in domain for user gui: \(getuid())",
                exitCode: 113
            )),
            CommandStep(result: CommandResult(stdout: "", stderr: "", exitCode: 0)),
        ]
        var calls: [[String]] = []
        var sleptIntervals: [TimeInterval] = []

        let service = LaunchdService(
            commandRunner: { args, _ in
                calls.append(args)
                guard !steps.isEmpty else {
                    XCTFail("Unexpected launchctl command: \(args)")
                    return CommandResult(stdout: "", stderr: "", exitCode: 1)
                }
                return try steps.removeFirst().resolve()
            },
            sleeper: { sleptIntervals.append($0) }
        )

        try service.stop()

        XCTAssertEqual(calls.map(\.first), ["kill", "print", "print", "bootout"])
        XCTAssertEqual(sleptIntervals, [0.2])
    }

    func testPlistGenerationIncludesExpectedCommand() {
        let plist = LaunchdService.generatePlist(
            label: "com.reelfin.reeltranscode.watch",
            executablePath: "/Applications/ReelTranscode.app/Contents/Resources/runtime/ReelTranscodeCore/ReelTranscodeCore",
            configPath: "/Users/test/Library/Application Support/ReelTranscode/config/reeltranscode.yaml",
            workingDirectory: "/Users/test/Library/Application Support/ReelTranscode",
            stdoutPath: "/tmp/out.log",
            stderrPath: "/tmp/err.log"
        )

        XCTAssertTrue(plist.contains("<string>watch</string>"))
        XCTAssertTrue(plist.contains("<string>--config</string>"))
        XCTAssertTrue(plist.contains("com.reelfin.reeltranscode.watch"))
    }

    func testPlistGenerationEscapesXMLSensitiveCharacters() {
        let plist = LaunchdService.generatePlist(
            label: "com.reelfin.reeltranscode.watch",
            executablePath: "/Applications/Reel & Transcode.app/Contents/Resources/runtime/ReelTranscodeCore/ReelTranscodeCore",
            configPath: "/Users/test/Library/Application Support/ReelTranscode/config/reeltranscode.yaml",
            workingDirectory: "/Users/test/Library/Application Support/ReelTranscode",
            stdoutPath: "/tmp/out<&>.log",
            stderrPath: "/tmp/err<&>.log"
        )

        XCTAssertTrue(plist.contains("Reel &amp; Transcode.app"))
        XCTAssertTrue(plist.contains("/tmp/out&lt;&amp;&gt;.log"))
        XCTAssertTrue(plist.contains("/tmp/err&lt;&amp;&gt;.log"))
    }
}

private struct CommandStep {
    let result: CommandResult?
    let error: Error?

    init(result: CommandResult) {
        self.result = result
        self.error = nil
    }

    init(error: Error) {
        self.result = nil
        self.error = error
    }

    func resolve() throws -> CommandResult {
        if let error {
            throw error
        }
        guard let result else {
            throw XCTSkip("Missing scripted launchctl result")
        }
        return result
    }
}
