import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static let store = EventStore()
    private var overlay: OverlayPanel?
    private var timer: Timer?
    private var activity: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .latencyCritical], reason: "Cosmo listens and speaks in real time")
        let store = Self.store
        store.start()
        store.startCosmo()
        let panel = OverlayPanel(store: store)
        panel.orderFrontRegardless()
        overlay = panel
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { _ in
            Task { @MainActor in
                panel.ignoresMouseEvents = store.approval == nil
                store.checkAlive()
            }
        }
    }
}

@main
struct CosmoApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        WindowGroup("Cosmo") {
            MainWindow(store: AppDelegate.store)
        }
        .defaultSize(width: 1240, height: 800)
        Settings {
            SettingsView()
        }
    }
}
