import SwiftUI

struct Orb: View {
    var state: CosmoState
    var size: CGFloat
    var halo = true

    private var rim: [Color] {
        switch state {
        case .waiting: return [Color(hex: 0xFFD08A), Color(hex: 0xF7B955), Color(hex: 0xFFE3B0), Color(hex: 0xFFD08A)]
        case .offline, .idle: return [Color(white: 0.55), Color(white: 0.3), Color(white: 0.5), Color(white: 0.55)]
        default: return [Color(hex: 0x7CC4FF), Color(hex: 0xB69CFF), Color(hex: 0xFF9ECF), Color(hex: 0xFFD08A), Color(hex: 0x7CF0D0), Color(hex: 0x7CC4FF)]
        }
    }

    private var spinSeconds: Double {
        switch state {
        case .thinking: return 0.9
        case .listening, .waiting: return 3
        case .speaking: return 4
        case .idle, .offline: return 24
        }
    }

    private var glowing: Bool { state == .listening || state == .speaking || state == .waiting }
    private var beat: Double { state == .speaking ? 0.55 : 1.4 }
    @State private var onScreen = true

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !onScreen || state == .idle || state == .offline)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let angle = Angle.degrees((t / spinSeconds).truncatingRemainder(dividingBy: 1) * 360)
            let pulse = 0.5 + 0.5 * sin(t * 2 * .pi / beat)
            ZStack {
                if halo && glowing {
                    Circle()
                        .fill(AngularGradient(colors: rim, center: .center, angle: angle * 0.4))
                        .frame(width: size * 1.5, height: size * 1.5)
                        .blur(radius: size * 0.28)
                        .opacity(0.35 + 0.35 * pulse)
                        .scaleEffect(0.92 + 0.12 * pulse)
                }
                Circle()
                    .fill(RadialGradient(colors: [Color(hex: 0x4A4A54), Color(hex: 0x1A1A20), Color(hex: 0x07070A)],
                                         center: UnitPoint(x: 0.34, y: 0.28), startRadius: 0, endRadius: size * 0.72))
                Circle()
                    .strokeBorder(AngularGradient(colors: rim, center: .center, angle: angle), lineWidth: max(1.5, size * 0.035))
                    .opacity(state == .idle || state == .offline ? 0.45 : 1)
                Circle()
                    .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
            }
            .frame(width: size, height: size)
        }
        .frame(width: size, height: size)
        .background(WindowVisibility(visible: $onScreen))
        .accessibilityElement()
        .accessibilityLabel("Cosmo, \(state.title)")
    }
}

struct WindowVisibility: NSViewRepresentable {
    @Binding var visible: Bool

    func makeNSView(context: Context) -> Probe { Probe() }
    func updateNSView(_ view: Probe, context: Context) { view.report = { visible = $0 } }

    final class Probe: NSView {
        var report: (Bool) -> Void = { _ in }
        private var token: NSObjectProtocol?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let token { NotificationCenter.default.removeObserver(token) }
            token = nil
            guard let window else { return }
            token = NotificationCenter.default.addObserver(forName: NSWindow.didChangeOcclusionStateNotification, object: window, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.update() }
            }
            DispatchQueue.main.async { self.update() }
        }

        private func update() {
            report(window?.occlusionState.contains(.visible) ?? false)
        }

        deinit {
            if let token { NotificationCenter.default.removeObserver(token) }
        }
    }
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255, opacity: opacity)
    }
}
