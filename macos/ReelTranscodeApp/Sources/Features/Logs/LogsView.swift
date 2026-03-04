import SwiftUI

struct LogsView: View {
    @EnvironmentObject private var model: AppViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Logs")
                    .font(.largeTitle.weight(.semibold))
                Spacer()
                Button("Reload") {
                    model.refreshLogs()
                }
            }

            TextEditor(text: $model.logsText)
                .font(.system(.body, design: .monospaced))
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .border(.separator)
        }
        .padding(20)
        .onAppear {
            model.refreshLogs()
        }
    }
}
