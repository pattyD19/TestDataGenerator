import Foundation
import Photos
import CoreLocation

/// Putting assets into the Photos library.
///
/// `PHAssetCreationRequest` is the only supported route: copying files into
/// DCIM over AFC leaves them unindexed by the Photos database, which is a known
/// dead end and why this app exists at all.
final class PhotoWriter {

    /// Batch size for one `performChanges` transaction.
    ///
    /// One transaction for 26,000 assets stalls the library; one transaction
    /// per asset spends all its time in round trips. A hundred is the plan's
    /// figure and behaves.
    static let batchSize = 100

    /// Permission is asked for in two stages, deliberately.
    ///
    /// Filling the library needs only `.addOnly`, so that is all the app asks
    /// for up front: a tester who only ever loads packs never hands this tool
    /// their whole photo library. `.addOnly` cannot fetch or delete, though, so
    /// **wipe** escalates to `.readWrite` at the moment it is needed and
    /// explains why in the usage string.
    ///
    /// Verified on iOS 26.5: `.addOnly` is sufficient for every
    /// `PHAssetCreationRequest` this app makes, including video.
    func authoriseAdd() async -> Bool {
        let current = PHPhotoLibrary.authorizationStatus(for: .addOnly)
        if current == .authorized { return true }
        if current == .denied || current == .restricted { return false }
        return await PHPhotoLibrary.requestAuthorization(for: .addOnly) == .authorized
    }

    func authoriseFull() async -> Bool {
        let current = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        if current == .authorized { return true }
        if current == .denied || current == .restricted { return false }
        return await PHPhotoLibrary.requestAuthorization(for: .readWrite) == .authorized
    }

    /// Fetch or create the album a pack lands in, so a tester can find and
    /// clear it by hand even if this app is gone.
    /// Returns nil under add-only: fetching and creating collections needs
    /// full access, and an album is a convenience rather than the mechanism —
    /// the receipt is what makes wipe exact.
    func album(named title: String) async throws -> PHAssetCollection? {
        guard PHPhotoLibrary.authorizationStatus(for: .readWrite) == .authorized else {
            return nil
        }
        let opts = PHFetchOptions()
        opts.predicate = NSPredicate(format: "localizedTitle = %@", title)
        let found = PHAssetCollection.fetchAssetCollections(
            with: .album, subtype: .albumRegular, options: opts)
        if let existing = found.firstObject { return existing }

        var placeholder: PHObjectPlaceholder?
        try await PHPhotoLibrary.shared().performChanges {
            let req = PHAssetCollectionChangeRequest
                .creationRequestForAssetCollection(withTitle: title)
            placeholder = req.placeholderForCreatedAssetCollection
        }
        guard let id = placeholder?.localIdentifier else { return nil }
        return PHAssetCollection.fetchAssetCollections(
            withLocalIdentifiers: [id], options: nil).firstObject
    }

    /// Import a batch, isolating any asset Photos refuses.
    ///
    /// A `performChanges` transaction is all-or-nothing: one invalid resource
    /// fails every asset in it. Found on real hardware — a 72-asset pack
    /// containing a zero-byte file failed entirely with
    /// PHPhotosErrorInvalidResource (3302), losing 300 MB of completed
    /// transfer along with it.
    ///
    /// So the fast path is one transaction for the whole batch, and on failure
    /// it retries one asset at a time to find out which ones are actually bad.
    /// Assets Photos legitimately cannot accept — an empty file has no
    /// resource to validate — are reported, not fatal: the point of the pack is
    /// that the *app under test* meets them, so the loader has to get the rest
    /// of the library in place.
    func add(batch: [(item: PackItem, file: URL)],
             to collection: PHAssetCollection?)
             async -> (created: [(String, String)], rejected: [(String, String)]) {
        do {
            return (try await addAll(batch, to: collection), [])
        } catch {
            var created: [(String, String)] = []
            var rejected: [(String, String)] = []
            for entry in batch {
                do {
                    created += try await addAll([entry], to: collection)
                } catch {
                    rejected.append((entry.item.name, Self.describe(error)))
                }
            }
            return (created, rejected)
        }
    }

    /// Photos error codes are opaque integers in a crash log; this turns the
    /// ones a loader actually meets into something readable.
    static func describe(_ error: Error) -> String {
        let ns = error as NSError
        guard ns.domain == PHPhotosErrorDomain else { return ns.localizedDescription }
        switch ns.code {
        case 3302: return "resource validation failed (an empty or truncated file "
                        + "cannot become a PHAsset)"
        case 3303: return "resource missing"
        case 3305: return "not enough space"
        case 3300: return "change request not supported as configured"
        case 3301: return "operation interrupted — transient, worth retrying"
        case 3311: return "the user denied photo access"
        default:   return "PHPhotosError \(ns.code)"
        }
    }

    private func addAll(_ batch: [(item: PackItem, file: URL)],
                        to collection: PHAssetCollection?) async throws -> [(String, String)] {
        var placeholders: [(String, PHObjectPlaceholder)] = []

        try await PHPhotoLibrary.shared().performChanges {
            for entry in batch {
                let req = entry.item.isVideo
                    ? PHAssetCreationRequest.forAsset()
                    : PHAssetCreationRequest.forAsset()
                let options = PHAssetResourceCreationOptions()
                options.originalFilename = entry.item.name
                // NOT shouldMoveFile. Moving is cheaper, but a failed
                // transaction has already consumed the files it processed
                // before the failure, so the retry below finds nothing to read
                // and reports perfectly good assets as invalid.
                //
                // Seen on real hardware: a 72-asset batch aborted on a
                // zero-byte file at index 13, and the retry then "refused"
                // assets 0-12 — a contiguous run, which is the tell. Copying
                // costs one pass over the bytes and makes the retry honest.
                options.shouldMoveFile = false
                req.addResource(with: entry.item.isVideo ? .video : .photo,
                                fileURL: entry.file, options: options)

                // The manifest's instant, not "now". This is what Photos
                // groups a timeline by, and the reason the generator carries a
                // timezone all the way through.
                req.creationDate = entry.item.takenAt
                if let lat = entry.item.latitude, let lon = entry.item.longitude {
                    req.location = CLLocation(latitude: lat, longitude: lon)
                }
                if let ph = req.placeholderForCreatedAsset {
                    placeholders.append((entry.item.name, ph))
                    if let collection,
                       let add = PHAssetCollectionChangeRequest(for: collection) {
                        add.addAssets([ph] as NSArray)
                    }
                }
            }
        }
        return placeholders.map { ($0.0, $0.1.localIdentifier) }
    }

    /// Remove assets by identifier. Photos shows one system confirmation per
    /// transaction, so this deletes in as few transactions as it can.
    func delete(identifiers: [String]) async throws -> Int {
        guard !identifiers.isEmpty else { return 0 }
        let assets = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
        guard assets.count > 0 else { return 0 }
        try await PHPhotoLibrary.shared().performChanges {
            PHAssetChangeRequest.deleteAssets(assets)
        }
        return assets.count
    }

    /// Free space where the library lives, checked before a byte is written.
    static func freeBytes() -> Int64 {
        let url = URL(fileURLWithPath: NSHomeDirectory())
        let values = try? url.resourceValues(
            forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage ?? -1
    }
}
