import XCTest
@testable import ReelTranscodeApp

final class WatchProcessServiceTests: XCTestCase {
    func testFindWatchProcessesMatchesConfigPathWithSpaces() throws {
        let configPath = "/Users/florian/Library/Application Support/ReelTranscode/config/reeltranscode.yaml"
        let service = WatchProcessService(
            processListProvider: {
                """
                  101 /Users/florian/Applications/ReelTranscodeApp-Validation.app/Contents/Resources/runtime/ReelTranscodeCore/ReelTranscodeCore --config \(configPath) watch
                  202 /Users/florian/Applications/ReelTranscodeApp.app/Contents/Resources/runtime/ReelTranscodeCore/ReelTranscodeCore --config /tmp/other.yaml watch
                  303 /usr/bin/python3 -m http.server
                """
            }
        )

        let processes = try service.findWatchProcesses(configPath: configPath)

        XCTAssertEqual(processes.count, 1)
        XCTAssertEqual(processes.first?.pid, 101)
        XCTAssertEqual(
            processes.first?.executablePath,
            "/Users/florian/Applications/ReelTranscodeApp-Validation.app/Contents/Resources/runtime/ReelTranscodeCore/ReelTranscodeCore"
        )
        XCTAssertEqual(processes.first?.configPath, configPath)
    }
}
