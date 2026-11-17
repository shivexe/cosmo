import SwiftUI

struct ChatMessage: Identifiable, Equatable {
    let id: String
    let fromYou: Bool
    var text: String
    var tools: [String] = []
    var time: Date?
}

@MainActor
@Observable
final class Transcript {
    private(set) var messages: [ChatMessage] = []
    private(set) var loaded = false
    @ObservationIgnored private var source: DispatchSourceFileSystemObject?
    @ObservationIgnored private var url: URL?
    @ObservationIgnored private var size: UInt64 = 0
    @ObservationIgnored private var reading = false
    @ObservationIgnored private var again = false
    nonisolated static let tailBytes: UInt64 = 4_000_000

    func open(_ path: String?) {
        let next = path.map { URL(fileURLWithPath: $0) }
        guard next != url else { return }
        close()
        url = next
        messages = []
        loaded = false
        guard let next else { loaded = true; return }
        reload()
        attach(next)
    }

    private func attach(_ target: URL) {
        guard url == target, source == nil else { return }
        let fd = Darwin.open(target.path, O_EVTONLY)
        guard fd >= 0 else {
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(2))
                self?.reload()
                self?.attach(target)
            }
            return
        }
        let s = DispatchSource.makeFileSystemObjectSource(fileDescriptor: fd, eventMask: [.write, .extend], queue: .main)
        s.setEventHandler { [weak self] in MainActor.assumeIsolated { self?.reload() } }
        s.setCancelHandler { Darwin.close(fd) }
        s.resume()
        source = s
    }

    func close() {
        source?.cancel()
        source = nil
        url = nil
    }

    private func reload() {
        guard let url else { return }
        if reading { again = true; return }
        reading = true
        Task.detached(priority: .utility) {
            let parsed = Transcript.parse(url)
            await MainActor.run {
                if url == self.url, parsed != self.messages { self.messages = parsed }
                self.loaded = true
                self.reading = false
                if self.again { self.again = false; self.reload() }
            }
        }
    }

    nonisolated static func parse(_ url: URL) -> [ChatMessage] {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return [] }
        defer { try? handle.close() }
        let end = (try? handle.seekToEnd()) ?? 0
        let start = end > tailBytes ? end - tailBytes : 0
        try? handle.seek(toOffset: start)
        guard let data = try? handle.readToEnd() else { return [] }
        var lines = data.split(separator: 0x0A)
        if start > 0, !lines.isEmpty { lines.removeFirst() }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var out: [ChatMessage] = []
        for raw in lines {
            guard let row = try? JSONSerialization.jsonObject(with: Data(raw)) as? [String: Any],
                  let type = row["type"] as? String, type == "user" || type == "assistant",
                  row["isMeta"] as? Bool != true, row["isSidechain"] as? Bool != true,
                  let message = row["message"] as? [String: Any] else { continue }
            let time = (row["timestamp"] as? String).flatMap { iso.date(from: $0) }
            var text: [String] = []
            var tools: [String] = []
            if let s = message["content"] as? String {
                text.append(s)
            } else if let blocks = message["content"] as? [[String: Any]] {
                for b in blocks {
                    switch b["type"] as? String {
                    case "text": if let t = b["text"] as? String { text.append(t) }
                    case "tool_use": tools.append(toolLine(b))
                    default: break
                    }
                }
            }
            let body = text.joined(separator: "\n\n").trimmingCharacters(in: .whitespacesAndNewlines)
            let fromYou = type == "user"
            if fromYou && (body.isEmpty || body.hasPrefix("<")) { continue }
            if body.isEmpty && tools.isEmpty { continue }
            if !fromYou, let last = out.last, !last.fromYou {
                out[out.count - 1].text = [last.text, body].filter { !$0.isEmpty }.joined(separator: "\n\n")
                out[out.count - 1].tools += tools
            } else {
                out.append(ChatMessage(id: row["uuid"] as? String ?? UUID().uuidString, fromYou: fromYou, text: body, tools: tools, time: time))
            }
        }
        return Array(out.suffix(400))
    }

    nonisolated static func toolLine(_ block: [String: Any]) -> String {
        let name = block["name"] as? String ?? "Tool"
        let input = block["input"] as? [String: Any] ?? [:]
        let detail = ["description", "command", "file_path", "pattern", "url", "query", "prompt"].lazy
            .compactMap { input[$0] as? String }.first ?? ""
        let short = detail.replacingOccurrences(of: "\n", with: " ")
        return short.isEmpty ? name : "\(name) · \(short.prefix(90))"
    }
}

struct ChatView: View {
    var messages: [ChatMessage]
    var empty: String
    var placeholder: String
    var you = "You"
    var them = "Cosmo"
    var send: (String) -> Void
    @State private var draft = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            if messages.isEmpty {
                Spacer()
                Text(empty).font(.system(size: 24, weight: .semibold)).foregroundStyle(.secondary).padding(.bottom, 18)
                composer
                Spacer()
                Spacer()
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 18) {
                            ForEach(messages) { m in Bubble(message: m, you: you, them: them).id(m.id) }
                        }
                        .padding(.horizontal, 28)
                        .padding(.vertical, 22)
                        .frame(maxWidth: 860)
                        .frame(maxWidth: .infinity)
                    }
                    .defaultScrollAnchor(.bottom)
                    .onChange(of: messages.last.map { "\($0.id)·\($0.text.count)·\($0.tools.count)" }) { _, _ in
                        if let id = messages.last?.id { withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(id, anchor: .bottom) } }
                    }
                }
                composer.padding(.bottom, 16)
            }
        }
        .onAppear { focused = true }
    }

    private var composer: some View {
        HStack(alignment: .center, spacing: 10) {
            TextField(placeholder, text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .lineLimit(1...6)
                .focused($focused)
                .onSubmit(submit)
            Button(action: submit) {
                Image(systemName: "arrow.up").font(.system(size: 13, weight: .bold))
                    .frame(width: 26, height: 26)
            }
            .buttonStyle(.glassProminent)
            .buttonBorderShape(.circle)
            .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(.leading, 18).padding(.trailing, 8).padding(.vertical, 9)
        .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .frame(maxWidth: 820)
        .padding(.horizontal, 28)
    }

    private func submit() {
        let t = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        send(t)
        draft = ""
    }
}

struct Bubble: View {
    var message: ChatMessage
    var you: String
    var them: String
    @State private var showTools = false

    var body: some View {
        if message.fromYou {
            HStack {
                Spacer(minLength: 80)
                Markdown(text: message.text)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Color.white.opacity(0.09)))
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Tile(glyph: "sparkle", colors: Tiles.claude, size: 20)
                    Text(them).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                    if let time = message.time { Text(clock(time)).font(.system(size: 11)).foregroundStyle(.tertiary) }
                }
                if !message.tools.isEmpty {
                    Button { showTools.toggle() } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "wrench.and.screwdriver").font(.system(size: 10.5))
                            Text("\(message.tools.count) tool call\(message.tools.count == 1 ? "" : "s")")
                            Image(systemName: showTools ? "chevron.up" : "chevron.down").font(.system(size: 9))
                        }
                        .font(.system(size: 11.5)).foregroundStyle(.secondary)
                        .padding(.horizontal, 10).padding(.vertical, 5)
                        .background(Capsule().fill(Color.white.opacity(0.05)))
                    }
                    .buttonStyle(.plain)
                    if showTools {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(Array(message.tools.enumerated()), id: \.offset) { _, t in
                                Text(t).font(.system(size: 11.5, design: .monospaced)).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }
                        .padding(.leading, 4)
                    }
                }
                if !message.text.isEmpty { Markdown(text: message.text) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct Markdown: View {
    var text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                if block.code {
                    Text(block.text)
                        .font(.system(size: 12, design: .monospaced))
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.black.opacity(0.3)))
                } else {
                    Text(Self.inline(block.text)).lineSpacing(3)
                }
            }
        }
        .textSelection(.enabled)
    }

    private var blocks: [(code: Bool, text: String)] {
        text.components(separatedBy: "```").enumerated().compactMap { i, part in
            var body = part
            if i % 2 == 1, let nl = body.firstIndex(of: "\n") { body = String(body[body.index(after: nl)...]) }
            body = body.trimmingCharacters(in: .newlines)
            return body.isEmpty ? nil : (i % 2 == 1, body)
        }
    }

    static func inline(_ s: String) -> AttributedString {
        let cleaned = s.replacingOccurrences(of: #"(?m)^#{1,6}\s+"#, with: "", options: .regularExpression)
        return (try? AttributedString(markdown: cleaned, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(cleaned)
    }
}
