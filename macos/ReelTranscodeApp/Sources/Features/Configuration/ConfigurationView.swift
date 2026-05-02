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
                            model.config.tempDirOverrides.removeValue(forKey: folder)
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
                Picker("Handling", selection: $model.config.outputBehavior) {
                    ForEach(OutputBehavior.allCases) { behavior in
                        Text(behavior.title).tag(behavior)
                    }
                }
                Text(model.config.outputBehavior.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if model.config.outputBehavior.usesSeparateOutputRoot {
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
                }
                if model.config.outputBehavior.usesArchiveRoot {
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
            }

            Section("Temporary Workspace") {
                Picker("Workspace Mode", selection: $model.config.tempWorkspaceStrategy) {
                    ForEach(TempWorkspaceStrategy.allCases) { strategy in
                        Text(strategy.title).tag(strategy)
                    }
                }
                .pickerStyle(.segmented)
                Text(model.config.tempWorkspaceStrategy.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    Text(model.config.tempWorkspaceStrategy == .configuredFirst ? "Scratch root" : "Fallback dir")
                        .frame(width: 110, alignment: .leading)
                    TextField("Temporary workspace root", text: $model.config.tempDir)
                    Button("Browse") {
                        if let picked = model.pickFolder() {
                            model.config.tempDir = picked
                        }
                    }
                }
                if !model.config.watchFolders.isEmpty {
                    Text("Optional overrides let each watch folder use its own SSD scratch root while keeping one shared default for everyone else.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    ForEach(model.config.watchFolders, id: \.self) { folder in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(folder)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            HStack {
                                Text("Override")
                                    .frame(width: 110, alignment: .leading)
                                TextField("Use shared scratch root", text: tempOverrideBinding(for: folder))
                                Button("Browse") {
                                    if let picked = model.pickFolder() {
                                        model.config.tempDirOverrides[folder] = picked
                                    }
                                }
                                if model.config.tempDirOverrides[folder]?.isEmpty == false {
                                    Button("Clear") {
                                        model.config.tempDirOverrides.removeValue(forKey: folder)
                                    }
                                }
                            }
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

                Stepper(value: $model.config.maxWorkers, in: 1...8) {
                    LabeledContent("Concurrent Jobs", value: "\(model.config.maxWorkers)")
                }

                Text("Use fewer workers for fragile NAS volumes, more workers for big ingest queues on fast storage.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Actions") {
                Button("Save") {
                    Task {
                        await model.saveConfig()
                        await model.validateConfig()
                    }
                }
                .buttonStyle(.borderedProminent)

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
        .onChange(of: model.config.profile) { _, newProfile in
            model.config.apply(newProfile)
        }
    }

    private func addWatchFolder(_ path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if !model.config.watchFolders.contains(trimmed) {
            model.config.watchFolders.append(trimmed)
        }
        newWatchFolder = ""
    }

    private func tempOverrideBinding(for folder: String) -> Binding<String> {
        Binding(
            get: { model.config.tempDirOverrides[folder] ?? "" },
            set: { newValue in
                let trimmed = newValue.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed.isEmpty {
                    model.config.tempDirOverrides.removeValue(forKey: folder)
                } else {
                    model.config.tempDirOverrides[folder] = trimmed
                }
            }
        )
    }
}
