import AppKit
import CoreImage
import CoreImage.CIFilterBuiltins
import SwiftUI

enum PhoneLink {
    static let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".cosmo/phone.json")

    static func load() -> (link: String, hosts: [String])? {
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let link = obj["link"] as? String else { return nil }
        return (link, obj["hosts"] as? [String] ?? [])
    }

    static func qr(_ text: String) -> NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 10, y: 10)),
              let cg = CIContext().createCGImage(output, from: output.extent) else { return nil }
        return NSImage(cgImage: cg, size: NSSize(width: output.extent.width / 2, height: output.extent.height / 2))
    }
}

enum PhoneInstall {
    static var adb: String? {
        ["/opt/homebrew/bin/adb", "/usr/local/bin/adb", NSHomeDirectory() + "/Library/Android/sdk/platform-tools/adb"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    static var apk: URL? {
        Bundle.main.url(forResource: "Cosmo", withExtension: "apk")
    }

    static func run(_ args: [String], timeout: TimeInterval = 120) async -> (ok: Bool, output: String) {
        guard let adb else { return (false, "adb is not installed.") }
        return await Task.detached {
            let log = FileManager.default.temporaryDirectory.appendingPathComponent("cosmo-adb-\(UUID().uuidString).txt")
            FileManager.default.createFile(atPath: log.path, contents: nil)
            defer { try? FileManager.default.removeItem(at: log) }
            guard let handle = try? FileHandle(forWritingTo: log) else { return (false, "Could not run adb.") }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: adb)
            process.arguments = args
            process.standardOutput = handle
            process.standardError = handle
            do { try process.run() } catch { return (false, error.localizedDescription) }
            let deadline = Date().addingTimeInterval(timeout)
            while process.isRunning && Date() < deadline { try? await Task.sleep(for: .milliseconds(100)) }
            if process.isRunning { process.terminate() }
            try? handle.close()
            let out = (try? String(contentsOf: log, encoding: .utf8)) ?? ""
            return (!process.isRunning && process.terminationStatus == 0, out.trimmingCharacters(in: .whitespacesAndNewlines))
        }.value
    }

    static func phones() async -> [String] {
        let result = await run(["devices"], timeout: 10)
        return result.output.split(separator: "\n").dropFirst().filter { $0.hasSuffix("\tdevice") }.map { String($0.split(separator: "\t")[0]) }
    }
}

struct PhonePairSheet: View {
    var store: EventStore
    @Environment(\.dismiss) private var dismiss
    @State private var pairing = PhoneLink.load()
    @State private var copied = false

    var body: some View {
        VStack(spacing: 18) {
            Text("Pair your phone").font(.title2.weight(.semibold))
            if let pairing, let image = PhoneLink.qr(pairing.link) {
                Image(nsImage: image)
                    .interpolation(.none)
                    .resizable()
                    .frame(width: 240, height: 240)
                    .padding(12)
                    .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(.white))
                VStack(alignment: .leading, spacing: 6) {
                    Label("Open Cosmo on your phone", systemImage: "1.circle")
                    Label("Tap Scan pairing code and point it here", systemImage: "2.circle")
                    Label("Same Wi‑Fi, or Tailscale when you’re away", systemImage: "3.circle")
                }
                .foregroundStyle(.secondary)
                HStack(spacing: 6) {
                    Circle().fill(store.phoneConnected ? Color.green : Color.secondary).frame(width: 8, height: 8)
                    Text(store.phoneConnected ? "Phone connected" : "Waiting for your phone…")
                }
                .font(.callout)
            } else {
                ContentUnavailableView("Cosmo isn’t ready", systemImage: "iphone.slash",
                                       description: Text("Start Cosmo, then open this again."))
                    .frame(height: 260)
            }
            HStack {
                if let pairing {
                    Button(copied ? "Copied" : "Copy Link") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(pairing.link, forType: .string)
                        copied = true
                    }
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 400)
        .onAppear { pairing = PhoneLink.load() }
        .onChange(of: store.phoneConnected) { _, connected in
            if connected { DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { dismiss() } }
        }
    }
}

struct PhoneSection: View {
    var store: EventStore
    @State private var pairing = false
    @State private var adbReady = false
    @State private var serial: String?
    @State private var installing = false
    @State private var installNote = ""

    var body: some View {
        Section {
            LabeledContent {
                HStack(spacing: 6) {
                    Circle().fill(store.phoneConnected ? Color.green : Color.secondary).frame(width: 7, height: 7)
                    Text(store.phoneConnected ? "Connected" : "Not connected").foregroundStyle(.secondary)
                }
            } label: {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Cosmo app")
                        Text("Chat, Stop and approvals from your phone").font(.callout).foregroundStyle(.secondary)
                    }
                } icon: { Image(systemName: "iphone").frame(width: 20) }
            }
            HStack {
                Button("Pair Phone…") { pairing = true }
                if PhoneInstall.apk != nil {
                    Button(installing ? "Installing…" : "Install on Phone") { install() }
                        .disabled(!adbReady || installing)
                        .help(adbReady ? "Install the Cosmo app on the phone connected over adb" : "Connect your phone over adb, for example with DevSwitch")
                }
                Spacer()
            }
            if !installNote.isEmpty { Text(installNote).font(.callout).foregroundStyle(.secondary) }
        } header: {
            Text("Cosmo on your phone")
        }
        .sheet(isPresented: $pairing) { PhonePairSheet(store: store) }
        .task {
            while !Task.isCancelled {
                serial = await PhoneInstall.phones().first
                adbReady = serial != nil
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    private func install() {
        guard let apk = PhoneInstall.apk, let serial else { return }
        installing = true
        installNote = ""
        Task {
            let result = await PhoneInstall.run(["-s", serial, "install", "-r", apk.path])
            installing = false
            installNote = result.ok ? "Installed. Open Cosmo on your phone, then click Pair Phone." : "Install failed: \(result.output.suffix(200))"
        }
    }
}
