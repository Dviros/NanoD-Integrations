// mediakey.swift — post macOS media keys so the knob's buttons control whatever's
// playing (Kaset/YouTube-Music, Spotify, Music, …). Kaset has no AppleScript API,
// but it responds to system media keys like every media app.
//
// Build:  swiftc -O mediakey.swift -o mediakey
// Use:    ./mediakey playpause | next | previous
//
// NOTE: posting HID events requires Accessibility permission for the process tree
// that runs this (System Settings → Privacy & Security → Accessibility → add your
// terminal / the bridge's launcher). Without it macOS silently drops the events.
import Cocoa

// NX_KEYTYPE_* media key codes.
let KEYS: [String: Int32] = ["playpause": 16, "next": 17, "previous": 18]

func postMediaKey(_ code: Int32) {
    func send(_ down: Bool) {
        let flags = NSEvent.ModifierFlags(rawValue: down ? 0xA00 : 0xB00)
        let data1 = (Int(code) << 16) | ((down ? 0xA : 0xB) << 8)
        guard let ev = NSEvent.otherEvent(with: .systemDefined, location: .zero,
                                          modifierFlags: flags, timestamp: 0,
                                          windowNumber: 0, context: nil,
                                          subtype: 8, data1: data1, data2: -1) else { return }
        ev.cgEvent?.post(tap: .cghidEventTap)
    }
    send(true); send(false)
}

let arg = CommandLine.arguments.dropFirst().first ?? ""
guard let code = KEYS[arg] else {
    FileHandle.standardError.write(Data("usage: mediakey <playpause|next|previous>\n".utf8))
    exit(2)
}
postMediaKey(code)
