import AppKit
import ServiceManagement
import SwiftUI

@MainActor
@Observable
final class ConfigFile {
    static let url = EventStore.home.appendingPathComponent("config.json")
    var values: [String: Any] = [:]

    init() { _ = load() }

    @discardableResult
    func load() -> Bool {
        guard FileManager.default.fileExists(atPath: Self.url.path) else { values = [:]; return true }
        guard let data = try? Data(contentsOf: Self.url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        values = obj
        return true
    }

    func string(_ key: String, _ fallback: String) -> String { values[key] as? String ?? fallback }
    func number(_ key: String, _ fallback: Double) -> Double { (values[key] as? NSNumber)?.doubleValue ?? fallback }

    func set(_ key: String, _ value: Any?) {
        guard load() else { return }
        values[key] = value ?? NSNull()
        guard let data = try? JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys]) else { return }
        try? data.write(to: Self.url, options: .atomic)
    }
}

enum KeyNames {
    static let modifiers: [UInt16: String] = [54: "cmd_r", 55: "cmd", 61: "alt_r", 58: "alt", 62: "ctrl_r", 59: "ctrl", 60: "shift_r", 56: "shift"]
    static let special: [UInt16: String] = [
        122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7", 100: "f8", 101: "f9", 109: "f10",
        103: "f11", 111: "f12", 105: "f13", 107: "f14", 113: "f15", 106: "f16", 64: "f17", 79: "f18", 80: "f19", 90: "f20",
        49: "space", 48: "tab", 117: "delete", 115: "home", 119: "end", 116: "page_up", 121: "page_down",
    ]
    static let labels: [String: String] = [
        "cmd_r": "Right ⌘", "cmd": "Left ⌘", "alt_r": "Right ⌥", "alt": "Left ⌥", "ctrl_r": "Right ⌃", "ctrl": "Left ⌃",
        "shift_r": "Right ⇧", "shift": "Left ⇧", "space": "Space", "tab": "Tab",
    ]

    static func name(for event: NSEvent) -> String? {
        if event.type == .flagsChanged {
            guard let name = modifiers[event.keyCode] else { return nil }
            let down: Bool
            switch name {
            case "cmd", "cmd_r": down = event.modifierFlags.contains(.command)
            case "alt", "alt_r": down = event.modifierFlags.contains(.option)
            case "ctrl", "ctrl_r": down = event.modifierFlags.contains(.control)
            case "shift", "shift_r": down = event.modifierFlags.contains(.shift)
            default: down = true
            }
            return down ? name : nil
        }
        if let name = special[event.keyCode] { return name }
        guard let c = event.charactersIgnoringModifiers?.lowercased(), c.count == 1, c.unicodeScalars.allSatisfy({ $0.value >= 33 && $0.value < 127 }) else { return nil }
        return c
    }

    static func label(_ name: String) -> String { labels[name] ?? name.uppercased() }
}

struct KeyRecorder: View {
    var title: String
    var key: String
    var fallback: String
    var config: ConfigFile
    @State private var recording = false
    @State private var monitor: Any?

    var body: some View {
        LabeledContent(title) {
            Button(recording ? "Press a key…" : KeyNames.label(config.string(key, fallback))) { recording ? stop() : start() }
                .buttonStyle(.glass)
        }
        .onDisappear(perform: stop)
    }

    private func start() {
        recording = true
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .flagsChanged]) { event in
            if event.type == .keyDown && event.keyCode == 53 { stop(); return nil }
            guard let name = KeyNames.name(for: event) else { return event }
            config.set(key, name)
            stop()
            AppDelegate.store.restartCosmo()
            return nil
        }
    }

    private func stop() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        recording = false
    }
}

struct TextFileEditor: View {
    var url: URL
    @State private var text = ""
    @State private var saved = ""
    var body: some View {
        VStack(alignment: .trailing, spacing: 8) {
            TextEditor(text: $text)
                .font(.system(size: 13, design: .monospaced))
                .scrollContentBackground(.hidden)
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 10).fill(.black.opacity(0.2)))
                .frame(minHeight: 160)
            Button("Save") {
                try? text.write(to: url, atomically: true, encoding: .utf8)
                saved = text
            }
            .disabled(text == saved)
        }
        .onAppear {
            text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
            saved = text
        }
    }
}

struct HostConfig: Identifiable, Equatable {
    var id = UUID()
    var name: String
    var label: String
    var kind: String
    var target: String
    var shell: String
}

struct DevicesSettings: View {
    var config: ConfigFile
    @State private var hosts: [HostConfig] = []
    @State private var saved: [HostConfig] = []

    var body: some View {
        Form {
            Section {
                ForEach($hosts) { $h in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            TextField("Name", text: $h.label).fontWeight(.semibold)
                            Picker("", selection: $h.kind) {
                                Text("SSH").tag("ssh")
                                Text("Android (adb)").tag("adb")
                            }
                            .labelsHidden().frame(width: 140)
                            Button(role: .destructive) { hosts.removeAll { $0.id == h.id } } label: { Image(systemName: "trash") }
                                .buttonStyle(.borderless)
                        }
                        TextField(h.kind == "adb" ? "Device serial (optional)" : "SSH host, e.g. user@server or a ~/.ssh/config name", text: $h.target)
                        if h.kind == "ssh" {
                            TextField("Shell (bash, PowerShell…)", text: $h.shell)
                        }
                    }
                    .padding(.vertical, 4)
                }
                if hosts.isEmpty { Text("No other machines yet.").foregroundStyle(.secondary) }
            } header: {
                Text("Machines Cosmo can run commands on")
            } footer: {
                Text("Cosmo checks them every minute. Workers learn about changes after a restart.").font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button("Add machine") { hosts.append(HostConfig(name: "", label: "", kind: "ssh", target: "", shell: "bash")) }
                Spacer()
                Button("Save") { save() }.buttonStyle(.glassProminent).disabled(hosts == saved)
            }
        }
        .formStyle(.grouped)
        .onAppear(perform: load)
    }

    private func load() {
        config.load()
        let dict = config.values["hosts"] as? [String: [String: Any]] ?? [:]
        hosts = dict.keys.sorted().map { name in
            let h = dict[name] ?? [:]
            return HostConfig(name: name, label: h["label"] as? String ?? name, kind: h["kind"] as? String ?? "ssh",
                              target: h["target"] as? String ?? "", shell: h["shell"] as? String ?? "")
        }
        saved = hosts
    }

    private func save() {
        var dict: [String: Any] = [:]
        for h in hosts where !h.label.trimmingCharacters(in: .whitespaces).isEmpty {
            var name = h.name.isEmpty ? h.label.lowercased().replacingOccurrences(of: #"[^a-z0-9]+"#, with: "-", options: .regularExpression) : h.name
            while dict[name] != nil { name += "-2" }
            var entry: [String: Any] = ["label": h.label, "kind": h.kind, "target": h.target]
            if h.kind == "ssh" { entry["shell"] = h.shell.isEmpty ? "bash" : h.shell }
            dict[name] = entry
        }
        config.set("hosts", dict)
        load()
    }
}

struct SettingsView: View {
    @State private var config = ConfigFile()
    @State private var loginOn = SMAppService.mainApp.status == .enabled
    private let voices = ["af_heart", "af_bella", "af_nicole", "af_sarah", "am_michael", "am_adam", "bf_emma", "bm_george"]

    var body: some View {
        TabView {
            Form {
                TextField("Your name", text: Binding(get: { config.string("user_name", "") }, set: { config.set("user_name", $0) }),
                          prompt: Text("What Cosmo calls you"))
                TextField("Python", text: Binding(get: { config.string("python", "") }, set: { config.set("python", $0) }),
                          prompt: Text("~/.cosmo/venv/bin/python"))
                Toggle("Open Cosmo at login", isOn: Binding(get: { loginOn }, set: { on in
                    if on { try? SMAppService.mainApp.register() } else { try? SMAppService.mainApp.unregister() }
                    loginOn = SMAppService.mainApp.status == .enabled
                }))
            }
            .formStyle(.grouped)
            .tabItem { Label("General", systemImage: "gearshape") }

            Form {
                Picker("Voice", selection: Binding(get: { config.string("voice", "af_heart") }, set: { config.set("voice", $0) })) {
                    ForEach(voices, id: \.self) { Text($0).tag($0) }
                }
                LabeledContent("Speed") {
                    HStack {
                        Slider(value: Binding(get: { config.number("speed", 1.0) }, set: { config.set("speed", (($0 * 20).rounded() / 20)) }), in: 0.8...1.4)
                        Text(String(format: "%.2f×", config.number("speed", 1.0))).monospacedDigit().frame(width: 52)
                    }
                }
                Text("Voice and speed apply to the next thing Cosmo says.").font(.caption).foregroundStyle(.secondary)
            }
            .formStyle(.grouped)
            .tabItem { Label("Voice", systemImage: "speaker.wave.2") }

            Form {
                Picker("Start listening with", selection: Binding(get: { config.string("listen_mode", "hotkey") }, set: { config.set("listen_mode", $0) })) {
                    Text("“Hey Cosmo”").tag("wake")
                    Text("Hotkey only").tag("hotkey")
                    Text("Always").tag("always")
                }
                KeyRecorder(title: "Hold to talk", key: "hotkey", fallback: "alt_r", config: config)
                KeyRecorder(title: "Hold to dictate", key: "dictation_key", fallback: "cmd_r", config: config)
                LabeledContent("Silence before answering", value: "\(Int(config.number("end_of_speech_ms", 700))) ms")
                Text("Click a shortcut, then press the key you want. Cosmo restarts to use it. Esc cancels.").font(.caption).foregroundStyle(.secondary)
            }
            .formStyle(.grouped)
            .tabItem { Label("Listening", systemImage: "mic") }

            Form {
                Section("Words Cosmo listens for, one per line") {
                    TextFileEditor(url: EventStore.home.appendingPathComponent("words.txt"))
                }
                Section("Pronunciations: word, a tab, then sounds") {
                    TextFileEditor(url: EventStore.home.appendingPathComponent("pronunciations.txt"))
                }
            }
            .formStyle(.grouped)
            .tabItem { Label("Words", systemImage: "character.book.closed") }

            DevicesSettings(config: config)
                .tabItem { Label("Devices", systemImage: "laptopcomputer.and.iphone") }

            Form {
                Picker("Talking brain", selection: Binding(get: { config.string("brain_model", "") }, set: { config.set("brain_model", $0.isEmpty ? nil : $0) })) {
                    Text("Sonnet 5.5 (default)").tag("")
                    Text("Haiku 5.5").tag("claude-haiku-5-5")
                    Text("Opus 5.5").tag("claude-opus-5-5")
                }
                LabeledContent("Workers", value: "Opus, set by the brain’s instructions")
                LabeledContent("Speech to text", value: "Whisper small.en")
                TextField("Fine-tuned adapter", text: Binding(get: { config.string("adapter_path", "") }, set: { config.set("adapter_path", $0) }),
                          prompt: Text("Optional LoRA folder"))
            }
            .formStyle(.grouped)
            .tabItem { Label("Models", systemImage: "cpu") }
        }
        .safeAreaInset(edge: .bottom) {
            HStack {
                Text("Most changes apply after a restart.").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Restart Cosmo") { AppDelegate.store.restartCosmo() }
            }
            .padding(.horizontal, 20).padding(.vertical, 10)
        }
        .frame(width: 600, height: 540)
        .preferredColorScheme(.dark)
    }
}
