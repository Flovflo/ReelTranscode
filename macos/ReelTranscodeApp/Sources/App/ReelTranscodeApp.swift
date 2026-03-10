import SwiftUI

@main
struct ReelTranscodeMacApp: App {
    @StateObject private var model = AppViewModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .task {
                    await model.bootstrap()
                }
        }
        .windowResizability(.contentSize)
        .commands {
            CommandMenu("ReelTranscode") {
                Button("Refresh Status") {
                    Task { await model.refreshStatus() }
                }
                .keyboardShortcut("r", modifiers: [.command])

                Button("Save Config") {
                    Task {
                        await model.saveConfig()
                        await model.validateConfig()
                    }
                }
                .keyboardShortcut("s", modifiers: [.command])
            }
        }
    }
}
