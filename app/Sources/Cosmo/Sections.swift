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
    var body: some View {
        VStack(alignment: .leading, spacing: 26) {
            HStack(spacing: 24) {
                Orb(state: store.state, size: 72)
                VStack(alignment: .leading, spacing: 6) {
                    Text(store.state.title).font(.system(size: 13, weight: .medium)).foregroundStyle(.secondary)
                    Text(store.headline).font(.system(size: 30, weight: .semibold)).tracking(-0.6).lineLimit(3)
                    if store.state == .offline && !store.starting {
                        Button("Start Cosmo") { store.startCosmo() }
                            .buttonStyle(.glassProminent)
                            .padding(.top, 6)
                    }
                }
            }
            .padding(.vertical, 6)

            HStack(alignment: .top, spacing: 20) {
                VStack(alignment: .leading, spacing: 20) {
                    if let approval = store.approval {
                        Card(tint: Color(hex: 0xF7B955)) {
                            VStack(alignment: .leading, spacing: 14) {
                                HStack(spacing: 12) {
                                    Tile(glyph: "hand.raised.fill", colors: Tiles.amber, size: 34)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text("Needs your OK").fontWeight(.semibold)
                                        Text(approval).font(.system(size: 12.5)).foregroundStyle(.secondary)
                                    }
                                }
                                HStack(spacing: 8) {
                                    Button("Allow") { store.approve(true) }.buttonStyle(.glassProminent).tint(Color(hex: 0xF7B955))
                                    Button("Deny") { store.approve(false) }.buttonStyle(.glass)
                                }
                            }
                            .padding(18)
                        }
                    }
                    Card {
                        Text("Running · \(store.runningWorkers.count)").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                            .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 4)
                        if store.runningWorkers.isEmpty {
                            EmptyNote(icon: "checkmark.circle", text: "Nothing running in the background")
                        }
                        ForEach(store.runningWorkers) { w in
                            Row(glyph: "gearshape.2.fill", colors: Tiles.worker, title: w.title, subtitle: w.lastStep) {
                                Text(elapsed(w.started)).font(.system(size: 12)).monospacedDigit().foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity)

                Card {
                    Text("Conversation").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                        .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 4)
                    if store.lines.isEmpty { EmptyNote(icon: "waveform", text: "Say “Hey Cosmo” to start") }
                    ForEach(store.lines.suffix(8)) { line in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(line.fromYou ? "You" : "Cosmo").font(.system(size: 11.5, weight: .medium)).foregroundStyle(.secondary)
                            Text(line.text).foregroundStyle(line.fromYou ? .primary : .secondary)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 7)
                    }
                    Spacer(minLength: 10)
                }
                .frame(maxWidth: 420)
            }
        }
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
            Card { EmptyNote(icon: "server.rack", text: "Your Linux server, Windows laptop and phone appear here once Cosmo can reach them (milestone M10).") }
        }
    }
}

// MARK: Sessions

struct SessionsView: View {
    var store: EventStore
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: "Claude sessions", subtitle: "Your coding sessions Cosmo can read and talk to")
            Card {
                if store.sessions.isEmpty { EmptyNote(icon: "terminal", text: "No live sessions, or Cosmo isn’t running") }
                ForEach(store.sessions) { s in
                    Row(glyph: "terminal.fill", colors: Tiles.claude, title: s.topic.isEmpty ? s.name : s.topic,
                        subtitle: s.last) {
                        VStack(alignment: .trailing, spacing: 3) {
                            Text(s.status.capitalized).font(.system(size: 12, weight: .medium))
                                .foregroundStyle(s.status == "busy" ? Color(hex: 0xA9DBFF) : .secondary)
                            Text(s.name).font(.system(size: 11.5)).foregroundStyle(.tertiary)
                        }
                    }
                }
            }
        }
    }
}

// MARK: Conversation

struct ConversationView: View {
    var store: EventStore
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: "Conversation", subtitle: "Everything you and Cosmo said")
            Card {
                if store.lines.isEmpty { EmptyNote(icon: "waveform", text: "Nothing said yet") }
                ForEach(store.lines.suffix(300)) { line in
                    HStack(alignment: .top, spacing: 14) {
                        Text(clock(line.time)).font(.system(size: 12)).monospacedDigit().foregroundStyle(.tertiary).frame(width: 44, alignment: .leading)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(line.fromYou ? "You" : "Cosmo").font(.system(size: 11.5, weight: .medium)).foregroundStyle(.secondary)
                            Text(line.text).foregroundStyle(line.fromYou ? .primary : .secondary).textSelection(.enabled)
                        }
                    }
                    .padding(.horizontal, 16).padding(.vertical, 8)
                }
            }
        }
    }
}

// MARK: Dictation

struct DictationView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PageHeader(title: "Dictation", subtitle: "What you dictated, and your dictionary")
            Card { EmptyNote(icon: "text.cursor", text: "Dictation into any app arrives with milestone M8. Your history and dictionary will show here.") }
        }
    }
}
