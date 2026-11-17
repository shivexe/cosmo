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
        49: "space", 48: "tab", 36: "enter", 51: "backspace", 117: "delete", 115: "home", 119: "end", 116: "page_up", 121: "page_down",
        123: "left", 124: "right", 125: "down", 126: "up",
    ]
    static let ansi: [Int: String] = [
        0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C", 9: "V", 11: "B", 12: "Q", 13: "W", 14: "E",
        15: "R", 16: "Y", 17: "T", 18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 24: "=", 25: "9", 26: "7", 27: "-",
        28: "8", 29: "0", 30: "]", 31: "O", 32: "U", 33: "[", 34: "I", 35: "P", 37: "L", 38: "J", 39: "'", 40: "K", 41: ";",
        42: "\\", 43: ",", 44: "/", 45: "N", 46: "M", 47: ".", 50: "`",
    ]
    static let labels: [String: String] = [
        "cmd_r": "Right ⌘", "cmd": "Left ⌘", "alt_r": "Right ⌥", "alt": "Left ⌥", "ctrl_r": "Right ⌃", "ctrl": "Left ⌃",
        "shift_r": "Right ⇧", "shift": "Left ⇧", "space": "Space", "tab": "Tab",
    ]

    static func change(for event: NSEvent) -> (name: String, down: Bool)? {
        switch event.type {
        case .flagsChanged:
            guard let name = modifiers[event.keyCode] else { return nil }
            let side: [String: UInt] = ["ctrl": 0x01, "shift": 0x02, "shift_r": 0x04, "cmd": 0x08, "cmd_r": 0x10, "alt": 0x20, "alt_r": 0x40, "ctrl_r": 0x2000]
            return (name, event.modifierFlags.rawValue & (side[name] ?? 0) != 0)
        case .keyDown, .keyUp:
            if event.type == .keyDown && event.isARepeat { return nil }
            return (special[event.keyCode] ?? "<\(event.keyCode)>", event.type == .keyDown)
        default:
            return nil
        }
    }

    static func allowed(_ name: String) -> Bool {
        modifiers.values.contains(name) || name.range(of: #"^f([1-9]|1[0-9]|20)$"#, options: .regularExpression) != nil
    }

    static func overlaps(_ a: String, _ b: String) -> Bool {
        let x = Set(a.split(separator: "+").map(String.init)), y = Set(b.split(separator: "+").map(String.init))
        return x.isSubset(of: y) || y.isSubset(of: x)
    }

    static func label(_ combo: String) -> String {
        combo.split(separator: "+").map { token in
            let t = String(token)
            if let l = labels[t] { return l }
            if t.hasPrefix("<"), let n = Int(t.dropFirst().dropLast()) { return ansi[n] ?? "Key \(n)" }
            return t.uppercased()
        }
        .joined(separator: " + ")
    }
}

struct KeyRecorder: View {
    var title: String
    var key: String
    var fallback: String
    var config: ConfigFile
    @State private var problem = ""
    @State private var recording = false
    @State private var monitor: Any?
    @State private var pressed: Set<String> = []
    @State private var combo: [String] = []

    var body: some View {
        LabeledContent(title) {
            VStack(alignment: .trailing, spacing: 4) {
                Button(recording ? (combo.isEmpty ? "Hold your keys…" : KeyNames.label(combo.joined(separator: "+"))) : KeyNames.label(config.string(key, fallback))) {
                    recording ? stop() : start()
                }
                .buttonStyle(.glass)
                if !problem.isEmpty { Text(problem).font(.caption).foregroundStyle(Color(hex: 0xFF9E9E)).multilineTextAlignment(.trailing) }
            }
        }
        .onDisappear(perform: stop)
    }

    private func start() {
        recording = true
        problem = ""
        pressed = []
        combo = []
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp, .flagsChanged]) { event in
            if event.type == .keyDown && event.keyCode == 53 { stop(); return nil }
            guard let change = KeyNames.change(for: event) else { return nil }
            if change.down {
                pressed.insert(change.name)
                if !combo.contains(change.name) { combo.append(change.name) }
            } else {
                pressed.remove(change.name)
            }
            if pressed.isEmpty && !combo.isEmpty {
                let value = combo.joined(separator: "+")
                config.load()
                let other = key == "hotkey" ? config.string("dictation_key", "cmd_r") : config.string("hotkey", "alt_r")
                stop()
                if !combo.allSatisfy(KeyNames.allowed) {
                    problem = "Use ⌘ ⌥ ⌃ ⇧ or F-keys. Other keys would also type into your app."
                } else if KeyNames.overlaps(value, other) {
                    problem = "That overlaps your other shortcut (\(KeyNames.label(other)))."
                } else {
                    config.set(key, value)
                    AppDelegate.store.restartCosmo()
                }
            }
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
                .frame(minHeight: 90)
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

struct SettingsView: View {
    @State private var config = ConfigFile()
    @State private var loginOn = SMAppService.mainApp.status == .enabled
    @AppStorage("settingsTab") private var tab = "General"
    private let voices = ["af_heart", "af_bella", "af_nicole", "af_sarah", "am_michael", "am_adam", "bf_emma", "bm_george"]

    private func voiceName(_ id: String) -> String {
        let parts = id.split(separator: "_")
        guard parts.count == 2, let code = parts.first, code.count == 2 else { return id }
        let accent = code.first == "b" ? "UK" : "US"
        let gender = code.last == "m" ? "male" : "female"
        return "\(parts[1].capitalized) · \(accent) English, \(gender)"
    }

    var body: some View {
        TabView(selection: $tab) {
            Form {
                Section {
                TextField("Your name", text: Binding(get: { config.string("user_name", "") }, set: { config.set("user_name", $0) }),
                          prompt: Text("What Cosmo calls you"))
                TextField("Python", text: Binding(get: { config.string("python", "") }, set: { config.set("python", $0) }),
                          prompt: Text("~/.cosmo/venv/bin/python"))
                Toggle("Open Cosmo at login", isOn: Binding(get: { loginOn }, set: { on in
                    if on { try? SMAppService.mainApp.register() } else { try? SMAppService.mainApp.unregister() }
                    loginOn = SMAppService.mainApp.status == .enabled
                }))
                LabeledContent("Cosmo") {
                    Button("Restart") { AppDelegate.store.restartCosmo() }
                }
                } footer: {
                    Text("Changes to your name, Python and models apply after a restart.").foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            .fitsContent()
            .tabItem { Label("General", systemImage: "gearshape") }
            .tag("General")

            Form {
                Picker("Voice", selection: Binding(get: { config.string("voice", "af_heart") }, set: { config.set("voice", $0) })) {
                    ForEach(voices, id: \.self) { Text(voiceName($0)).tag($0) }
                }
                LabeledContent("Speed") {
                    HStack {
                        Slider(value: Binding(get: { config.number("speed", 1.0) }, set: { config.set("speed", (($0 * 20).rounded() / 20)) }), in: 0.8...1.4)
                        Text(String(format: "%.2f×", config.number("speed", 1.0))).monospacedDigit().frame(width: 52)
                    }
                }
                Text("Voice and speed apply to the next thing Cosmo says.").font(.caption).foregroundStyle(.secondary)
                Section("How Cosmo says tricky words: word, a tab, then sounds") {
                    TextFileEditor(url: EventStore.home.appendingPathComponent("pronunciations.txt"))
                }
            }
            .formStyle(.grouped)
            .fitsContent()
            .tabItem { Label("Voice", systemImage: "speaker.wave.2") }
            .tag("Voice")

            Form {
                Picker("Start listening with", selection: Binding(get: { config.string("listen_mode", "hotkey") }, set: { config.set("listen_mode", $0) })) {
                    Text("“Hey Cosmo”").tag("wake")
                    Text("Hotkey only").tag("hotkey")
                    Text("Always").tag("always")
                }
                KeyRecorder(title: "Hold to talk", key: "hotkey", fallback: "alt_r", config: config)
                KeyRecorder(title: "Hold to dictate", key: "dictation_key", fallback: "cmd_r", config: config)
                LabeledContent("Silence before answering", value: "\(Int(config.number("end_of_speech_ms", 700))) ms")
                Text("Click a shortcut, hold one key or several together, then let go. Esc cancels.").font(.caption).foregroundStyle(.secondary)
            }
            .formStyle(.grouped)
            .fitsContent()
            .tabItem { Label("Listening", systemImage: "mic") }
            .tag("Listening")

            Form {
                Picker("Brain", selection: Binding(get: { config.string("brain", "claude") }, set: { config.set("brain", $0); config.set("brain_model", nil) })) {
                    Text("Claude").tag("claude")
                    Text("Codex").tag("codex")
                }
                if config.string("brain", "claude") == "claude" {
                    Picker("Talking model", selection: Binding(get: { config.string("brain_model", "") }, set: { config.set("brain_model", $0.isEmpty ? nil : $0) })) {
                        Text("Sonnet 5.5 (default)").tag("")
                        Text("Haiku 5.5").tag("claude-haiku-5-5")
                        Text("Opus 5.5").tag("claude-opus-5-5")
                    }
                    LabeledContent("Task model", value: "Opus")
                } else {
                    LabeledContent("Talking model", value: "Fast Codex model")
                    LabeledContent("Task model", value: "Your default Codex model")
                    Text("Codex uses your ChatGPT login. Its workers can’t message your Claude sessions.").font(.caption).foregroundStyle(.secondary)
                }
                LabeledContent("Speech to text", value: "Whisper small.en")
                TextField("Fine-tuned adapter", text: Binding(get: { config.string("adapter_path", "") }, set: { config.set("adapter_path", $0) }),
                          prompt: Text("Optional LoRA folder"))
            }
            .formStyle(.grouped)
            .fitsContent()
            .tabItem { Label("Models", systemImage: "cpu") }
            .tag("Models")
        }
        .frame(width: 520)
        .noInitialFocus()
        .preferredColorScheme(.dark)
    }
}
