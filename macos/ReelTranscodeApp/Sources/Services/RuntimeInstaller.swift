import Foundation

final class RuntimeInstaller {
    func prepareDirectories() throws {
        try FileManager.default.createDirectory(at: AppPaths.appSupportDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.configDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.logsDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: AppPaths.runtimeDirectory, withIntermediateDirectories: true)
    }

    func installEmbeddedRuntimeIfAvailable() throws {
        try prepareDirectories()

        guard let resourceRoot = Bundle.main.resourceURL else { return }
        let sourceRuntime = resourceRoot.appendingPathComponent("runtime", isDirectory: true)
        let sourceBin = resourceRoot.appendingPathComponent("bin", isDirectory: true)
        let sourceCore = sourceRuntime.appendingPathComponent("ReelTranscodeCore", isDirectory: true)
        let destCore = AppPaths.runtimeDirectory.appendingPathComponent("ReelTranscodeCore", isDirectory: true)
        let destBin = AppPaths.runtimeDirectory.appendingPathComponent("bin", isDirectory: true)

        if FileManager.default.fileExists(atPath: sourceCore.path) {
            let sourceExecutable = sourceCore.appendingPathComponent("ReelTranscodeCore")
            let destExecutable = destCore.appendingPathComponent("ReelTranscodeCore")
            if shouldReplaceBinary(source: sourceExecutable, destination: destExecutable) {
                try copyReplacing(source: sourceCore, destination: destCore)
            }
        }

        if FileManager.default.fileExists(atPath: sourceBin.path) {
            let sourceFFmpeg = sourceBin.appendingPathComponent("ffmpeg")
            let sourceFFprobe = sourceBin.appendingPathComponent("ffprobe")
            let destFFmpeg = destBin.appendingPathComponent("ffmpeg")
            let destFFprobe = destBin.appendingPathComponent("ffprobe")
            if shouldReplaceBinary(source: sourceFFmpeg, destination: destFFmpeg)
                || shouldReplaceBinary(source: sourceFFprobe, destination: destFFprobe)
            {
                try copyReplacing(source: sourceBin, destination: destBin)
            }
        }

        if FileManager.default.fileExists(atPath: destCore.path) {
            try ensureExecutableFlag(for: AppPaths.runtimeDirectory.appendingPathComponent("ReelTranscodeCore/ReelTranscodeCore"))
        }
        if FileManager.default.fileExists(atPath: destBin.path) {
            try ensureExecutableFlag(for: AppPaths.runtimeDirectory.appendingPathComponent("bin/ffmpeg"))
            try ensureExecutableFlag(for: AppPaths.runtimeDirectory.appendingPathComponent("bin/ffprobe"))
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

    private func shouldReplaceBinary(source: URL, destination: URL) -> Bool {
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
}
