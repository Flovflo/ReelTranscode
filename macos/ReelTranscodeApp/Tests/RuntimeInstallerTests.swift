import XCTest
@testable import ReelTranscodeApp

final class RuntimeInstallerTests: XCTestCase {
    func testEmbeddedBinaryNamesCoverDolbyVisionToolchain() {
        XCTAssertEqual(
            Set(RuntimeInstaller.embeddedBinaryNames),
            Set(["ffmpeg", "ffprobe", "ffmpeg_dovi_compat", "DoViMuxer", "MP4Box", "mediainfo", "mp4muxer"])
        )
    }

    func testShouldReplaceBinaryWhenDestinationIsMissing() throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let source = tmp.appendingPathComponent("source-bin")
        let destination = tmp.appendingPathComponent("dest-bin")
        try Data("abc".utf8).write(to: source)

        let installer = RuntimeInstaller()
        XCTAssertTrue(installer.shouldReplaceBinary(source: source, destination: destination))
    }

    func testShouldReplaceBinaryWhenSameSizeButDifferentContent() throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let source = tmp.appendingPathComponent("source-bin")
        let destination = tmp.appendingPathComponent("dest-bin")
        try Data("abcd".utf8).write(to: source)
        try Data("wxyz".utf8).write(to: destination)

        let installer = RuntimeInstaller()
        XCTAssertTrue(installer.shouldReplaceBinary(source: source, destination: destination))
    }

    func testShouldNotReplaceBinaryWhenContentMatches() throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let source = tmp.appendingPathComponent("source-bin")
        let destination = tmp.appendingPathComponent("dest-bin")
        let content = Data("same-content".utf8)
        try content.write(to: source)
        try content.write(to: destination)

        let installer = RuntimeInstaller()
        XCTAssertFalse(installer.shouldReplaceBinary(source: source, destination: destination))
    }
}
