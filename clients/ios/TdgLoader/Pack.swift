import Foundation

/// The manifest is the contract with the generator. The app parses it and
/// nothing else: every item already carries the URL to fetch it from, so the
/// loader never needs to know how the server lays out paths.
struct PackItem: Identifiable {
    let name: String
    let kind: String            // "image" | "video"
    let bytes: Int64
    let sha256: String
    let takenAt: Date
    let url: URL
    let latitude: Double?
    let longitude: Double?

    var id: String { name }
    var isVideo: Bool { kind == "video" }
}

struct Pack {
    let jobId: String
    let album: String
    let fileCount: Int
    let totalBytes: Int64
    let items: [PackItem]

    /// Schema v2 writes `taken_at_utc` with an explicit offset. v1 wrote a
    /// naive local time, which is the bug that made a pack land at a different
    /// instant on every machine that imported it — parse it, but only as a
    /// fallback, and treat it as local time the way Photos itself would.
    private static func parseDate(_ s: String) -> Date {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        if let d = iso.date(from: s) { return d }
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: s) { return d }
        let naive = DateFormatter()
        naive.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        naive.timeZone = TimeZone.current
        return naive.date(from: s) ?? Date()
    }

    static func parse(_ data: Data, base: URL) throws -> Pack {
        guard let o = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rawItems = o["items"] as? [[String: Any]] else {
            throw LoaderError.badManifest
        }
        let jobId = o["job_id"] as? String ?? "unknown"
        var items: [PackItem] = []
        items.reserveCapacity(rawItems.count)
        for it in rawItems {
            guard let name = it["name"] as? String,
                  let rel = it["url"] as? String,
                  let url = URL(string: rel, relativeTo: base)?.absoluteURL else { continue }
            let gps = it["gps"] as? [String: Any]
            items.append(PackItem(
                name: name,
                kind: it["kind"] as? String ?? "image",
                bytes: (it["bytes"] as? NSNumber)?.int64Value ?? 0,
                sha256: it["sha256"] as? String ?? "",
                takenAt: parseDate((it["taken_at_utc"] as? String)
                                   ?? (it["taken_at"] as? String) ?? ""),
                url: url,
                latitude: gps?["lat"] as? Double,
                longitude: gps?["lon"] as? Double))
        }
        return Pack(
            jobId: jobId,
            album: o["album"] as? String ?? "TDG \(jobId)",
            fileCount: o["file_count"] as? Int ?? items.count,
            totalBytes: (o["total_bytes"] as? NSNumber)?.int64Value
                        ?? items.reduce(0) { $0 + $1.bytes },
            items: items)
    }
}

/// What the six-digit pairing code resolves to.
struct Pairing {
    let jobId: String
    let label: String
    let fileCount: Int
    let totalBytes: Int64
    let manifestURL: URL

    static func parse(_ data: Data, base: URL) throws -> Pairing {
        guard let o = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rel = o["manifest_url"] as? String,
              let url = URL(string: rel, relativeTo: base)?.absoluteURL else {
            throw LoaderError.badPairing
        }
        return Pairing(
            jobId: o["job_id"] as? String ?? "?",
            label: o["label"] as? String ?? "",
            fileCount: o["file_count"] as? Int ?? 0,
            totalBytes: (o["total_bytes"] as? NSNumber)?.int64Value ?? 0,
            manifestURL: url)
    }
}

enum LoaderError: LocalizedError {
    case badManifest
    case badPairing
    case notAuthorised
    case notAuthorisedFull
    case http(Int, String)
    case shortRead(String, Int64, Int64)
    case noSpace(Int64, Int64)

    var errorDescription: String? {
        switch self {
        case .badManifest: return "That manifest is not in a shape this app understands."
        case .badPairing: return "No finished pack has that code."
        case .notAuthorised:
            return "Permission to add photos was declined."
        case .notAuthorisedFull:
            return "Removing assets needs Full Access to the photo library. "
                 + "Adding them only needs add-only permission, so iOS asks "
                 + "again here — allow it, or delete the TDG album by hand."
        case .http(let code, let url): return "HTTP \(code) from \(url)"
        case .shortRead(let name, let want, let got):
            return "\(name): expected \(want) bytes, got \(got)"
        case .noSpace(let need, let free):
            return "Not enough space: needs \(ByteFormat.human(need)), "
                 + "\(ByteFormat.human(free)) free"
        }
    }
}

enum ByteFormat {
    static func human(_ n: Int64) -> String {
        if n < 0 { return "unknown" }
        let units = ["B", "KB", "MB", "GB", "TB"]
        var v = Double(n), i = 0
        while v >= 1024 && i < units.count - 1 { v /= 1024; i += 1 }
        return i == 0 ? "\(n) B" : String(format: "%.1f %@", v, units[i])
    }
}
