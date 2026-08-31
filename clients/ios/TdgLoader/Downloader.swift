import Foundation

/// HTTP, using only what the platform ships.
///
/// Downloads land in a temp file rather than in memory: a 4 GB clip must not
/// become 4 GB of resident memory on a phone, and `PHAssetCreationRequest`
/// wants a file URL anyway.
struct Downloader {

    private static let session: URLSession = {
        let c = URLSessionConfiguration.default
        c.timeoutIntervalForRequest = 30
        c.timeoutIntervalForResource = 3600      // a big clip on poor Wi-Fi
        c.waitsForConnectivity = true
        return URLSession(configuration: c)
    }()

    static func data(from url: URL) async throws -> Data {
        let (data, response) = try await session.data(from: url)
        try check(response, url, data)
        return data
    }

    /// Download to a temp file and return its URL. The caller owns the file and
    /// hands it to Photos with `shouldMoveFile`, so it needs no cleanup on the
    /// happy path.
    static func download(_ item: PackItem, into dir: URL) async throws -> URL {
        let (tmp, response) = try await session.download(from: item.url)
        // A failed download writes the error body to the temp file, so read it
        // back rather than reporting a bare status for a pack that was pruned
        // out from under a running fill.
        try check(response, item.url, try? Data(contentsOf: tmp))
        let dest = dir.appendingPathComponent(item.name)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.moveItem(at: tmp, to: dest)

        let size = (try? FileManager.default.attributesOfItem(atPath: dest.path)[.size]
                    as? NSNumber)??.int64Value ?? 0
        guard size == item.bytes else {
            try? FileManager.default.removeItem(at: dest)
            throw LoaderError.shortRead(item.name, item.bytes, size)
        }
        return dest
    }

    private static func check(_ response: URLResponse, _ url: URL,
                              _ body: Data?) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200...299).contains(http.statusCode) else {
            throw LoaderError.from(status: http.statusCode,
                                   url: url.absoluteString, body: body)
        }
    }
}
