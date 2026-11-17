import Foundation
import Observation

@MainActor
@Observable
final class EventStore {
    static let home = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".cosmo")
    static let eventsURL = home.appendingPathComponent("events.jsonl")
    static let sessionsURL = home.appendingPathComponent("sessions.json")
    static let commandsURL = home.appendingPathComponent("commands.jsonl")
    static let startTail = 400_000

    var state: CosmoState = .offline
    var lastSaid = ""
    var lastHeard = ""
    var approval: String?
    var lines: [Line] = []
    var workers: [Worker] = []
    var activity: [ActivityItem] = []
    var sessions: [SessionRow] = []
    var lastEvent = Date.distantPast

    @ObservationIgnored private var offset: UInt64 = 0
    @ObservationIgnored private var asked: String?
    @ObservationIgnored private var source: DispatchSourceFileSystemObject?
    @ObservationIgnored private var timer: Timer?

    var runningWorkers: [Worker] { workers.filter(\.running) }
    var doneWorkers: [Worker] { workers.filter { !$0.running }.reversed() }
    var problems: [ActivityItem] { activity.filter { $0.kind == .problem } }

    var headline: String {
        if let approval { return "Should I \(approval)?" }
        switch state {
        case .offline: return starting ? "Starting Cosmo…" : "Cosmo isn’t running"
        case .listening: return lastHeard.isEmpty ? "Listening" : lastHeard
        case .speaking: return lastSaid
        case .thinking: return "Thinking"
        case .waiting: return "Waiting for your answer"
        case .idle:
            let n = runningWorkers.count
            return n == 0 ? "Say “Hey Cosmo”" : "\(n) worker\(n == 1 ? "" : "s") running in the background"
        }
    }

    func start() {
        try? FileManager.default.createDirectory(at: Self.home, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: Self.eventsURL.path) {
            FileManager.default.createFile(atPath: Self.eventsURL.path, contents: nil)
        }
        let size = (try? FileManager.default.attributesOfItem(atPath: Self.eventsURL.path)[.size] as? UInt64) ?? 0
        offset = size > UInt64(Self.startTail) ? size - UInt64(Self.startTail) : 0
        readNew(skipPartialFirstLine: offset > 0)
        watch()
        loadSessions()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in
                if self?.source == nil { self?.watch() }
                self?.readNew(skipPartialFirstLine: false)
                self?.loadSessions()
            }
        }
    }

    private func watch() {
        source?.cancel()
        source = nil
        let fd = open(Self.eventsURL.path, O_EVTONLY)
        guard fd >= 0 else { return }
        let s = DispatchSource.makeFileSystemObjectSource(fileDescriptor: fd, eventMask: [.write, .extend, .delete, .rename], queue: .main)
        s.setEventHandler { [weak self] in
            guard let self else { return }
            MainActor.assumeIsolated {
                if s.data.contains(.delete) || s.data.contains(.rename) {
                    self.offset = 0
                    self.watch()
                }
                self.readNew(skipPartialFirstLine: false)
            }
        }
        s.setCancelHandler { close(fd) }
        s.resume()
        source = s
    }

    private func readNew(skipPartialFirstLine: Bool) {
        guard let handle = try? FileHandle(forReadingFrom: Self.eventsURL) else { return }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        if size < offset { offset = 0 }
        guard size > offset else { return }
        try? handle.seek(toOffset: offset)
        guard let data = try? handle.readToEnd(), let end = data.lastIndex(of: 0x0A) else { return }
        offset += UInt64(end + 1)
        var lines = data[data.startIndex...end].split(separator: 0x0A)
        if skipPartialFirstLine, !lines.isEmpty { lines.removeFirst() }
        let decoder = JSONDecoder()
        for raw in lines {
            if let event = try? decoder.decode(RawEvent.self, from: Data(raw)) { apply(event) }
        }
        if activity.count > 2000 { activity.removeFirst(activity.count - 2000) }
        if self.lines.count > 1000 { self.lines.removeFirst(self.lines.count - 1000) }
    }

    private func note(_ kind: ActivityItem.Kind, _ text: String, _ time: Date) {
        activity.append(ActivityItem(time: time, kind: kind, text: text))
    }

    private func apply(_ e: RawEvent) {
        let time = Date(timeIntervalSince1970: e.t)
        lastEvent = time
        let d = e.detail
        switch e.state {
        case "ready":
            state = .idle
            approval = nil
            for i in workers.indices where workers[i].running { workers[i].status = "stopped" }
        case "idle", "listening", "thinking", "speaking":
            state = CosmoState(rawValue: e.state) ?? state
            approval = nil
        case "waiting_for_approval":
            state = .waiting
            if !d.text.isEmpty {
                approval = d.text
                asked = d.text
                note(.approval, "Asked: \(d.text)", time)
            } else if approval == nil {
                approval = asked
            }
        case "approval":
            approval = nil
            asked = nil
            note(.approval, "Answer: \(d.text)", time)
        case "heard":
            guard !d.text.isEmpty else { break }
            lastHeard = d.text
            lines.append(Line(time: time, fromYou: true, text: d.text))
            note(.conversation, "You: \(d.text)", time)
        case "said":
            lastSaid = d.text
            lines.append(Line(time: time, fromYou: false, text: d.text))
        case "worker_started":
            workers.removeAll { $0.id == d["id"] }
            workers.append(Worker(id: d["id"], title: d["title"], started: time))
            note(.worker, "Started: \(d["title"])", time)
        case "worker_progress":
            if let i = workers.firstIndex(where: { $0.id == d["id"] }) {
                let step = d["tool"].isEmpty ? d["step"] : "\(d["step"]) · \(d["tool"])"
                if workers[i].steps.last?.1 != step { workers[i].steps.append((time, step)) }
            }
        case "worker_done":
            if let i = workers.firstIndex(where: { $0.id == d["id"] }) {
                if !d["summary"].isEmpty { workers[i].summary = d["summary"] }
                if workers[i].running {
                    workers[i].status = d["status"].isEmpty ? "completed" : d["status"]
                    workers[i].ended = time
                    note(workers[i].failed ? .problem : .worker, "\(workers[i].status.capitalized): \(workers[i].title)", time)
                }
            }
        case "wake":
            note(.wake, "Heard “Cosmo”", time)
        case "wake_not_confirmed":
            note(.wake, "Ignored: \(d.text)", time)
        case "interrupted", "talked_over":
            note(.conversation, "Stopped", time)
        case "error":
            note(.problem, d.text, time)
        case "not_you":
            note(.wake, "Ignored a voice that isn’t yours", time)
        default:
            break
        }
    }

    private func loadSessions() {
        guard let data = try? Data(contentsOf: Self.sessionsURL),
              let rows = try? JSONDecoder().decode([SessionRow].self, from: data) else { return }
        sessions = rows
    }

    func send(_ command: [String: String]) {
        var line = command
        line["t"] = String(Date().timeIntervalSince1970)
        guard let data = try? JSONSerialization.data(withJSONObject: line), let text = String(data: data, encoding: .utf8) else { return }
        let url = Self.commandsURL
        if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
        guard let handle = try? FileHandle(forWritingTo: url) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: Data((text + "\n").utf8))
    }

    func checkAlive() {
        let url = Self.home.appendingPathComponent("cosmo.pid")
        let pid = (try? String(contentsOf: url, encoding: .utf8)).flatMap { Int32($0.trimmingCharacters(in: .whitespacesAndNewlines)) }
        let alive = pid.map { kill($0, 0) == 0 } ?? false
        if !alive && state != .offline {
            state = .offline
            approval = nil
            for i in workers.indices where workers[i].running { workers[i].status = "stopped" }
        } else if alive && state == .offline {
            state = .idle
        }
        if alive || launched?.isRunning != true { starting = false }
    }

    static let python = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("cosmo/.venv/bin/python")
    @ObservationIgnored private var launched: Process?
    var starting = false

    func startCosmo() {
        checkAlive()
        guard state == .offline, launched?.isRunning != true else { return }
        let process = Process()
        process.executableURL = Self.python
        process.arguments = ["-m", "cosmo"]
        process.currentDirectoryURL = Self.home
        let log = Self.home.appendingPathComponent("cosmo.log")
        if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil) }
        if let handle = try? FileHandle(forWritingTo: log) {
            _ = try? handle.seekToEnd()
            process.standardOutput = handle
            process.standardError = handle
        }
        do {
            try process.run()
            launched = process
            starting = true
        } catch {
            note(.problem, "Couldn’t start Cosmo: \(error.localizedDescription)", Date())
        }
    }

    func approve(_ yes: Bool) { send(["cmd": "approve", "answer": yes ? "yes" : "no"]) }
    func stop() { send(["cmd": "stop"]) }
    func ask(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty { send(["cmd": "ask", "text": t]) }
    }
}
