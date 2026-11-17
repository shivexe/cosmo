import SwiftUI

enum Pane: String, CaseIterable, Identifiable {
    case now = "Now", workers = "Tasks", sessions = "Sessions", dictation = "Dictation", devices = "Devices", activity = "Activity"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .now: return "waveform.circle"
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
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 8) {
                    Orb(state: store.state, size: 14, halo: false)
                    Text(store.state.title).font(.system(size: 12.5)).foregroundStyle(.secondary)
                    Spacer()
                    if store.state != .idle && store.state != .offline || !store.runningWorkers.isEmpty {
                        Button { store.stop() } label: { Image(systemName: "stop.fill") }
                            .buttonStyle(.borderless).help("Stop everything").keyboardShortcut(.escape, modifiers: [])
                    }
                    SettingsLink { Image(systemName: "gearshape") }.buttonStyle(.borderless).help("Settings")
                }
                .padding(.horizontal, 18).padding(.vertical, 14)
            }
        } detail: {
            Group {
                switch section.wrappedValue ?? .now {
                case .now: NowView(store: store)
                case .workers: WorkersView(store: store)
                case .sessions: SessionsView(store: store)
                case .dictation: DictationView(store: store)
                case .devices: DevicesView(store: store)
                case .activity: ActivityView(store: store)
                }
            }
            .navigationTitle((section.wrappedValue ?? .now).rawValue)
        }
        .background { Button("") { pane = Pane.now.rawValue }.keyboardShortcut("k", modifiers: .command).hidden() }
        .preferredColorScheme(.dark)
        .frame(minWidth: 980, minHeight: 640)
        .noInitialFocus()
    }

    private func badge(for s: Pane) -> Int {
        switch s {
        case .workers: return store.runningWorkers.count
        case .sessions: return store.sessions.count
        default: return 0
        }
    }
}

