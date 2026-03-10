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
                Toggle("Replace original files in place (advanced)", isOn: $model.config.replaceOriginalsInPlace)
                Text("Recommended: keep originals and publish one validated Apple-native MP4 in the optimized output folder.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if !model.config.replaceOriginalsInPlace {
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
            }

            Section("Temporary Workspace") {
                Text("Use a volume with plenty of free space for DV-safe remux and OCR temporary files.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    Text("Temp dir")
                        .frame(width: 110, alignment: .leading)
                    TextField("Temporary workspace", text: $model.config.tempDir)
                    Button("Browse") {
                        if let picked = model.pickFolder() {
                            model.config.tempDir = picked
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
