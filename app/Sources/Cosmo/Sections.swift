import SwiftUI

private let timeFormat: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "HH:mm"
    return f
}()

func clock(_ d: Date) -> String { timeFormat.string(from: d) }

func elapsed(_ from: Date, _ to: Date = Date()) -> String {
    let s = Int(max(0, to.timeIntervalSince(from)))
    return s < 60 ? "\(s)s" : s < 3600 ? "\(s / 60)m" : "\(s / 3600)h \((s % 3600) / 60)m"
}

struct Card<Content: View>: View {
    var tint: Color? = nil
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(tint.map { $0.opacity(0.1) } ?? Color.white.opacity(0.04))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .strokeBorder((tint ?? .white).opacity(tint == nil ? 0.07 : 0.3), lineWidth: 1)
            )
    }
}

struct PageHeader: View {
    var title: String
    var subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 28, weight: .semibold)).tracking(-0.5)
            Text(subtitle).foregroundStyle(.secondary)
        }
        .padding(.bottom, 6)
    }
}

struct Tile: View {
    var glyph: String
    var colors: [Color]
    var size: CGFloat = 36
    var body: some View {
        Image(systemName: glyph)
            .font(.system(size: size * 0.42, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(RoundedRectangle(cornerRadius: size * 0.27, style: .continuous)
                .fill(LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)))
    }
}

enum Tiles {
    static let worker = [Color(hex: 0x4C7DFF), Color(hex: 0x2B4BB8)]
    static let done = [Color(hex: 0x3E8E5E), Color(hex: 0x24573A)]
    static let failed = [Color(hex: 0xD9534F), Color(hex: 0x8F2F2C)]
    static let claude = [Color(hex: 0xD97757), Color(hex: 0xA8553A)]
    static let amber = [Color(hex: 0xF7B955), Color(hex: 0xC9862B)]
    static let voice = [Color(hex: 0x8B6CFF), Color(hex: 0x5B3FD0)]
    static let grey = [Color(hex: 0x4A5568), Color(hex: 0x2D3748)]
}

struct Row<Trailing: View>: View {
    var glyph: String
    var colors: [Color]
    var title: String
    var subtitle: String
    @ViewBuilder var trailing: Trailing
    var body: some View {
        HStack(spacing: 14) {
            Tile(glyph: glyph, colors: colors)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).fontWeight(.semibold).lineLimit(2)
                if !subtitle.isEmpty { Text(subtitle).font(.system(size: 12.5)).foregroundStyle(.secondary).lineLimit(2) }
            }
            Spacer(minLength: 12)
            trailing
        }
        .padding(12)
    }
}

struct EmptyNote: View {
    var icon: String
    var text: String
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon).foregroundStyle(.secondary)
            Text(text).foregroundStyle(.secondary)
        }
        .padding(18)
    }
}

// MARK: Now

struct NowView: View {
    var store: EventStore
    @State private var brain = Transcript()

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 16) {
                Orb(state: store.state, size: 46)
                VStack(alignment: .leading, spacing: 3) {
                    Text(store.state.title).font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                    Text(store.headline).font(.system(size: 19, weight: .semibold)).lineLimit(2)
                }
                Spacer(minLength: 16)
                if store.state == .offline && !store.starting {
                    Button("Start Cosmo") { store.startCosmo() }.buttonStyle(.glassProminent)
                } else if store.state != .idle && store.state != .offline || !store.runningWorkers.isEmpty {
                    Button("Stop everything") { store.stop() }
                        .buttonStyle(.glass)
                        .keyboardShortcut(.escape, modifiers: [])
                }
            }
            .padding(.horizontal, 30).padding(.top, 22).padding(.bottom, 14)
            VStack(spacing: 10) {
                if store.missingPermission { PermissionBanner(store: store) }
                if let approval = store.approval {
                    Card(tint: Color(hex: 0xF7B955)) {
                        HStack(spacing: 12) {
                            Tile(glyph: "hand.raised.fill", colors: Tiles.amber, size: 32)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Needs your OK").fontWeight(.semibold)
                                Text(approval).font(.system(size: 12.5)).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Allow") { store.approve(true) }.buttonStyle(.glassProminent).tint(Color(hex: 0xF7B955))
                            Button("Deny") { store.approve(false) }.buttonStyle(.glass)
                        }
                        .padding(14)
                    }
                }
            }
            .padding(.horizontal, 28)
            Divider().opacity(0.4).padding(.top, 6)
            ChatView(messages: brain.messages,
                     empty: store.brainTranscript != nil && !brain.loaded ? "Loading…" : "What can I do for you?",
                     placeholder: "Ask Cosmo to do something") { store.ask($0) }
        }
        .onChange(of: store.brainTranscript, initial: true) { _, path in brain.open(path) }
        .onDisappear { brain.close() }
    }
}

struct PermissionBanner: View {
    var store: EventStore
    var body: some View {
        Card(tint: Color(hex: 0xD9534F)) {
            HStack(spacing: 12) {
                Tile(glyph: "lock.shield.fill", colors: Tiles.failed, size: 32)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Hotkeys and dictation are off").fontWeight(.semibold)
                    Text("Turn on Cosmo in Accessibility and Input Monitoring, then restart Cosmo.")
                        .font(.system(size: 12.5)).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Accessibility") { open("Privacy_Accessibility") }.buttonStyle(.glass)
                Button("Input Monitoring") { open("Privacy_ListenEvent") }.buttonStyle(.glass)
                Button("Restart Cosmo") { store.restartCosmo() }.buttonStyle(.glassProminent)
            }
            .padding(14)
        }
    }

    private func open(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") { NSWorkspace.shared.open(url) }
    }
}

// MARK: Inbox

struct InboxView: View {
    var store: EventStore
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: "Inbox", subtitle: "Only what needs you")
            Card {
                if let approval = store.approval {
                    Row(glyph: "hand.raised.fill", colors: Tiles.amber, title: "Should I \(approval)?", subtitle: "Waiting for your answer") {
                        HStack(spacing: 8) {
                            Button("Allow") { store.approve(true) }.buttonStyle(.glassProminent).tint(Color(hex: 0xF7B955))
                            Button("Deny") { store.approve(false) }.buttonStyle(.glass)
                        }
                    }
                }
                ForEach(store.workers.filter(\.failed).reversed()) { w in
                    Row(glyph: "exclamationmark.triangle.fill", colors: Tiles.failed, title: w.title, subtitle: w.summary.isEmpty ? "Failed" : w.summary) {
                        Button("Retry") { store.ask("Retry this: \(w.title)") }.buttonStyle(.glass)
                    }
                }
                if store.approval == nil && !store.workers.contains(where: \.failed) {
                    EmptyNote(icon: "checkmark.seal", text: "Nothing needs you right now")
                }
            }
        }
    }
}

// MARK: Activity

struct ActivityView: View {
    var store: EventStore
    @State private var filter: ActivityItem.Kind?
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .bottom) {
                PageHeader(title: "Activity", subtitle: "Everything Cosmo did")
                Spacer()
                Picker("Show", selection: $filter) {
                    Text("All").tag(ActivityItem.Kind?.none)
                    ForEach(ActivityItem.Kind.allCases, id: \.self) { Text($0.rawValue).tag(Optional($0)) }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 520)
            }
            Card {
                let items = store.activity.filter { filter == nil || $0.kind == filter }.suffix(200).reversed()
                if items.isEmpty { EmptyNote(icon: "clock", text: "No activity yet") }
                ForEach(Array(items)) { item in
                    HStack(spacing: 14) {
                        Text(clock(item.time)).font(.system(size: 12)).monospacedDigit().foregroundStyle(.secondary).frame(width: 44, alignment: .leading)
                        Text(item.kind.rawValue).font(.system(size: 12)).foregroundStyle(.secondary).frame(width: 100, alignment: .leading)
                        Text(item.text).lineLimit(2)
                        Spacer()
                    }
                    .padding(.horizontal, 16).padding(.vertical, 9)
                }
            }
        }
    }
}

// MARK: Workers

struct WorkersView: View {
    var store: EventStore
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: "Workers", subtitle: "Background jobs Cosmo started for you")
            Text("Running · \(store.runningWorkers.count)").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
            Card {
                if store.runningWorkers.isEmpty { EmptyNote(icon: "checkmark.circle", text: "Nothing running") }
                ForEach(store.runningWorkers) { w in WorkerRow(worker: w) }
            }
            Text("Done · \(store.doneWorkers.count)").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
            Card {
                if store.doneWorkers.isEmpty { EmptyNote(icon: "tray", text: "No finished workers yet") }
                ForEach(store.doneWorkers.prefix(50)) { w in WorkerRow(worker: w) }
            }
        }
    }
}

struct WorkerRow: View {
    var worker: Worker
    @State private var open = false
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { open.toggle() } label: {
                Row(glyph: worker.running ? "gearshape.2.fill" : worker.failed ? "exclamationmark.triangle.fill" : "checkmark",
                    colors: worker.running ? Tiles.worker : worker.failed ? Tiles.failed : Tiles.done,
                    title: worker.title, subtitle: worker.lastStep) {
                    Text(elapsed(worker.started, worker.ended ?? Date())).font(.system(size: 12)).monospacedDigit().foregroundStyle(.secondary)
                    Image(systemName: open ? "chevron.up" : "chevron.down").font(.system(size: 11)).foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(worker.steps.enumerated()), id: \.offset) { _, step in
                        HStack(spacing: 12) {
                            Text(clock(step.0)).font(.system(size: 11.5)).monospacedDigit().foregroundStyle(.tertiary)
                            Text(step.1).font(.system(size: 12.5)).foregroundStyle(.secondary)
                        }
                    }
                    if !worker.summary.isEmpty {
                        Text(worker.summary).font(.system(size: 12.5)).padding(.top, 4)
                    }
                }
                .padding(.leading, 62).padding(.bottom, 14).padding(.trailing, 14)
            }
        }
    }
}

// MARK: Devices

struct DevicesView: View {
    var store: EventStore
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: "Devices", subtitle: "Where Cosmo can work")
            Card {
                Row(glyph: "laptopcomputer", colors: Tiles.worker, title: "This Mac",
                    subtitle: store.state == .offline ? "Cosmo isn’t running" : "Cosmo is running · \(store.state.title.lowercased())") {
                    Text(store.state == .offline ? "Offline" : "Online").font(.system(size: 12)).foregroundStyle(.secondary)
                }
            }
            Card {
                if store.hosts.isEmpty { EmptyNote(icon: "server.rack", text: "Checking your other machines…") }
                ForEach(store.hosts) { h in
                    Row(glyph: h.kind == "adb" ? "iphone.gen3" : h.name == "windows" ? "pc" : "server.rack",
                        colors: h.online ? Tiles.worker : Tiles.grey, title: h.label,
                        subtitle: h.online ? "Cosmo can run commands here" : h.kind == "adb" ? "Not connected over adb" : "Not reachable right now") {
                        VStack(alignment: .trailing, spacing: 3) {
                            Text(h.online ? "Online" : "Offline").font(.system(size: 12, weight: .medium))
                                .foregroundStyle(h.online ? Color(hex: 0xA9DBFF) : .secondary)
                            Text("checked \(clock(Date(timeIntervalSince1970: h.checked)))").font(.system(size: 11)).foregroundStyle(.tertiary)
                        }
                    }
                }
            }
        }
    }
}

// MARK: Sessions

struct SessionsView: View {
    var store: EventStore
    @State private var selected: String?
    @State private var transcript = Transcript()

    private var current: SessionRow? { store.sessions.first { $0.id == selected } }

    var body: some View {
        HStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Claude sessions").font(.system(size: 20, weight: .semibold)).padding(.horizontal, 10).padding(.bottom, 8)
                    if store.sessions.isEmpty { EmptyNote(icon: "terminal", text: "No live sessions, or Cosmo isn’t running") }
                    ForEach(store.sessions) { s in
                        Button { selected = s.id } label: { SessionItem(session: s, selected: s.id == selected) }
                            .buttonStyle(.plain)
                    }
                }
                .padding(14)
            }
            .frame(width: 300)
            Divider().opacity(0.4)
            Group {
                if let s = current {
                    VStack(spacing: 0) {
                        HStack(spacing: 10) {
                            Tile(glyph: "terminal.fill", colors: Tiles.claude, size: 26)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(s.name).fontWeight(.semibold).lineLimit(1)
                                Text(s.folder).font(.system(size: 11)).foregroundStyle(.tertiary).lineLimit(1).truncationMode(.middle)
                            }
                            Spacer()
                            Text(s.status.capitalized).font(.system(size: 12, weight: .medium))
                                .foregroundStyle(s.status == "busy" ? Color(hex: 0xA9DBFF) : .secondary)
                        }
                        .padding(.horizontal, 22).padding(.vertical, 12)
                        Divider().opacity(0.4)
                        if s.transcript == nil {
                            EmptyNote(icon: "arrow.clockwise", text: "Restart Cosmo once to load this session’s messages").frame(maxHeight: .infinity)
                        } else {
                            ChatView(messages: transcript.messages,
                                     empty: transcript.loaded ? "No messages yet" : "Loading…",
                                     placeholder: "Message \(s.name) as you", them: "Claude") { text in
                                store.send(["cmd": "send", "name": s.name, "text": text])
                            }
                        }
                    }
                } else {
                    EmptyNote(icon: "sidebar.left", text: "Pick a session to read its conversation").frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onChange(of: store.sessions.map(\.id), initial: true) { _, ids in
            if selected == nil || !ids.contains(selected!) { selected = ids.first }
        }
        .onChange(of: current?.transcript, initial: true) { _, path in transcript.open(path) }
        .onDisappear { transcript.close() }
    }
}

struct SessionItem: View {
    var session: SessionRow
    var selected: Bool
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(session.status == "busy" ? Color(hex: 0x7CC4FF) : Color.gray.opacity(0.6))
                .frame(width: 7, height: 7).padding(.top, 6)
            VStack(alignment: .leading, spacing: 3) {
                Text(session.name).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Text(plain(session.last.isEmpty ? session.topic : session.last)).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(selected ? Color.white.opacity(0.1) : .clear))
        .contentShape(Rectangle())
    }
}

func plain(_ s: String) -> String {
    s.replacingOccurrences(of: #"(?m)\*\*|__|`|^#+\s*|^\s*[-*]\s+"#, with: "", options: .regularExpression)
}

// MARK: Dictation

struct DictationView: View {
    var store: EventStore
    @State private var dictionary = ""
    @State private var saved = ""
    private static let url = EventStore.home.appendingPathComponent("dictionary.txt")

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: "Dictation", subtitle: "Hold \(KeyNames.label(ConfigFile().string("dictation_key", "cmd_r"))) and talk. Release to type it into whatever app you’re in.")
            Text("Recent · \(store.dictations.count)").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
            Card {
                if store.dictations.isEmpty { EmptyNote(icon: "text.cursor", text: "Nothing dictated yet") }
                ForEach(store.dictations.suffix(50).reversed()) { line in
                    HStack(alignment: .top, spacing: 14) {
                        Text(clock(line.time)).font(.system(size: 12)).monospacedDigit().foregroundStyle(.tertiary).frame(width: 44, alignment: .leading)
                        Text(line.text).textSelection(.enabled)
                        Spacer()
                        Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(line.text, forType: .string) } label: {
                            Image(systemName: "doc.on.doc")
                        }
                        .buttonStyle(.plain).foregroundStyle(.secondary).help("Copy")
                    }
                    .padding(.horizontal, 16).padding(.vertical, 9)
                }
            }
            HStack {
                Text("Dictionary").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                Spacer()
                Text("One per line: what Whisper writes => what you want").font(.system(size: 11.5)).foregroundStyle(.tertiary)
                Button("Save") { save() }.buttonStyle(.glass).disabled(dictionary == saved)
            }
            Card {
                TextEditor(text: $dictionary)
                    .font(.system(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 180)
                    .padding(10)
            }
        }
        .onAppear(perform: load)
    }

    private func load() {
        dictionary = (try? String(contentsOf: Self.url, encoding: .utf8)) ?? "# one per line: what Whisper writes => what you want\n"
        saved = dictionary
    }

    private func save() {
        try? dictionary.write(to: Self.url, atomically: true, encoding: .utf8)
        saved = dictionary
    }
}
