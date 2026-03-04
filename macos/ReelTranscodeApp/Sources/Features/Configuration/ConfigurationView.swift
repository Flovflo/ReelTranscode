import SwiftUI

struct ConfigurationView: View {
    @EnvironmentObject private var model: AppViewModel
    @State private var newWatchFolder = ""

    var body: some View {
        Form {
            Section("Watch Folders") {
                ForEach(model.config.watchFolders, id: \.self) { folder in
                    HStack {
                        Text(folder)
                        Spacer()
                        Button("Remove") {
                            model.config.watchFolders.removeAll { $0 == folder }
                        }
                    }
                }

                HStack {
                    TextField("/Volumes/Media/Movies", text: $newWatchFolder)
                    Button("Add") { addWatchFolder(newWatchFolder) }
                    Button("Browse") {
                        if let picked = model.pickFolder() {
                            addWatchFolder(picked)
                        }
                    }
                }
            }

            Section("Output") {
                HStack {
                    Text("Optimized")
                        .frame(width: 110, alignment: .leading)
                    TextField("Output root", text: $model.config.outputRoot)
                    Button("Browse") {
                        if let picked = model.pickFolder() {
                            model.config.outputRoot = picked
                        }
                    }
                }
                HStack {
                    Text("Archive")
                        .frame(width: 110, alignment: .leading)
                    TextField("Archive root", text: $model.config.archiveRoot)
                    Button("Browse") {
                        if let picked = model.pickFolder() {
                            model.config.archiveRoot = picked
                        }
                    }
                }
            }

            Section("Performance") {
                Picker("Profile", selection: $model.config.profile) {
                    ForEach(PerformanceProfile.allCases) { profile in
                        Text(profile.rawValue).tag(profile)
                    }
                }
                .pickerStyle(.segmented)
            }

            Section("Actions") {
                HStack {
                    Button("Save Config") {
                        Task {
                            await model.saveConfig()
                            await model.validateConfig()
                        }
                    }
                    .buttonStyle(.borderedProminent)

                    Button("Validate Only") {
                        Task { await model.validateConfig() }
                    }
                }

                if !model.configValidationErrors.isEmpty {
                    ForEach(model.configValidationErrors) { err in
                        Text("\(err.field): \(err.message)")
                            .font(.system(.caption, design: .monospaced))
                    }
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Configuration")
        .padding(20)
    }

    private func addWatchFolder(_ path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if !model.config.watchFolders.contains(trimmed) {
            model.config.watchFolders.append(trimmed)
        }
        newWatchFolder = ""
    }
}
