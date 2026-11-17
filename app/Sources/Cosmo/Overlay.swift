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
                        Text(store.approval.map { "Should I \($0)?" } ?? store.headline)
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(store.approval != nil ? Color(hex: 0xFFD08A) : .white)
                            .lineLimit(1)
                            .truncationMode(.tail)
                        if store.state == .listening || store.state == .speaking {
                            VoiceBars()
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
        TimelineView(.animation(minimumInterval: 1.0 / 30)) { context in
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

final class DictationPanel: NSPanel {
    static let size = NSSize(width: 392, height: 92)

    init(store: EventStore) {
        super.init(contentRect: NSRect(origin: .zero, size: Self.size), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        ignoresMouseEvents = true
        let host = NSHostingView(rootView: DictationBubble(store: store))
        host.sizingOptions = []
        contentView = host
        place()
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            self?.place()
        }
    }

    func place() {
        guard let screen = NSScreen.main ?? NSScreen.screens.first else { return }
        let f = screen.visibleFrame
        setFrameOrigin(NSPoint(x: f.maxX - Self.size.width - 6, y: f.midY - Self.size.height / 2))
    }
}

struct DictationBubble: View {
    var store: EventStore

    private var liveText: String {
        let text = store.dictationText.split(separator: "\n").suffix(2).joined(separator: "\n")
        guard text.count > 90 else { return text }
        let cut = text.suffix(90)
        return "…" + (cut.firstIndex(of: " ").map { String(cut[cut.index(after: $0)...]) } ?? String(cut))
    }

    var body: some View {
        HStack(spacing: 8) {
            if store.dictation != .off && !liveText.isEmpty {
                Text(liveText)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .truncationMode(.head)
                    .frame(maxWidth: 280, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .glassEffect(.regular.tint(.black.opacity(0.45)), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
            if store.dictation != .off {
                VStack(spacing: 6) {
                    Orb(state: store.dictation == .working ? .thinking : .listening, size: 30, halo: false)
                        .overlay(alignment: .bottomTrailing) {
                            if store.dictation == .locked {
                                Image(systemName: "lock.fill").font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                                    .padding(3).background(Circle().fill(Color(hex: 0x4C7DFF))).offset(x: 4, y: 4)
                            }
                        }
                    if store.dictation == .working {
                        ProgressView().controlSize(.small)
                    } else {
                        VoiceBars().scaleEffect(0.7)
                    }
                }
                .frame(width: 64, height: 76)
                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
                .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .padding(.trailing, 14)
        .frame(width: DictationPanel.size.width, height: DictationPanel.size.height, alignment: .trailing)
        .animation(.spring(duration: 0.3), value: store.dictation)
        .animation(.easeOut(duration: 0.2), value: liveText.isEmpty)
        .environment(\.colorScheme, .dark)
    }
}

final class HighlightPanel: NSPanel {
    init() {
        super.init(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .screenSaver
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        ignoresMouseEvents = true
        contentView = NSHostingView(rootView: HighlightBox())
    }

    func flash(_ box: CGRect) {
        guard let primary = NSScreen.screens.first else { return }
        let rect = NSRect(x: box.minX - 4, y: primary.frame.maxY - box.maxY - 4, width: box.width + 8, height: box.height + 8)
        setFrame(rect, display: true)
        alphaValue = 1
        orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 1.2
            animator().alphaValue = 0
        }
    }
}

struct HighlightBox: View {
    var body: some View {
        RoundedRectangle(cornerRadius: 6, style: .continuous)
            .strokeBorder(Color(hex: 0x7CC4FF), lineWidth: 2.5)
            .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(Color(hex: 0x7CC4FF, opacity: 0.12)))
            .shadow(color: Color(hex: 0x7CC4FF, opacity: 0.8), radius: 6)
    }
}

final class ControlPanel: NSPanel {
    static let size = NSSize(width: 460, height: 64)

    init(store: EventStore) {
        super.init(contentRect: NSRect(origin: .zero, size: Self.size), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        let host = NSHostingView(rootView: ControlBar(store: store))
        host.sizingOptions = []
        contentView = host
        place()
    }

    func place() {
        guard let screen = NSScreen.main ?? NSScreen.screens.first else { return }
        let f = screen.visibleFrame
        setFrameOrigin(NSPoint(x: f.midX - Self.size.width / 2, y: f.minY + 14))
    }
}

struct ControlBar: View {
    var store: EventStore
    var body: some View {
        if let control = store.control {
            HStack(spacing: 10) {
                Image(systemName: "cursorarrow.motionlines").foregroundStyle(Color(hex: 0x7CC4FF))
                Text("Using \(control.app)").font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Text(control.action).font(.system(size: 13)).foregroundStyle(.secondary).lineLimit(1)
                Spacer(minLength: 8)
                Button("Stop") { store.stop() }.controlSize(.small).keyboardShortcut(.cancelAction)
            }
            .padding(.horizontal, 16)
            .frame(width: ControlPanel.size.width - 16, height: 44)
            .glassEffect(.regular, in: Capsule())
            .frame(width: ControlPanel.size.width, height: ControlPanel.size.height)
            .environment(\.colorScheme, .dark)
        }
    }
}
