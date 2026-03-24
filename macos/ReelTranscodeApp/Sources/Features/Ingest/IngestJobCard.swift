import SwiftUI

struct IngestJobCard: View {
    let row: JobRow

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle()
                    .fill(row.statusColor)
                    .frame(width: 9, height: 9)
                Text(row.fileName)
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                Text(row.statusLabel)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(row.statusColor)
            }

            Text(row.sourceDirectory)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)

            if let targetPath = row.targetPath {
                Text(targetPath)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            if let errorMessage = row.errorMessage, !errorMessage.isEmpty {
                Text(errorMessage)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12))
    }
}
