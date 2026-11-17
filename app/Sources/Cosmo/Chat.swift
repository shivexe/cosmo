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
                  row["isCompactSummary"] as? Bool != true, row["isVisibleInTranscriptOnly"] as? Bool != true,
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
    private let column: CGFloat = 720

    var body: some View {
        VStack(spacing: 0) {
            if messages.isEmpty {
                Spacer()
                Text(empty).font(.title).foregroundStyle(.secondary).padding(.bottom, 20)
                composer
                Spacer()
                Spacer()
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 24) {
                            ForEach(messages) { m in Bubble(message: m, you: you, them: them).id(m.id) }
                        }
                        .frame(maxWidth: column)
                        .padding(.horizontal, 24)
                        .padding(.vertical, 24)
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
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(placeholder, text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...8)
                .focused($focused)
                .onSubmit(submit)
                .padding(.vertical, 6)
            Button(action: submit) {
                Image(systemName: "arrow.up").fontWeight(.semibold)
                    .frame(width: 20, height: 20)
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.circle)
            .help("Send (Return)")
            .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(.leading, 16).padding(.trailing, 8).padding(.vertical, 8)
        .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .frame(maxWidth: column)
        .padding(.horizontal, 24)
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
                Spacer(minLength: 96)
                Markdown(text: message.text)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
            .help(message.time.map { "\(you), \(clock($0))" } ?? you)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 4) {
                    Image(systemName: "sparkle").foregroundStyle(Color(hex: 0xD97757))
                    Text(them).fontWeight(.semibold).foregroundStyle(.secondary)
                    if let time = message.time { Text(clock(time)).monospacedDigit().foregroundStyle(.tertiary) }
                }
                .font(.subheadline)
                if !message.tools.isEmpty { tools }
                if !message.text.isEmpty { Markdown(text: message.text) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var tools: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button { withAnimation(.snappy(duration: 0.2)) { showTools.toggle() } } label: {
                HStack(spacing: 4) {
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.semibold))
                        .rotationEffect(.degrees(showTools ? 90 : 0))
                        .frame(width: 12)
                    Text("Used \(message.tools.count) tool\(message.tools.count == 1 ? "" : "s")")
                }
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(showTools ? "Hide tool calls" : "Show tool calls")
            if showTools {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(message.tools.enumerated()), id: \.offset) { _, t in
                        Label {
                            Text(t).lineLimit(1).truncationMode(.middle).help(t)
                        } icon: {
                            Image(systemName: "wrench.and.screwdriver").foregroundStyle(.tertiary)
                        }
                        .font(.subheadline.monospaced())
                        .foregroundStyle(.secondary)
                    }
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.quinary, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
        }
    }
}

struct Markdown: View {
    var text: String

    enum Block {
        case paragraph(String), heading(Int, String), bullet(Int, String), numbered(String, Int, String)
        case quote(String), code(String), table([[String]]), rule
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(Self.parse(text).enumerated()), id: \.offset) { _, block in view(block) }
        }
        .textSelection(.enabled)
    }

    @ViewBuilder private func view(_ block: Block) -> some View {
        switch block {
        case .paragraph(let t):
            Text(Self.inline(t)).lineSpacing(4).fixedSize(horizontal: false, vertical: true)
        case .heading(let level, let t):
            Text(Self.inline(t)).font(level == 1 ? .title2.weight(.semibold) : level == 2 ? .title3.weight(.semibold) : .headline).padding(.top, 8)
        case .bullet(let depth, let t):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(depth == 0 ? "•" : "◦").foregroundStyle(.secondary).frame(width: 12)
                Text(Self.inline(t)).lineSpacing(4).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 20)
        case .numbered(let n, let depth, let t):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(n).monospacedDigit().foregroundStyle(.secondary).frame(minWidth: 16, alignment: .trailing)
                Text(Self.inline(t)).lineSpacing(4).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 20)
        case .quote(let t):
            HStack(spacing: 12) {
                RoundedRectangle(cornerRadius: 1.5).fill(.tertiary).frame(width: 3)
                Text(Self.inline(t)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        case .code(let t):
            ScrollView(.horizontal) {
                Text(t).font(.callout.monospaced()).lineSpacing(2).padding(12)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(.quaternary))
        case .table(let rows):
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                ForEach(Array(rows.enumerated()), id: \.offset) { i, row in
                    GridRow {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                            Text(Self.inline(cell))
                                .fontWeight(i == 0 ? .semibold : .regular)
                                .foregroundStyle(i == 0 ? .secondary : .primary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if i < rows.count - 1 { Divider().gridCellUnsizedAxes(.horizontal) }
                }
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(.quaternary))
        case .rule:
            Divider().padding(.vertical, 4)
        }
    }

    static func parse(_ text: String) -> [Block] {
        var blocks: [Block] = []
        var paragraph: [String] = []
        var table: [[String]] = []
        var code: [String]?
        func flush() {
            if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: "\n"))); paragraph = [] }
            if !table.isEmpty { blocks.append(.table(table)); table = [] }
        }
        for raw in text.components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("```") {
                if let lines = code { blocks.append(.code(lines.joined(separator: "\n"))); code = nil } else { flush(); code = [] }
                continue
            }
            if code != nil { code?.append(raw); continue }
            let indent = (raw.prefix { $0 == " " }.count) / 2
            if line.hasPrefix("|") {
                if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: "\n"))); paragraph = [] }
                let cells = line.trimmingCharacters(in: CharacterSet(charactersIn: "|")).components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
                if !cells.allSatisfy({ $0.range(of: #"^:?-{2,}:?$"#, options: .regularExpression) != nil }) { table.append(cells) }
                continue
            }
            if line.isEmpty { flush(); continue }
            if let m = line.range(of: #"^#{1,6}\s+"#, options: .regularExpression) {
                flush(); blocks.append(.heading(line[m].filter { $0 == "#" }.count, String(line[m.upperBound...])))
            } else if line.range(of: #"^(-{3,}|\*{3,}|_{3,})$"#, options: .regularExpression) != nil {
                flush(); blocks.append(.rule)
            } else if let m = line.range(of: #"^[-*•+]\s+"#, options: .regularExpression) {
                flush(); blocks.append(.bullet(indent, String(line[m.upperBound...])))
            } else if let m = line.range(of: #"^\d+[.)]\s+"#, options: .regularExpression) {
                flush(); blocks.append(.numbered(line[m].trimmingCharacters(in: .whitespaces), indent, String(line[m.upperBound...])))
            } else if line.hasPrefix(">") {
                flush(); blocks.append(.quote(line.drop { $0 == ">" || $0 == " " }.description))
            } else {
                if !table.isEmpty { blocks.append(.table(table)); table = [] }
                paragraph.append(line)
            }
        }
        if let lines = code { blocks.append(.code(lines.joined(separator: "\n"))) }
        flush()
        return blocks
    }

    static func inline(_ s: String) -> AttributedString {
        (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(s)
    }
}
