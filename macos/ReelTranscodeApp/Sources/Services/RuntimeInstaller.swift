import Foundation
import CryptoKit

final class RuntimeInstaller {
    static let embeddedBinaryNames = ["ffmpeg", "ffprobe", "ffmpeg_dovi_compat", "DoViMuxer", "MP4Box", "mediainfo", "mp4muxer"]

    func prepareDirectories() throws {
        try FileManager.default.createDirectory(at: AppPaths.appSupportDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.configDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.logsDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.runtimeDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.runtimeBinDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.runtimeLibDirectory, withIntermediateDirectories: true)
    }

    func installEmbeddedRuntimeIfAvailable() throws {
        try prepareDirectories()

        guard let resourceRoot = Bundle.main.resourceURL else { return }
        let sourceRuntime = resourceRoot.appendingPathComponent("runtime", isDirectory: true)
        let sourceBin = resourceRoot.appendingPathComponent("bin", isDirectory: true)
        let sourceLib = resourceRoot.appendingPathComponent("lib", isDirectory: true)
        let sourceCore = sourceRuntime.appendingPathComponent("ReelTranscodeCore", isDirectory: true)
        let destCore = AppPaths.runtimeDirectory.appendingPathComponent("ReelTranscodeCore", isDirectory: true)
        let destBin = AppPaths.runtimeDirectory.appendingPathComponent("bin", isDirectory: true)
        let destLib = AppPaths.runtimeDirectory.appendingPathComponent("lib", isDirectory: true)

        if FileManager.default.fileExists(atPath: sourceCore.path) {
            let sourceExecutable = sourceCore.appendingPathComponent("ReelTranscodeCore")
            let destExecutable = destCore.appendingPathComponent("ReelTranscodeCore")
            if shouldReplaceBinary(source: sourceExecutable, destination: destExecutable) {
                try copyReplacing(source: sourceCore, destination: destCore)
            }
        }

        if FileManager.default.fileExists(atPath: sourceBin.path) {
            let shouldRefreshBin = Self.embeddedBinaryNames.contains { binaryName in
                shouldReplaceBinary(
                    source: sourceBin.appendingPathComponent(binaryName),
                    destination: destBin.appendingPathComponent(binaryName)
                )
            }
            if shouldRefreshBin {
                try copyReplacing(source: sourceBin, destination: destBin)
            }
        }

        if FileManager.default.fileExists(atPath: sourceLib.path) {
            try copyReplacing(source: sourceLib, destination: destLib)
        }

        if FileManager.default.fileExists(atPath: destCore.path) {
            try ensureExecutableFlag(for: AppPaths.runtimeDirectory.appendingPathComponent("ReelTranscodeCore/ReelTranscodeCore"))
        }
        if FileManager.default.fileExists(atPath: destBin.path) {
            for binaryName in Self.embeddedBinaryNames {
                try ensureExecutableFlag(for: AppPaths.runtimeDirectory.appendingPathComponent("bin/\(binaryName)"))
            }
        }
    }

    private func copyReplacing(source: URL, destination: URL) throws {
        if FileManager.default.fileExists(atPath: destination.path) {
            try FileManager.default.removeItem(at: destination)
        }
        try FileManager.default.copyItem(at: source, to: destination)
    }

    private func ensureExecutableFlag(for url: URL) throws {
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        var attrs = try FileManager.default.attributesOfItem(atPath: url.path)
        let currentPerms = (attrs[.posixPermissions] as? NSNumber)?.intValue ?? 0o644
        let updatedPerms = currentPerms | 0o111
        attrs[.posixPermissions] = NSNumber(value: updatedPerms)
        try FileManager.default.setAttributes(attrs, ofItemAtPath: url.path)
    }

    func shouldReplaceBinary(source: URL, destination: URL) -> Bool {
        guard FileManager.default.fileExists(atPath: source.path) else { return false }
        guard FileManager.default.fileExists(atPath: destination.path) else { return true }

        do {
            let sourceAttrs = try FileManager.default.attributesOfItem(atPath: source.path)
            let destAttrs = try FileManager.default.attributesOfItem(atPath: destination.path)
            let sourceSize = (sourceAttrs[.size] as? NSNumber)?.int64Value
            let destSize = (destAttrs[.size] as? NSNumber)?.int64Value
            if sourceSize != destSize {
                return true
            }

            if let sourceHash = sha256(of: source), let destHash = sha256(of: destination) {
                if sourceHash != destHash {
                    return true
                }
                return false
            }

            let sourceDate = sourceAttrs[.modificationDate] as? Date
            let destDate = destAttrs[.modificationDate] as? Date
            if let sourceDate, let destDate {
                return sourceDate > destDate
            }
        } catch {
            return true
        }
        return false
    }

    private func sha256(of fileURL: URL) -> String? {
        guard let data = try? Data(contentsOf: fileURL, options: [.mappedIfSafe]) else {
            return nil
        }
        let digest = SHA256.hash(data: data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}
