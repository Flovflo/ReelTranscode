import SwiftUI

struct OnboardingView: View {
    @EnvironmentObject private var model: AppViewModel
    @State private var watchFolder = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Setup ReelTranscode")
                .font(.largeTitle.weight(.semibold))

            Text("Configure watch folders, output paths, and runtime profile for your first launch.")
                .foregroundStyle(.secondary)

            GroupBox("Watch Folders") {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(model.config.watchFolders, id: \.self) { folder in
                        HStack {
                            Text(folder)
                                .lineLimit(1)
                            Spacer()
                            Button("Remove") {
                                model.config.watchFolders.removeAll { $0 == folder }
                            }
                        }
                    }

                    HStack {
                        TextField("/Volumes/Media/Movies", text: $watchFolder)
                        Button("Add") {
                            addWatchFolder(path: watchFolder)
                        }
                        Button("Browse") {
                            if let picked = model.pickFolder() {
                                addWatchFolder(path: picked)
                            }
                        }
                    }
                }
                .padding(.top, 4)
            }

            GroupBox("Output") {
                VStack(spacing: 8) {
                    HStack {
                        Text("Optimized")
                            .frame(width: 100, alignment: .leading)
                        TextField("Output root", text: $model.config.outputRoot)
                        Button("Browse") {
                            if let picked = model.pickFolder() {
                                model.config.outputRoot = picked
                            }
                        }
                    }

                    HStack {
                        Text("Archive")
                            .frame(width: 100, alignment: .leading)
                        TextField("Archive root", text: $model.config.archiveRoot)
                        Button("Browse") {
                            if let picked = model.pickFolder() {
                                model.config.archiveRoot = picked
                            }
                        }
                    }
                }
                .padding(.top, 4)
            }

            Picker("Performance", selection: $model.config.profile) {
                ForEach(PerformanceProfile.allCases) { profile in
                    Text(profile.rawValue).tag(profile)
                }
            }
            .pickerStyle(.segmented)

            HStack {
                Spacer()
                Button("Initialize App") {
                    Task { await model.completeOnboarding() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.config.watchFolders.isEmpty)
            }
        }
        .padding(28)
        .frame(minWidth: 860, minHeight: 620)
    }

    private func addWatchFolder(path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if !model.config.watchFolders.contains(trimmed) {
            model.config.watchFolders.append(trimmed)
        }
        watchFolder = ""
    }
}
