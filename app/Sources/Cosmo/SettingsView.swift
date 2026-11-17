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

struct SettingsView: View {
    @State private var config = ConfigFile()
    private let voices = ["af_heart", "af_bella", "af_nicole", "af_sarah", "am_michael", "am_adam", "bf_emma", "bm_george"]
    private let keys = ["alt_r": "Right Option", "cmd_r": "Right Command", "f13": "F13"]

    var body: some View {
        TabView {
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
                Picker("Talk hotkey", selection: Binding(get: { config.string("hotkey", "alt_r") }, set: { config.set("hotkey", $0) })) {
                    ForEach(keys.keys.sorted(), id: \.self) { Text(keys[$0] ?? $0).tag($0) }
                }
                LabeledContent("Silence before answering", value: "\(Int(config.number("end_of_speech_ms", 700))) ms")
                Text("Listening changes apply after Cosmo restarts.").font(.caption).foregroundStyle(.secondary)
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

            Form {
                Picker("Talking brain", selection: Binding(get: { config.string("brain_model", "") }, set: { config.set("brain_model", $0.isEmpty ? nil : $0) })) {
                    Text("Sonnet 5.5 (default)").tag("")
                    Text("Haiku 5.5").tag("claude-haiku-5-5")
                    Text("Opus 5.5").tag("claude-opus-5-5")
                }
                LabeledContent("Workers", value: "Opus, set by the brain’s instructions")
                LabeledContent("Speech to text", value: config.string("speech_engine", "whisper-small-lora"))
                Text("Model changes apply after Cosmo restarts.").font(.caption).foregroundStyle(.secondary)
            }
            .formStyle(.grouped)
            .tabItem { Label("Models", systemImage: "cpu") }
        }
        .frame(width: 560, height: 460)
        .preferredColorScheme(.dark)
    }
}
