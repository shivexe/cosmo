import Foundation

enum CosmoState: String {
    case offline, idle, listening, thinking, speaking, waiting = "waiting_for_approval"

    var title: String {
        switch self {
        case .offline: return "Not running"
        case .idle: return "Idle"
        case .listening: return "Listening"
        case .thinking: return "Thinking"
        case .speaking: return "Speaking"
        case .waiting: return "Needs you"
        }
    }
}

struct Line: Identifiable {
    let id = UUID()
    let time: Date
    let fromYou: Bool
    let text: String
}

struct Worker: Identifiable {
    let id: String
    var title: String
    var started: Date
    var steps: [(Date, String)] = []
    var status = "running"
    var summary = ""
    var ended: Date?

    var running: Bool { status == "running" }
    var failed: Bool { status == "failed" || status == "killed" }
    var lastStep: String {
        if !running { return summary.isEmpty ? status.capitalized : summary }
        return steps.last?.1 ?? "Starting"
    }
}

struct ActivityItem: Identifiable {
    let id = UUID()
    let time: Date
    let kind: Kind
    let text: String

    enum Kind: String, CaseIterable {
        case conversation = "Conversation", worker = "Workers", approval = "Approvals", wake = "Wake word", problem = "Problems"
    }
}

struct SessionRow: Decodable, Identifiable {
    let name: String
    let status: String
    let folder: String
    let topic: String
    let last: String
    let updated: Double
    var id: String { name }
}

struct RawEvent: Decodable {
    let t: Double
    let state: String
    let detail: Detail

    enum Detail: Decodable {
        case text(String)
        case object([String: String])

        init(from decoder: Decoder) throws {
            let c = try decoder.singleValueContainer()
            if let s = try? c.decode(String.self) { self = .text(s); return }
            if let o = try? c.decode([String: String].self) { self = .object(o); return }
            self = .text("")
        }

        var text: String { if case .text(let s) = self { return s } else { return "" } }
        subscript(key: String) -> String { if case .object(let o) = self { return o[key] ?? "" } else { return "" } }
    }
}
