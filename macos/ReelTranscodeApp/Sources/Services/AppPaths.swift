import Foundation

enum AppPaths {
    static let appName = "ReelTranscode"
    static let launchAgentLabel = "com.reelfin.reeltranscode.watch"

    static var appSupportDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return base.appendingPathComponent(appName, isDirectory: true)
    }

    static var configDirectory: URL {
        appSupportDirectory.appendingPathComponent("config", isDirectory: true)
    }

    static var logsDirectory: URL {
        appSupportDirectory.appendingPathComponent("logs", isDirectory: true)
    }

    static var runtimeDirectory: URL {
        appSupportDirectory.appendingPathComponent("runtime", isDirectory: true)
    }

    static var runtimeBinDirectory: URL {
        runtimeDirectory.appendingPathComponent("bin", isDirectory: true)
    }

    static var runtimeLibDirectory: URL {
        runtimeDirectory.appendingPathComponent("lib", isDirectory: true)
    }

    static var configFileURL: URL {
        configDirectory.appendingPathComponent("reeltranscode.yaml")
    }

    static var launchAgentsDirectory: URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent("Library/LaunchAgents", isDirectory: true)
    }

    static var launchAgentPlistURL: URL {
        launchAgentsDirectory.appendingPathComponent("\(launchAgentLabel).plist")
    }

    static var watchStdoutURL: URL {
        logsDirectory.appendingPathComponent("watch.stdout.log")
    }

    static var watchStderrURL: URL {
        logsDirectory.appendingPathComponent("watch.stderr.log")
    }
}
