import SwiftUI

enum Pane: String, CaseIterable, Identifiable {
    case now = "Now", inbox = "Inbox", activity = "Activity", workers = "Workers"
    case devices = "Devices", sessions = "Sessions", dictation = "Dictation"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .now: return "waveform.circle"
        case .inbox: return "tray"
        case .activity: return "list.bullet.rectangle"
        case .workers: return "square.stack.3d.up"
        case .devices: return "laptopcomputer.and.iphone"
        case .sessions: return "terminal"
        case .dictation: return "text.cursor"
        }
    }
}

struct MainWindow: View {
    var store: EventStore
    @AppStorage("pane") private var pane = Pane.now.rawValue
    private var section: Binding<Pane?> {
        Binding(get: { Pane(rawValue: pane) ?? .now }, set: { pane = ($0 ?? .now).rawValue })
    }

    var body: some View {
        NavigationSplitView {
            List(selection: section) {
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
                switch section.wrappedValue ?? .now {
                case .sessions: SessionsView(store: store)
                case .now: NowView(store: store)
                default:
                    ScrollView {
                        Group {
                            switch section.wrappedValue ?? .now {
                            case .inbox: InboxView(store: store)
                            case .activity: ActivityView(store: store)
                            case .workers: WorkersView(store: store)
                            case .devices: DevicesView(store: store)
                            case .dictation: DictationView(store: store)
                            default: EmptyView()
                            }
                        }
                        .padding(.horizontal, 34)
                        .padding(.vertical, 28)
                        .frame(maxWidth: 1100, alignment: .leading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                if section.wrappedValue != .now { StatusBar(store: store) }
            }
        }
        .background { Button("") { pane = Pane.now.rawValue }.keyboardShortcut("k", modifiers: .command).hidden() }
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
            ForEach(store.hosts) { h in
                HStack(spacing: 8) {
                    Circle().fill(h.online ? Color(hex: 0x7CC4FF) : Color.gray).frame(width: 7, height: 7)
                    Text(h.label).font(.system(size: 12.5)).foregroundStyle(h.online ? .primary : .secondary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(.white.opacity(0.04)))
    }
}
