import Foundation
import SwiftUI

@MainActor
final class LoaderModel: ObservableObject {

    @Published var host: String = UserDefaults.standard.string(forKey: "host") ?? ""
    @Published var code: String = ""
    @Published var status: String = ""
    @Published var detail: String = ""
    @Published var progress: Double = 0
    @Published var busy = false
    @Published var canLoad = false
    @Published var loadedJobs: [(job: String, count: Int)] = []

    private var pairing: Pairing?
    private var task: Task<Void, Never>?
    private let writer = PhotoWriter()

    /// Launch arguments of the form `-host X -code Y -autostart 1` land in
    /// UserDefaults' argument domain for free, which gives an unattended run
    /// without a second code path through the app:
    ///
    ///     xcrun simctl launch <udid> com.tdg.loader \
    ///         -host http://127.0.0.1:8722 -code 123456 -autostart 1
    ///
    /// Useful in CI, and the only way to drive the app on a machine where the
    /// simulator cannot be tapped.
    init() {
        refreshReceipts()
        if let c = UserDefaults.standard.string(forKey: "code") { code = c }
        if UserDefaults.standard.bool(forKey: "autostart") { autoRun() }
        // `-autowipe 1` is the same affordance for the other direction, and
        // deletes only the identifiers this app's own receipts record.
        if UserDefaults.standard.bool(forKey: "autowipe") { wipe() }
    }

    func autoRun() {
        task = Task {
            await pairAsync()
            if canLoad { await loadAsync() }
        }
    }

    func refreshReceipts() {
        loadedJobs = Receipt.knownJobs().map { ($0, Receipt(jobId: $0).count) }
            .filter { $0.1 > 0 }
    }

    var hasLoaded: Bool { !loadedJobs.isEmpty }

    private var baseURL: URL? {
        var s = host.trimmingCharacters(in: .whitespaces)
        while s.hasSuffix("/") { s.removeLast() }
        if !s.hasPrefix("http") { s = "http://" + s }
        return URL(string: s)
    }

    // MARK: pairing

    func pair() { task = Task { await pairAsync() } }

    private func pairAsync() async {
        guard let base = baseURL, code.count == 6 else {
            status = "Enter the address and the six-digit code."
            return
        }
        UserDefaults.standard.set(host, forKey: "host")
        busy = true; canLoad = false; status = "Looking up \(code)…"; detail = ""
        do {
            let url = base.appendingPathComponent("api/pair/\(code)")
            let p = try Pairing.parse(try await Downloader.data(from: url), base: base)
            pairing = p
            let free = PhotoWriter.freeBytes()
            status = p.label.isEmpty ? "Pack \(p.jobId)" : p.label
            detail = "\(p.fileCount) files, \(ByteFormat.human(p.totalBytes))\n"
                   + "\(ByteFormat.human(free)) free on this device"
            if free >= 0 && free < p.totalBytes {
                detail += "  — NOT ENOUGH SPACE"
                canLoad = false
            } else {
                canLoad = true
            }
        } catch {
            pairing = nil
            status = "Could not find that pack"
            detail = error.localizedDescription
        }
        busy = false
    }

    // MARK: the fill

    func load() { task = Task { await loadAsync() } }

    private func loadAsync() async {
        guard let p = pairing else { return }
        busy = true; canLoad = false; progress = 0
        do { try await runLoad(p) }
        catch is CancellationError { status = "Stopped — reopen to resume" }
        catch {
            status = "Failed"
            detail = error.localizedDescription
        }
        busy = false
        refreshReceipts()
    }

    private func runLoad(_ p: Pairing) async throws {
        guard await writer.authoriseAdd() else { throw LoaderError.notAuthorised }

        status = "Fetching manifest"
        let data = try await Downloader.data(from: p.manifestURL)
        let pack = try Pack.parse(data, base: p.manifestURL)
        let receipt = Receipt(jobId: pack.jobId)

        let pending = pack.items.filter { !receipt.has($0.name) }
        guard !pending.isEmpty else {
            status = "Already loaded"
            detail = "\(receipt.count) assets are in the library"
            progress = 1
            return
        }

        let need = pending.reduce(Int64(0)) { $0 + $1.bytes }
        let free = PhotoWriter.freeBytes()
        if free >= 0 && free < need { throw LoaderError.noSpace(need, free) }

        let album = try await writer.album(named: pack.album)
        let staging = FileManager.default.temporaryDirectory
            .appendingPathComponent("tdg-\(pack.jobId)")
        try? FileManager.default.createDirectory(at: staging,
                                                 withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: staging) }

        var done: Int64 = 0
        var rejected: [(String, String)] = []
        let already = receipt.count
        for chunk in pending.chunked(into: PhotoWriter.batchSize) {
            try Task.checkCancellation()
            // Download the batch, then import it in one transaction. Doing it
            // file-by-file would mean one Photos transaction per asset, which
            // is where a large import goes to die.
            var staged: [(item: PackItem, file: URL)] = []
            for item in chunk {
                try Task.checkCancellation()
                staged.append((item, try await Downloader.download(item, into: staging)))
                done += item.bytes
                progress = Double(done) / Double(need)
                status = "Downloading \(already + staged.count) of \(pack.fileCount)"
            }
            status = "Importing \(staged.count) into Photos"
            let outcome = await writer.add(batch: staged, to: album)
            receipt.add(outcome.created)
            rejected += outcome.rejected
            detail = "\(receipt.count) of \(pack.fileCount) in the library"
                   + (rejected.isEmpty ? "" : "  ·  \(rejected.count) refused")
        }
        progress = 1
        status = rejected.isEmpty ? "Done" : "Done, with refusals"
        detail = "\(receipt.count) assets in “\(pack.album)”"
        if !rejected.isEmpty {
            // Named, not just counted: which asset Photos refused is the
            // interesting part, and a zero-byte file being refused is the
            // correct outcome rather than a defect.
            let lines = rejected.prefix(4)
                .map { "\($0.0): \($0.1)" }.joined(separator: "\n")
            detail += "\n\nPhotos refused \(rejected.count):\n" + lines
                    + (rejected.count > 4 ? "\n…" : "")
        }
    }

    // MARK: wipe

    func wipe() {
        busy = true
        task = Task {
            var removed = 0
            do {
                // Wipe is the only thing that needs the whole library.
                guard await writer.authoriseFull() else { throw LoaderError.notAuthorisedFull }
                for job in Receipt.knownJobs() {
                    let r = Receipt(jobId: job)
                    removed += try await writer.delete(identifiers: r.identifiers)
                    r.clear()
                }
                status = "Removed \(removed) assets"
                detail = ""
            } catch {
                status = "Could not remove everything"
                detail = error.localizedDescription
            }
            busy = false
            refreshReceipts()
        }
    }

    func stop() { task?.cancel() }
}

extension Array {
    func chunked(into size: Int) -> [[Element]] {
        stride(from: 0, to: count, by: size).map {
            Array(self[$0 ..< Swift.min($0 + size, count)])
        }
    }
}
