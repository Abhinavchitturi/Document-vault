import Foundation
import VaultKit

/// Dates, sizes and days left, the way the Mac shows them.
enum Format {
    private static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// "2027-03-14" or an ISO timestamp → "14 Mar 2027" (in the Mac's own date style).
    static func date(_ value: String) -> String {
        let day = String(value.prefix(10))
        guard let date = isoDay.date(from: day) else { return value }
        return date.formatted(date: .abbreviated, time: .omitted)
    }

    static func size(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }

    static func daysLeft(_ days: Int) -> String {
        switch days {
        case ..<(-1): "\(-days) days ago"
        case -1: "Yesterday"
        case 0: "Today"
        case 1: "Tomorrow"
        default: "In \(days) days"
        }
    }

    /// The fact worth showing under a document's name: when it expires or falls due.
    static func keyFact(_ document: VaultDocument) -> (text: String, urgent: Bool)? {
        let today = isoDay.string(from: Date())
        if let due = document.dueOn { return ("Due \(date(due))", due >= today && due <= soon) }
        if let expires = document.expiresOn { return ("Expires \(date(expires))", expires >= today && expires <= soon) }
        return nil
    }

    private static var soon: String {
        isoDay.string(from: Calendar.current.date(byAdding: .day, value: 30, to: Date()) ?? Date())
    }
}
