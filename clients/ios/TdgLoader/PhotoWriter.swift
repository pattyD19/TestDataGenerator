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

    /// Import one batch of already-downloaded files in a single transaction.
    ///
    /// Returns (fileName, localIdentifier) for each asset created, which is
    /// what the receipt records and what wipe later deletes.
    func add(batch: [(item: PackItem, file: URL)],
             to collection: PHAssetCollection?) async throws -> [(String, String)] {
        var placeholders: [(String, PHObjectPlaceholder)] = []

        try await PHPhotoLibrary.shared().performChanges {
            for entry in batch {
                let req = entry.item.isVideo
                    ? PHAssetCreationRequest.forAsset()
                    : PHAssetCreationRequest.forAsset()
                let options = PHAssetResourceCreationOptions()
                options.originalFilename = entry.item.name
                // The bytes are already on disk in a temp file this app owns,
                // so hand them over rather than copying them again.
                options.shouldMoveFile = true
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
