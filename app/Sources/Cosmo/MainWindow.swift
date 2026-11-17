import SwiftUI

enum Pane: String, CaseIterable, Identifiable {
    case now = "Now", inbox = "Inbox", activity = "Activity", workers = "Workers"
    case devices = "Devices", sessions = "Sessions", conversation = "Conversation", dictation = "Dictation"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .now: return "waveform.circle"
        case .inbox: return "tray"
        case .activity: return "list.bullet.rectangle"
        case .workers: return "square.stack.3d.up"
        case .devices: return "laptopcomputer.and.iphone"
        case .sessions: return "terminal"
        case .conversation: return "bubble.left.and.bubble.right"
        case .dictation: return "text.cursor"
        }
    }
}

struct MainWindow: View {
    var store: EventStore
    @State private var section: Pane? = .now
    @State private var ask = ""
    @FocusState private var askFocused: Bool

    var body: some View {
        NavigationSplitView {
            List(selection: $section) {
                ForEach(Pane.allCases) { s in
                    Label(s.rawValue, systemImage: s.icon)
                        .badge(badge(for: s))
                        .tag(s)
                }
            }
            .navigationSplitViewColumnWidth(min: 200, ideal: 220)
            .safeAreaInset(edge: .bottom) { DevicesMini(store: store).padding(12) }
        } detail: {
            VStack(spacing: 0) {
                ScrollView {
                    Group {
                        switch section ?? .now {
                        case .now: NowView(store: store)
                        case .inbox: InboxView(store: store)
                        case .activity: ActivityView(store: store)
                        case .workers: WorkersView(store: store)
                        case .devices: DevicesView(store: store)
                        case .sessions: SessionsView(store: store)
                        case .conversation: ConversationView(store: store)
                        case .dictation: DictationView()
                        }
                    }
                    .padding(.horizontal, 34)
                    .padding(.vertical, 28)
                    .frame(maxWidth: 1100, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                StatusBar(store: store)
            }
            .toolbar {
                ToolbarItem(placement: .principal) {
                    HStack(spacing: 8) {
                        Image(systemName: "sparkle").foregroundStyle(.secondary)
                        TextField("Ask Cosmo to do something", text: $ask)
                            .textFieldStyle(.plain)
                            .focused($askFocused)
                            .onSubmit { store.ask(ask); ask = "" }
                        Text("⌘K").font(.caption).foregroundStyle(.tertiary)
                    }
                    .padding(.horizontal, 14)
                    .frame(width: 460, height: 32)
                    .glassEffect(.regular, in: Capsule())
                }
            }
        }
        .background { Button("") { askFocused = true }.keyboardShortcut("k", modifiers: .command).hidden() }
        .containerBackground(.ultraThinMaterial, for: .window)
        .preferredColorScheme(.dark)
        .frame(minWidth: 980, minHeight: 640)
    }

    private func badge(for s: Pane) -> Int {
        switch s {
        case .inbox: return (store.approval == nil ? 0 : 1) + store.workers.filter(\.failed).count
        case .workers: return store.runningWorkers.count
        case .sessions: return store.sessions.count
        default: return 0
        }
    }
}

struct StatusBar: View {
    var store: EventStore
    var body: some View {
        HStack(spacing: 10) {
            Orb(state: store.state, size: 15, halo: false)
            Text(store.state.title).font(.system(size: 12.5)).foregroundStyle(.secondary)
            Spacer()
            if store.state != .idle && store.state != .offline || !store.runningWorkers.isEmpty {
                Button("Stop everything") { store.stop() }
                    .buttonStyle(.glass)
                    .keyboardShortcut(.escape, modifiers: [])
                    .controlSize(.small)
            }
        }
        .padding(.horizontal, 18)
        .frame(height: 42)
    }
}

struct DevicesMini: View {
    var store: EventStore
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Devices").font(.system(size: 11.5, weight: .medium)).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                Circle().fill(store.state == .offline ? Color.gray : Color(hex: 0x7CC4FF)).frame(width: 7, height: 7)
                    .shadow(color: Color(hex: 0x7CC4FF, opacity: store.state == .offline ? 0 : 0.8), radius: 4)
                Text("This Mac").font(.system(size: 12.5))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(.white.opacity(0.04)))
    }
}
