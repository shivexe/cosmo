import AppKit
import SwiftUI

final class OverlayPanel: NSPanel {
    init(store: EventStore) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 720, height: 220),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        isMovableByWindowBackground = false
        ignoresMouseEvents = true
        let host = NSHostingView(rootView: OverlayCapsule(store: store))
        host.sizingOptions = []
        contentView = host
        place()
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            self?.place()
        }
    }

    override var canBecomeKey: Bool { true }

    func place() {
        guard let screen = NSScreen.screens.first ?? NSScreen.main else { return }
        let f = screen.visibleFrame
        setFrameOrigin(NSPoint(x: f.midX - frame.width / 2, y: f.maxY - frame.height - 8))
    }
}

struct OverlayCapsule: View {
    var store: EventStore

    private var visible: Bool {
        store.approval != nil || [.listening, .thinking, .speaking, .waiting].contains(store.state)
    }

    var body: some View {
        VStack {
            if visible {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 14) {
                        Orb(state: store.state, size: 34)
                        Text(store.headline)
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(store.approval != nil ? Color(hex: 0xFFD08A) : .white)
                            .lineLimit(1)
                            .truncationMode(.tail)
                        if store.state == .listening || store.state == .speaking {
                            VoiceBars()
                        }
                        let running = store.runningWorkers.count
                        if running > 0 && store.approval == nil {
                            Divider().frame(height: 18)
                            Text("\(running) running").font(.system(size: 12.5)).foregroundStyle(.secondary)
                        }
                    }
                    if store.approval != nil {
                        HStack(spacing: 8) {
                            Button("Allow") { store.approve(true) }
                                .buttonStyle(.glassProminent)
                                .tint(Color(hex: 0xF7B955))
                            Button("Deny") { store.approve(false) }
                                .buttonStyle(.glass)
                            Text("or just say yes or no").font(.system(size: 12.5)).foregroundStyle(.secondary)
                        }
                        .padding(.leading, 48)
                    }
                }
                .padding(.vertical, 10)
                .padding(.horizontal, 12)
                .padding(.trailing, 8)
                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 30, style: .continuous))
                .transition(.move(edge: .top).combined(with: .opacity))
            }
            Spacer(minLength: 0)
        }
        .frame(width: 720, height: 220, alignment: .top)
        .animation(.spring(duration: 0.35), value: visible)
        .animation(.spring(duration: 0.35), value: store.approval)
        .environment(\.colorScheme, .dark)
    }
}

struct VoiceBars: View {
    var body: some View {
        TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            HStack(spacing: 3) {
                ForEach(0..<4, id: \.self) { i in
                    Capsule()
                        .fill(.white)
                        .frame(width: 3, height: 4 + 14 * abs(sin(t * 5 + Double(i) * 1.3)))
                }
            }
            .frame(height: 18)
        }
    }
}
