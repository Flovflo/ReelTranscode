import XCTest
@testable import ReelTranscodeApp

final class LaunchdServiceTests: XCTestCase {
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
