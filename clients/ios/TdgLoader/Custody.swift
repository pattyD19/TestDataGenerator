import Foundation
import UIKit

/// Keeping a copy of the receipt somewhere the app cannot take with it.
///
/// The receipt in Application Support is authoritative — it is what a resumed
/// fill reads and what `deleteAssets` is driven from. But it is app storage, so
/// deleting the app destroys it and leaves every imported asset in Photos with
/// nothing left to name it. The library keeps the bytes; the only record of
/// *which* assets they are goes. "A test you cannot undo is a test you run
/// once" then stops being true.
///
/// So the receipt is also deposited with the control plane, and a freshly
/// installed app can ask for it back. The pack does not have to still exist:
/// recovery works after a prune, which is the situation it is actually for.
///
/// **Every call here is best effort.** A control plane that has moved or gone
/// away must never fail a fill or block a wipe — those depend on the device's
/// own receipt. This is a second chance, not a dependency.
enum Custody {

    // MARK: a device id that outlives the app

    private static let account = "tdg.device-id"
    private static var memo: String?

    private static func keychainRead() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.tdg.loader",
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    private static func keychainWrite(_ value: String) -> Bool {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.tdg.loader",
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(base as CFDictionary)
        var add = base
        add[kSecValueData as String] = Data(value.utf8)
        // AfterFirstUnlock so a fill that starts while the phone is locked can
        // still read it; the id is not a secret, only a stable name.
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }

    /// `identifierForVendor` is exactly the wrong tool here: it is reset when
    /// the last app from a vendor is deleted, which is precisely the event this
    /// whole file exists to survive. Keychain items are not removed with the
    /// app, so a UUID stored there is stable across a reinstall and is cleared
    /// only by erasing the device — which takes the assets with it anyway.
    static func deviceId() -> String {
        if let memo { return memo }
        if let existing = keychainRead() { memo = existing; return existing }

        let fresh = UUID().uuidString
        // The write has to be *checked*, not fired and forgotten. An unsigned
        // simulator build carries no keychain-access-group entitlement, so
        // SecItemAdd fails; ignoring that returned a brand-new UUID on every
        // call, and the deposit, the recovery and the withdrawal each went out
        // under a different device. Nothing errored — the server simply had no
        // row matching the id being asked about.
        if keychainWrite(fresh), keychainRead() == fresh {
            memo = fresh
            return fresh
        }

        // No keychain. UserDefaults still gives one id per install, which keeps
        // deposit, recovery and withdrawal agreeing with each other. What it
        // cannot do is survive the app being deleted — so recovery after a
        // delete works on a signed device build and not in the simulator.
        let store = UserDefaults.standard
        if let prior = store.string(forKey: account) { memo = prior; return prior }
        store.set(fresh, forKey: account)
        memo = fresh
        return fresh
    }

    static func deviceName() -> String { UIDevice.current.name }

    // MARK: talking to the control plane

    private static func url(_ base: URL, _ token: String, device: String? = nil) -> URL? {
        var c = URLComponents(url: base.appendingPathComponent("api/receipts"),
                              resolvingAgainstBaseURL: false)
        var items = [URLQueryItem(name: "token", value: token)]
        if let device { items.append(URLQueryItem(name: "device", value: device)) }
        c?.queryItems = items
        return c?.url
    }

    /// Deposit this device's receipt. Failures are swallowed on purpose.
    @discardableResult
    static func deposit(base: URL?, token: String, receipt: Receipt) async -> Bool {
        guard let base, !token.isEmpty, let url = url(base, token) else { return false }
        let body: [String: Any] = [
            "device": deviceId(),
            "platform": "ios",
            "device_name": deviceName(),
            "entries": receipt.entries(),
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return false }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        return ((try? await URLSession.shared.data(for: req)) != nil)
    }

    /// Ask for this device's receipt back and adopt it. Returns how many
    /// entries were recovered.
    ///
    /// Only ever called against an empty receipt — a device that still holds
    /// its own record is the authority, and adopting over it could only lose
    /// entries.
    static func recover(base: URL?, token: String, receipt: Receipt) async -> Int {
        guard let base, !token.isEmpty, receipt.count == 0,
              let url = url(base, token, device: deviceId()),
              let (data, response) = try? await URLSession.shared.data(from: url),
              (response as? HTTPURLResponse).map({ (200...299).contains($0.statusCode) }) ?? false,
              let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let entries = o["entries"] as? [String: String], !entries.isEmpty
        else { return 0 }
        receipt.adopt(entries)
        return entries.count
    }

    /// Withdraw the copy after a wipe: once the assets are gone it is only a
    /// stale claim that something still needs cleaning up.
    static func forget(base: URL?, token: String) async {
        guard let base, !token.isEmpty,
              let url = url(base, token, device: deviceId()) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        _ = try? await URLSession.shared.data(for: req)
    }
}
