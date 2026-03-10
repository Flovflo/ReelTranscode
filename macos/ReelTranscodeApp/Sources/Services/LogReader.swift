import Foundation

final class LogReader {
    func combinedWatchLogs(maxBytes: Int = 200_000) -> String {
        let stdout = readLastBytes(from: AppPaths.watchStdoutURL, maxBytes: maxBytes)
        let stderr = readLastBytes(from: AppPaths.watchStderrURL, maxBytes: maxBytes)

        return """
        ===== STDOUT =====
        \(stdout)

        ===== STDERR =====
        \(stderr)
        """
    }

    private func readLastBytes(from url: URL, maxBytes: Int) -> String {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return "(no log file)" }
        defer { try? handle.close() }

        let fileSize = (try? handle.seekToEnd()) ?? 0
        let offset = fileSize > UInt64(maxBytes) ? fileSize - UInt64(maxBytes) : 0
        try? handle.seek(toOffset: offset)
        let data = try? handle.readToEnd()
        return String(decoding: data ?? Data(), as: UTF8.self)
    }
}
