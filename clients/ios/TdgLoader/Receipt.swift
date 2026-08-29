import Foundation

/// What this app added, and what Photos called it.
///
/// Keyed on the `PHAsset` **localIdentifier**, never the filename: Photos
/// renames every imported asset to `IMG_NNNN`, so a filename-keyed wipe would
/// find nothing. The identifier is also exactly what `deleteAssets` needs.
///
/// It makes a fill resumable after the app is killed, and it makes wipe exact —
/// the app deletes the identifiers it recorded and nothing else in the library
/// is touched.
final class Receipt {
    let jobId: String
    private let url: URL
    private var byName: [String: String] = [:]      // file name -> localIdentifier
    private let queue = DispatchQueue(label: "tdg.receipt")

    init(jobId: String) {
        self.jobId = jobId
        let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                           in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        self.url = dir.appendingPathComponent("receipt-\(jobId).json")
        load()
    }

    var count: Int { queue.sync { byName.count } }
    var identifiers: [String] { queue.sync { Array(byName.values) } }

    func has(_ name: String) -> Bool { queue.sync { byName[name] != nil } }

    func add(_ pairs: [(String, String)]) {
        queue.sync {
            for (name, id) in pairs { byName[name] = id }
            save()
        }
    }

    func clear() {
        queue.sync {
            byName.removeAll()
            try? FileManager.default.removeItem(at: url)
        }
    }

    private func load() {
        guard let data = try? Data(contentsOf: url),
              let o = try? JSONSerialization.jsonObject(with: data) as? [String: String]
        else { return }
        byName = o
    }

    /// Written to a sibling then moved, so a crash mid-write cannot leave a
    /// half-parsed receipt that would strand assets in the library.
    private func save() {
        guard let data = try? JSONSerialization.data(withJSONObject: byName) else { return }
        let tmp = url.deletingLastPathComponent()
            .appendingPathComponent("receipt-\(jobId).tmp")
        do {
            try data.write(to: tmp, options: .atomic)
            _ = try FileManager.default.replaceItemAt(url, withItemAt: tmp)
        } catch {
            try? data.write(to: url, options: .atomic)
        }
    }

    static func knownJobs() -> [String] {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                           in: .userDomainMask)[0]
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return names.compactMap { n in
            guard n.hasPrefix("receipt-"), n.hasSuffix(".json") else { return nil }
            return String(n.dropFirst("receipt-".count).dropLast(".json".count))
        }.sorted()
    }
}
