import AppKit
import CoreAudio
import ServiceManagement
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static let store = EventStore()
    private var overlay: OverlayPanel?
    private var dictating: DictationPanel?
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
        let dictationPanel = DictationPanel(store: store)
        dictationPanel.orderFrontRegardless()
        dictating = dictationPanel
        trackApproval()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in
            Task { @MainActor in store.checkAlive() }
        }
        if !UserDefaults.standard.bool(forKey: "loginItemSet") {
            try? SMAppService.mainApp.register()
            UserDefaults.standard.set(true, forKey: "loginItemSet")
        }
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in store.send(["cmd": "audio_reset", "why": "wake"]) }
        }
        watchAudioDevices()
    }

    private func watchAudioDevices() {
        for selector in [kAudioHardwarePropertyDefaultInputDevice, kAudioHardwarePropertyDefaultOutputDevice] {
            var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, .main) { _, _ in
                Task { @MainActor in Self.store.send(["cmd": "audio_reset", "why": "device"]) }
            }
        }
    }

    private func trackApproval() {
        withObservationTracking {
            overlay?.ignoresMouseEvents = Self.store.approval == nil
        } onChange: {
            Task { @MainActor in self.trackApproval() }
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
