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

// MARK: Now

struct NowView: View {
    var store: EventStore
    @State private var brain = Transcript()

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Orb(state: store.state, size: 44)
                VStack(alignment: .leading, spacing: 2) {
                    Text(store.state == .idle && store.headline == "Working" ? "Working" : store.state.title).font(.subheadline).foregroundStyle(.secondary)
                    Text(store.headline).font(.title2.weight(.semibold)).lineLimit(2)
                }
                Spacer(minLength: 16)
                if store.state == .offline && !store.starting {
                    Button("Start Cosmo") { store.startCosmo() }.buttonStyle(.borderedProminent)
                } else if store.state != .idle && store.state != .offline || !store.runningWorkers.isEmpty {
                    Button("Stop Everything") { store.stop() }
                        .buttonStyle(.bordered)
                        .keyboardShortcut(.escape, modifiers: [])
                        .help("Stop everything Cosmo is doing (Escape)")
                }
            }
            .padding(.horizontal, 20).padding(.vertical, 16)
            if store.missingPermission || store.approval != nil {
                VStack(spacing: 8) {
                    if store.missingPermission { PermissionBanner(store: store) }
                    if let approval = store.approval {
                        Notice(symbol: "hand.raised.fill", tint: .orange, title: "Needs your OK", detail: approval) {
                            Button("Deny") { store.approve(false) }
                            Button("Allow") { store.approve(true) }.buttonStyle(.borderedProminent)
                        }
                    }
                }
                .padding(.horizontal, 20).padding(.bottom, 16)
            }
            Divider()
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
        Notice(symbol: "lock.shield.fill", tint: .red, title: "Hotkeys and dictation are off",
               detail: "Turn on Cosmo in Accessibility and Input Monitoring, then restart Cosmo.") {
            Button("Accessibility…") { open("Privacy_Accessibility") }
            Button("Input Monitoring…") { open("Privacy_ListenEvent") }
            Button("Restart Cosmo") { store.restartCosmo() }.buttonStyle(.borderedProminent)
        }
    }

    private func open(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") { NSWorkspace.shared.open(url) }
    }
}

struct Notice<Actions: View>: View {
    var symbol: String
    var tint: Color
    var title: String
    var detail: String
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(tint)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.headline)
                    Text(detail).foregroundStyle(.secondary)
                }
                HStack(spacing: 8) { actions }.buttonStyle(.bordered)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(tint.opacity(0.3)))
    }
}

// MARK: Activity

struct ActivityView: View {
    var store: EventStore
    @State private var filter: ActivityItem.Kind?

    private var items: [ActivityItem] { store.activity.filter { filter == nil || $0.kind == filter }.suffix(300).reversed() }

    var body: some View {
        Group {
            if items.isEmpty {
                ContentUnavailableView("No activity yet", systemImage: "clock", description: Text("What Cosmo hears, does and runs shows up here."))
            } else {
                Table(items) {
                    TableColumn("Time") { Text(clock($0.time)).monospacedDigit().foregroundStyle(.secondary) }.width(56)
                    TableColumn("Type") { Text($0.kind.rawValue).foregroundStyle(.secondary) }.width(110)
                    TableColumn("Event") { Text($0.text).lineLimit(2) }
                }
            }
        }
        .toolbar {
            Menu {
                Picker("Show", selection: $filter) {
                    Text("Everything").tag(ActivityItem.Kind?.none)
                    ForEach(ActivityItem.Kind.allCases, id: \.self) { Text($0.rawValue).tag(Optional($0)) }
                }
                .pickerStyle(.inline)
            } label: {
                Label(filter?.rawValue ?? "Everything", systemImage: "line.3.horizontal.decrease.circle")
                    .labelStyle(.titleAndIcon)
            }
            .help("Filter the activity")
        }
    }
}

// MARK: Workers

struct WorkersView: View {
    var store: EventStore
    var body: some View {
        if store.workers.isEmpty {
            ContentUnavailableView("No tasks yet", systemImage: "square.stack.3d.up", description: Text("What you ask Cosmo to do shows up here."))
        } else {
            List {
                if !store.runningWorkers.isEmpty {
                    Section("Running") { ForEach(store.runningWorkers) { WorkerRow(worker: $0) } }
                }
                if !store.doneWorkers.isEmpty {
                    Section("Finished") {
                        ForEach(store.doneWorkers.prefix(50)) { WorkerRow(worker: $0) }
                    }
                }
            }
            .listStyle(.inset)
        }
    }
}

struct WorkerRow: View {
    var worker: Worker
    private var icon: (String, Color) {
        if worker.failed { return ("exclamationmark.triangle.fill", .red) }
        if worker.status == "stopped" { return ("stop.circle", .secondary) }
        return ("checkmark.circle.fill", .green)
    }
    var body: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(worker.steps.enumerated()), id: \.offset) { _, step in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(clock(step.0)).monospacedDigit().foregroundStyle(.tertiary)
                        Text(step.1).foregroundStyle(.secondary)
                    }
                    .font(.callout)
                }
                if !worker.summary.isEmpty {
                    Text(worker.summary).textSelection(.enabled).fixedSize(horizontal: false, vertical: true).padding(.top, 4)
                }
            }
            .padding(.leading, 24).padding(.vertical, 4)
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Group {
                    if worker.running {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: icon.0).foregroundStyle(icon.1)
                    }
                }
                .frame(width: 16)
                VStack(alignment: .leading, spacing: 2) {
                    Text(worker.title).lineLimit(1)
                    Text(plain(worker.lastStep)).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 8)
                Text(elapsed(worker.started, worker.ended ?? Date())).font(.callout).monospacedDigit().foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: Devices

struct DevicesView: View {
    var store: EventStore
    @State private var config = ConfigFile()
    @State private var editing: HostConfig?

    var body: some View {
        Form {
            Section {
                LabeledContent {
                    status(store.state != .offline)
                } label: {
                    Label {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("This Mac")
                            Text(store.state == .offline ? "Cosmo isn’t running" : "Cosmo runs here").font(.callout).foregroundStyle(.secondary)
                        }
                    } icon: { icon("laptopcomputer") }
                }
                if store.hosts.isEmpty {
                    Text("No other machines. Click + to add a server, laptop or phone.").foregroundStyle(.secondary)
                }
                ForEach(store.hosts) { h in
                    LabeledContent {
                        status(h.online)
                    } label: {
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(h.label)
                                Text(h.online ? "Cosmo can run commands here" : h.kind == "adb" ? "Not connected over adb" : "Not reachable")
                                    .font(.callout).foregroundStyle(.secondary)
                            }
                        } icon: { icon(h.kind == "adb" ? "iphone" : "server.rack") }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture(count: 2) { editing = host(h.name) }
                    .contextMenu {
                        Button("Edit…") { editing = host(h.name) }
                        Button("Remove", role: .destructive) { remove(h.name) }
                    }
                }
            } header: {
                Text("Machines")
            } footer: {
                Text("Cosmo checks each machine every minute. Double-click one to edit it, or click + to add one.").foregroundStyle(.secondary)
            }
            PhoneSection(store: store)
        }
        .formStyle(.grouped)
        .toolbar {
            Button { editing = HostConfig(name: "", label: "", kind: "ssh", target: "", shell: "bash") } label: { Label("Add Machine", systemImage: "plus") }
                .help("Add a machine")
        }
        .sheet(item: $editing) { h in HostEditor(host: h, config: config) }
    }

    private func status(_ online: Bool) -> some View {
        HStack(spacing: 6) {
            Circle().fill(online ? Color.green : Color.secondary).frame(width: 7, height: 7)
            Text(online ? "Online" : "Offline").foregroundStyle(.secondary)
        }
    }

    private func icon(_ name: String) -> some View {
        Image(systemName: name).frame(width: 20)
    }

    private func host(_ name: String) -> HostConfig? {
        config.load()
        guard let h = (config.values["hosts"] as? [String: [String: Any]])?[name] else { return nil }
        return HostConfig(name: name, label: h["label"] as? String ?? name, kind: h["kind"] as? String ?? "ssh",
                          target: h["target"] as? String ?? "", shell: h["shell"] as? String ?? "")
    }

    private func remove(_ name: String) {
        config.load()
        var dict = config.values["hosts"] as? [String: Any] ?? [:]
        dict.removeValue(forKey: name)
        config.set("hosts", dict)
        AppDelegate.store.hostsChanged(dict)
    }
}

struct HostEditor: View {
    @State var host: HostConfig
    var config: ConfigFile
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            TextField("Name", text: $host.label, prompt: Text("Linux server"))
            Picker("Type", selection: $host.kind) {
                Text("SSH").tag("ssh")
                Text("Android over adb").tag("adb")
            }
            TextField(host.kind == "adb" ? "Serial" : "SSH host", text: $host.target,
                      prompt: Text(host.kind == "adb" ? "Optional, from adb devices" : "user@address or a ~/.ssh/config name"))
            if host.kind == "ssh" {
                TextField("Shell", text: $host.shell, prompt: Text("bash"))
            }
        }
        .formStyle(.grouped)
        .fitsContent()
        .frame(width: 440)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") { save(); dismiss() }.disabled(host.label.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
    }

    private func save() {
        config.load()
        var dict = config.values["hosts"] as? [String: Any] ?? [:]
        var name = host.name
        if name.isEmpty {
            name = host.label.lowercased().replacingOccurrences(of: #"[^a-z0-9]+"#, with: "-", options: .regularExpression)
            while dict[name] != nil { name += "-2" }
        }
        var entry: [String: Any] = ["label": host.label, "kind": host.kind, "target": host.target]
        if host.kind == "ssh" { entry["shell"] = host.shell.isEmpty ? "bash" : host.shell }
        dict[name] = entry
        config.set("hosts", dict)
        AppDelegate.store.hostsChanged(dict)
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
            List(store.sessions, selection: $selected) { s in
                SessionItem(session: s).tag(s.id)
            }
            .listStyle(.inset)
            .overlay { if store.sessions.isEmpty { ContentUnavailableView("No Live Sessions", systemImage: "terminal", description: Text("Claude sessions running on this Mac show up here.")) } }
            .frame(width: 280)
            Divider()
            Group {
                if let s = current {
                    VStack(spacing: 0) {
                        HStack(spacing: 8) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(s.name).font(.headline).lineLimit(1)
                                Text(URL(fileURLWithPath: s.folder).lastPathComponent).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer(minLength: 8)
                            Label(s.status.capitalized, systemImage: s.status == "busy" ? "circle.fill" : "circle")
                                .font(.callout)
                                .imageScale(.small)
                                .foregroundStyle(s.status == "busy" ? Color.accentColor : .secondary)
                        }
                        .padding(.horizontal, 24).padding(.vertical, 12)
                        Divider()
                        if s.transcript == nil {
                            ContentUnavailableView("No Messages Loaded", systemImage: "arrow.clockwise",
                                                   description: Text("Restart Cosmo once to load this session’s messages."))
                        } else {
                            ChatView(messages: transcript.messages,
                                     empty: transcript.loaded ? "No messages yet" : "Loading…",
                                     placeholder: "Message \(s.name) as you", them: "Claude") { text in
                                store.send(["cmd": "send", "name": s.name, "text": text])
                            }
                        }
                    }
                } else {
                    ContentUnavailableView("Pick a Session", systemImage: "bubble.left.and.bubble.right", description: Text("Its conversation shows here."))
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
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(session.name).font(.headline).lineLimit(1)
                Spacer(minLength: 8)
                if session.status == "busy" {
                    Image(systemName: "circle.fill").font(.system(size: 6)).foregroundStyle(Color.accentColor)
                        .accessibilityLabel("Busy")
                }
                Text(ago(session.updated / 1000)).font(.subheadline).monospacedDigit().foregroundStyle(.secondary)
            }
            Text(plain(session.last.isEmpty ? session.topic : session.last)).font(.callout).foregroundStyle(.secondary).lineLimit(2)
            Text(URL(fileURLWithPath: session.folder).lastPathComponent).font(.caption).foregroundStyle(.tertiary).lineLimit(1)
        }
        .padding(.vertical, 4)
    }
}

func ago(_ t: Double) -> String {
    let s = Int(max(0, Date().timeIntervalSince1970 - t))
    return s < 60 ? "now" : s < 3600 ? "\(s / 60)m" : s < 86400 ? "\(s / 3600)h" : "\(s / 86400)d"
}

func plain(_ s: String) -> String {
    s.replacingOccurrences(of: #"(?m)\*\*|__|`|^#+\s*|^\s*[-*]\s+"#, with: "", options: .regularExpression)
}

// MARK: Dictation

struct DictationView: View {
    var store: EventStore
    @State private var config = ConfigFile()
    @State private var words: [String] = []
    @State private var fixes: [(said: String, meant: String)] = []
    @State private var newWord = ""
    @State private var newSaid = ""
    @State private var newMeant = ""
    @State private var editing: UUID?
    @State private var draft = ""
    @State private var learned = ""
    @State private var kept: [String] = []

    var body: some View {
        Form {
            Section {
                KeyRecorder(title: "Shortcut", key: "dictation_key", fallback: "cmd_r", config: config)
                Toggle("Double-tap to keep it on", isOn: Binding(
                    get: { config.values["dictation_double_tap"] as? Bool ?? true },
                    set: { config.set("dictation_double_tap", $0); AppDelegate.store.restartCosmo() }))
            } footer: {
                Text("Hold the shortcut and talk, then let go. Or double-tap it, talk, and tap once to stop. Cosmo types into whatever app you’re in.")
                    .foregroundStyle(.secondary)
            }

            Section {
                if !words.isEmpty {
                    FlowLayout(spacing: 6) {
                        ForEach(words, id: \.self) { w in
                            HStack(spacing: 4) {
                                Text(w)
                                Button { words.removeAll { $0 == w }; saveWords() } label: { Image(systemName: "xmark.circle.fill") }
                                    .buttonStyle(.borderless).foregroundStyle(.tertiary).help("Remove \(w)")
                            }
                            .padding(.leading, 9).padding(.trailing, 5).padding(.vertical, 3)
                            .background(Capsule().fill(.quaternary))
                        }
                    }
                    .padding(.vertical, 4)
                }
                TextField("Add a word", text: $newWord, prompt: Text("A name, tool or term")).onSubmit(addWord)
            } header: {
                Text("Words you use")
            } footer: {
                Text("Cosmo hints these to the speech model so it spells them right.").foregroundStyle(.secondary)
            }

            Section {
                ForEach(Array(fixes.enumerated()), id: \.offset) { i, f in
                    LabeledContent {
                        HStack {
                            Text(f.meant)
                            Button { fixes.remove(at: i); saveFixes() } label: { Image(systemName: "minus.circle") }
                                .buttonStyle(.borderless).help("Remove")
                        }
                    } label: {
                        Text(f.said).foregroundStyle(.secondary)
                    }
                }
                HStack {
                    TextField("Heard", text: $newSaid, prompt: Text("Heard")).labelsHidden()
                    Image(systemName: "arrow.right").foregroundStyle(.tertiary)
                    TextField("Meant", text: $newMeant, prompt: Text("Meant")).labelsHidden().onSubmit(addFix)
                    Button("Add", action: addFix)
                        .disabled(newSaid.trimmingCharacters(in: .whitespaces).isEmpty || newMeant.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            } header: {
                Text("Corrections")
            } footer: {
                Text("When the speech model writes the left side, Cosmo types the right side.").foregroundStyle(.secondary)
            }

            Section {
                if store.dictations.isEmpty { Text("Nothing dictated yet.").foregroundStyle(.secondary) }
                ForEach(store.dictations.suffix(10).reversed()) { line in
                    DictationRow(line: line, editing: editing == line.id, draft: $draft,
                                 onFix: { editing = line.id; draft = line.text },
                                 onCancel: { editing = nil },
                                 onLearn: { learn(from: line.text) })
                }
            } header: {
                Text("Recent")
            } footer: {
                Text(learned.isEmpty ? "Hover a line and click Fix to teach Cosmo what you said." : learned).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .onAppear(perform: load)
    }

    private static let wordsURL = EventStore.home.appendingPathComponent("words.txt")
    private static let fixesURL = EventStore.home.appendingPathComponent("dictionary.txt")

    private func load() {
        var seen = Set<String>()
        words = ((try? String(contentsOf: Self.wordsURL, encoding: .utf8)) ?? "").split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }
        fixes = []
        kept = []
        for raw in ((try? String(contentsOf: Self.fixesURL, encoding: .utf8)) ?? "").components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("# one per line") { continue }
            if !line.hasPrefix("#"), let range = line.range(of: "=>") ?? line.range(of: "\t") {
                let said = line[..<range.lowerBound].trimmingCharacters(in: .whitespaces)
                let meant = line[range.upperBound...].trimmingCharacters(in: .whitespaces)
                if !said.isEmpty && !meant.isEmpty { fixes.append((said, meant)); continue }
            }
            kept.append(raw)
        }
    }

    private func saveWords() { try? (words.joined(separator: "\n") + "\n").write(to: Self.wordsURL, atomically: true, encoding: .utf8) }
    private func saveFixes() {
        let lines = ["# one per line: what Whisper writes => what you want"] + kept + fixes.map { "\($0.said) => \($0.meant)" }
        let body = lines.joined(separator: "\n") + "\n"
        try? body.write(to: Self.fixesURL, atomically: true, encoding: .utf8)
    }

    private func addWord() {
        let w = newWord.trimmingCharacters(in: .whitespaces)
        guard !w.isEmpty else { return }
        if !words.contains(where: { $0.caseInsensitiveCompare(w) == .orderedSame }) { words.append(w); saveWords() }
        newWord = ""
    }

    private func addFix() {
        let said = newSaid.trimmingCharacters(in: .whitespaces), meant = newMeant.trimmingCharacters(in: .whitespaces)
        guard !said.isEmpty, !meant.isEmpty else { return }
        fixes.removeAll { $0.said.caseInsensitiveCompare(said) == .orderedSame }
        fixes.append((said, meant))
        saveFixes()
        newSaid = ""; newMeant = ""
    }

    private func learn(from original: String) {
        let change = Self.changedSpan(original, draft)
        editing = nil
        guard let change else { learned = "No word changed, nothing to learn."; return }
        fixes.removeAll { $0.said.caseInsensitiveCompare(change.said) == .orderedSame }
        fixes.append((change.said, change.meant))
        saveFixes()
        for (i, w) in change.meant.split(separator: " ").map(String.init).enumerated()
        where w.count > 2 && w != w.lowercased() && !(change.start == 0 && i == 0) && !words.contains(where: { $0.caseInsensitiveCompare(w) == .orderedSame }) {
            words.append(w)
        }
        saveWords()
        learned = "Learned: “\(change.said)” → “\(change.meant)”. Next time Cosmo types it your way."
    }

    static func changedSpan(_ a: String, _ b: String) -> (said: String, meant: String, start: Int)? {
        let clean: (String) -> [String] = { $0.split(separator: " ").map { $0.trimmingCharacters(in: .punctuationCharacters) }.filter { !$0.isEmpty } }
        let x = clean(a), y = clean(b)
        let same: (Int, Int) -> Bool = { i, j in i == 0 && j == 0 ? x[i].lowercased() == y[j].lowercased() : x[i] == y[j] }
        var start = 0
        while start < min(x.count, y.count), same(start, start) { start += 1 }
        var end = 0
        while end < min(x.count, y.count) - start, same(x.count - 1 - end, y.count - 1 - end) { end += 1 }
        let said = x[start..<(x.count - end)].joined(separator: " "), meant = y[start..<(y.count - end)].joined(separator: " ")
        guard !said.isEmpty, !meant.isEmpty, x.count - end - start <= 4 else { return nil }
        return (said, meant, start)
    }
}

struct DictationRow: View {
    var line: Line
    var editing: Bool
    @Binding var draft: String
    var onFix: () -> Void
    var onCancel: () -> Void
    var onLearn: () -> Void
    @State private var hover = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 14) {
                Text(clock(line.time)).monospacedDigit().foregroundStyle(.tertiary).frame(width: 44, alignment: .leading)
                Text(line.text).textSelection(.enabled)
                Spacer()
                if hover && !editing {
                    Button("Fix", action: onFix).controlSize(.small)
                    Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(line.text, forType: .string) } label: {
                        Image(systemName: "doc.on.doc")
                    }
                    .buttonStyle(.plain).foregroundStyle(.secondary).help("Copy")
                }
            }
            if editing {
                HStack {
                    TextField("What you actually said", text: $draft).textFieldStyle(.roundedBorder).onSubmit(onLearn)
                    Button("Cancel", action: onCancel).controlSize(.small)
                    Button("Teach Cosmo", action: onLearn).controlSize(.small).keyboardShortcut(.defaultAction)
                }
                .padding(.leading, 58)
            }
        }
        .contentShape(Rectangle())
        .onHover { hover = $0 }
    }
}

struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 400
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x + s.width > width, x > 0 { x = 0; y += row + spacing; row = 0 }
            x += s.width + spacing; row = max(row, s.height)
        }
        return CGSize(width: width, height: y + row)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x + s.width > bounds.maxX, x > bounds.minX { x = bounds.minX; y += row + spacing; row = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += s.width + spacing; row = max(row, s.height)
        }
    }
}
