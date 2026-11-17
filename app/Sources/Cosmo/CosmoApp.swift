import AppKit
import CoreAudio
import ServiceManagement
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static let store = EventStore()
    private var overlay: OverlayPanel?
    private var dictating: DictationPanel?
    private let highlight = HighlightPanel()
    private var controlBar: ControlPanel?

    private func show(_ panel: NSPanel?, _ visible: Bool) {
        guard let panel else { return }
        if visible && !panel.isVisible {
            panel.orderFrontRegardless()
        } else if !visible && panel.isVisible && Self.store.clicking {
            panel.orderOut(nil)
        } else if !visible && panel.isVisible {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
                guard self != nil else { return }
                if panel.isVisible && !self!.stillWanted(panel) { panel.orderOut(nil) }
            }
        }
    }

    private func stillWanted(_ panel: NSPanel) -> Bool {
        let store = Self.store
        if panel === overlay { return !store.clicking && (store.approval != nil || [.listening, .thinking, .speaking, .waiting].contains(store.state)) }
        if panel === dictating { return store.dictation != .off }
        if panel === controlBar { return store.control != nil && !store.clicking }
        return false
    }
    private var timer: Timer?
    private var activity: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .latencyCritical], reason: "Cosmo listens and speaks in real time")
        let store = Self.store
        store.start()
        store.startCosmo()
        overlay = OverlayPanel(store: store)
        dictating = DictationPanel(store: store)
        controlBar = ControlPanel(store: store)
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
            let store = Self.store
            overlay?.ignoresMouseEvents = store.approval == nil
            show(overlay, !store.clicking && (store.approval != nil || [.listening, .thinking, .speaking, .waiting].contains(store.state)))
            show(dictating, store.dictation != .off)
            show(controlBar, store.control != nil && !store.clicking)
            if let box = store.control?.box, box.width > 0, box.height > 0, box.width < 3000 {
                highlight.flash(box)
            }
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
