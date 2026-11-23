import Foundation

// The vault's data, as the engine sends it (backend/api/app.py; the web app's frontend/lib/api.ts has the same shapes).

public enum Category: String, Codable, CaseIterable, Sendable {
    case insurance, warranty, bill, property, identity, other

    public var label: String {
        switch self {
        case .insurance: "Insurance"
        case .warranty: "Warranties"
        case .bill: "Bills"
        case .property: "Property"
        case .identity: "Identity"
        case .other: "Other"
        }
    }
}

public struct User: Codable, Equatable, Sendable {
    public let id: Int
    public let name: String
    public let isAdmin: Bool
}

public struct AuthStatus: Codable, Sendable {
    public let user: User?
    public let setupNeeded: Bool
    public let suggestedName: String?
}

public struct LlmStatus: Codable, Sendable {
    public let ready: Bool
    public let message: String
    public let canDownload: Bool?  // the local AI isn't downloaded yet: offer the one-click download
}

/// How far the one-click download of the local AI is.
public struct AIDownloadStatus: Codable, Sendable {
    public let state: String  // idle, downloading, done, failed
    public let step: String?  // "llama.cpp" or "model"
    public let done: Int
    public let total: Int
    public let error: String?
}

/// 1.0: several computers keep one vault. A code for another computer to join this vault.
public struct SyncInvite: Codable, Sendable {
    public let code: String
    public let expiresIn: Int
    public let urls: [String]
    public let fingerprint: String
    public let name: String
}

/// Another computer that keeps this vault: when it was last caught up with, or why not.
public struct SyncPeer: Codable, Sendable, Identifiable {
    public let centreId: String
    public let name: String
    public let urls: [String]
    public let fingerprint: String
    public let syncedAt: String?
    public let problem: String?
    public var id: String { centreId }
}

/// Copying a joined vault: copying (done of total documents), done or failed.
public struct SyncJoining: Codable, Sendable {
    public let state: String
    public let done: Int
    public let total: Int
    public let error: String?
    public let with: String?

    public init(state: String, done: Int, total: Int, error: String?, with: String?) {
        self.state = state
        self.done = done
        self.total = total
        self.error = error
        self.with = with
    }
}

public struct SyncStatus: Codable, Sendable {
    public let centreId: String
    public let name: String
    public let peers: [SyncPeer]
    public let joining: SyncJoining?
    public let canJoin: Bool
}

public struct SyncProbe: Codable, Sendable {
    public let fingerprint: String
    public let address: String
}

/// The search model: downloading (done of total bytes, the first start only), loading, ready or failed.
public struct SearchStatus: Codable, Sendable {
    public let state: String
    public let done: Int
    public let total: Int
    public let error: String?
}

public struct Info: Codable, Sendable {
    public let owner: String
    public let isAdmin: Bool
    public let llm: String?
    public let llmStatus: LlmStatus
    public let numDocuments: Int
    public let numChunks: Int
    public let categories: [String: Int]
    public let ocrAvailable: Bool
    public let supportedTypes: [String]
    public let maxUploadMb: Int
}

public struct Person: Codable, Hashable, Sendable {
    public let id: Int
    public let name: String
}

public struct VaultDocument: Codable, Identifiable, Hashable, Sendable {
    public var id: String { docId }
    public let docId: String
    public let filename: String
    public let category: Category
    public let owner: String
    public let mine: Bool
    public let `private`: Bool
    public let sharedWith: [Person]
    public let pages: Int
    public let ocrPages: Int
    public let ocrConfidence: Int?
    public let needsReview: Bool
    public let expiresOn: String?
    public let dueOn: String?
    public let amount: String?
    public let reference: String?
    public let issuer: String?
    public let sizeBytes: Int
    public let addedAt: String

    private enum CodingKeys: String, CodingKey {
        case docId, filename, category, owner, mine, `private`, sharedWith, pages, ocrPages, ocrConfidence, needsReview
        case expiresOn, dueOn, amount, reference, issuer, sizeBytes, addedAt
    }

    // Some answers (an upload's) leave out who owns and sees the document: it is yours, and private to you.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        docId = try c.decode(String.self, forKey: .docId)
        filename = try c.decode(String.self, forKey: .filename)
        category = (try? c.decode(Category.self, forKey: .category)) ?? .other
        owner = try c.decodeIfPresent(String.self, forKey: .owner) ?? ""
        mine = try c.decodeIfPresent(Bool.self, forKey: .mine) ?? true
        `private` = try c.decodeIfPresent(Bool.self, forKey: .private) ?? false
        sharedWith = try c.decodeIfPresent([Person].self, forKey: .sharedWith) ?? []
        pages = try c.decodeIfPresent(Int.self, forKey: .pages) ?? 0
        ocrPages = try c.decodeIfPresent(Int.self, forKey: .ocrPages) ?? 0
        ocrConfidence = try c.decodeIfPresent(Int.self, forKey: .ocrConfidence)
        needsReview = try c.decodeIfPresent(Bool.self, forKey: .needsReview) ?? false
        expiresOn = try c.decodeIfPresent(String.self, forKey: .expiresOn)
        dueOn = try c.decodeIfPresent(String.self, forKey: .dueOn)
        amount = try c.decodeIfPresent(String.self, forKey: .amount)
        reference = try c.decodeIfPresent(String.self, forKey: .reference)
        issuer = try c.decodeIfPresent(String.self, forKey: .issuer)
        sizeBytes = try c.decodeIfPresent(Int.self, forKey: .sizeBytes) ?? 0
        addedAt = try c.decodeIfPresent(String.self, forKey: .addedAt) ?? ""
    }
}

public struct Upcoming: Codable, Identifiable, Hashable, Sendable {
    public var id: String { "\(docId)-\(kind)" }
    public let docId: String
    public let filename: String
    public let category: Category
    public let kind: String  // "expires" or "due"
    public let date: String
    public let daysLeft: Int
    public let amount: String?
    public let issuer: String?
}

public struct Source: Codable, Identifiable, Hashable, Sendable {
    public var id: String { chunkId }
    public let rank: Int
    public let score: Double
    public let chunkId: String
    public let docId: String
    public let filename: String
    public let category: Category
    public let page: Int
    public let text: String
}

public struct UploadResult: Codable, Sendable {
    public let filename: String
    public let status: String  // "added", "duplicate" or "error"
    public let document: VaultDocument?
    public let error: String?
    public let code: String?  // "password_required" / "wrong_password": a locked PDF

    public init(filename: String, status: String, document: VaultDocument? = nil, error: String? = nil, code: String? = nil) {
        self.filename = filename; self.status = status; self.document = document; self.error = error; self.code = code
    }
}

/// What the vault takes: PDFs, Word (.docx) files, photos and plain text. Archives (zip, 7z, rar…) and other files are
/// turned away before they're sent; a file with no extension (as chat apps send them) is sent, and the vault checks it.
public enum AcceptedFiles {
    public static let extensions: Set<String> = ["pdf", "docx", "txt", "md", "png", "jpg", "jpeg", "webp", "tif", "tiff",
                                                 "heic", "heif"]
    public static let archives: Set<String> = ["zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "zst", "cab", "iso", "dmg"]

    public static func accepts(_ url: URL) -> Bool {
        let ext = url.pathExtension.lowercased()
        return ext.isEmpty || extensions.contains(ext)
    }

    /// Why a file isn't taken, in a sentence.
    public static func reason(_ url: URL) -> String {
        if archives.contains(url.pathExtension.lowercased()) {
            return "Zip and other archive files can't be added. Unzip it first, then add the PDFs, Word files or photos inside."
        }
        if url.pathExtension.lowercased() == "doc" {
            return "This is an old Word (.doc) file. Open it and save it as PDF or .docx, then add that."
        }
        return "Only PDFs, Word (.docx) files, photos and text files can be added. Save or print it as PDF, then add that."
    }

    /// Files to send (a folder's documents, inside it too) and the ones turned away, each with why.
    public static func sort(_ urls: [URL]) -> (send: [URL], refused: [UploadResult]) {
        var send: [URL] = [], refused: [UploadResult] = []
        for url in urls {
            var isFolder: ObjCBool = false
            if FileManager.default.fileExists(atPath: url.path, isDirectory: &isFolder), isFolder.boolValue {
                let inside = FileManager.default.enumerator(at: url, includingPropertiesForKeys: [.isRegularFileKey],
                                                            options: [.skipsHiddenFiles])
                while let file = inside?.nextObject() as? URL {
                    if (try? file.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true,
                       extensions.contains(file.pathExtension.lowercased()) { send.append(file) }
                }
            } else if accepts(url) {
                send.append(url)
            } else {
                refused.append(UploadResult(filename: url.lastPathComponent, status: "error", error: reason(url)))
            }
        }
        return (send, refused)
    }
}

public struct ChatTurn: Codable, Sendable {
    public let role: String  // "user" or "assistant"
    public let content: String
    public init(role: String, content: String) { self.role = role; self.content = content }
}

/// One line of a streamed answer: the sources first, then the answer word by word, then which sources it cited.
public enum ChatEvent: Sendable, Equatable {
    case sources([Source])
    case token(String)
    case done(citations: [Int], found: Bool, llm: String)
    case error(message: String, detail: String?)
}

extension ChatEvent: Decodable {
    private enum Keys: String, CodingKey { case type, sources, text, citations, found, llm, message, detail }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        switch try c.decode(String.self, forKey: .type) {
        case "sources": self = .sources(try c.decode([Source].self, forKey: .sources))
        case "token": self = .token(try c.decode(String.self, forKey: .text))
        case "done": self = .done(citations: try c.decode([Int].self, forKey: .citations),
                                  found: try c.decode(Bool.self, forKey: .found),
                                  llm: try c.decodeIfPresent(String.self, forKey: .llm) ?? "")
        default: self = .error(message: try c.decodeIfPresent(String.self, forKey: .message) ?? "Something went wrong.",
                               detail: try c.decodeIfPresent(String.self, forKey: .detail))
        }
    }
}

public struct Settings: Codable, Sendable {
    public let owner: String
    public let isAdmin: Bool
    public let llmModel: String?
    public let llmModels: [String]
    public let llmStatus: LlmStatus
    public let embeddingModel: String
    public let database: String
    public let indexDir: String
    public let ocrAvailable: Bool
    public let searchMethod: String
    public let factsReader: String
    public let keyKeptIn: String
    public let familyMode: Bool
}

public struct Reminder: Codable, Identifiable, Hashable, Sendable {
    public var id: String { "\(docId)-\(kind)" }
    public let docId: String
    public let filename: String
    public let category: Category
    public let kind: String
    public let date: String
    public let daysLeft: Int
    public let done: Bool
    public let status: String  // overdue, today, soon, upcoming, done
    public let title: String
    public let body: String
}

/// What the centre computer shows to connect another device: a one-time code, its addresses, its certificate.
public struct Pairing: Codable, Sendable {
    public let code: String
    public let expiresIn: Int
    public let url: String
    public let urls: [String]
    public let fingerprint: String
}

/// The vault's JSON uses snake_case; Swift uses camelCase.
public enum VaultJSON {
    public static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    public static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()
}
