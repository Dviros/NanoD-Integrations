// nowplaying-art.swift
// Fetches current now-playing artwork via the private MediaRemote framework.
// Writes artwork bytes to a temp file, then prints one line to stdout:
//   <trackIdentifier>\t<artworkTempFilePath>
// Exits with code 1 if no track is playing or no artwork is available.
// Compiles with: swiftc nowplaying-art.swift -o nowplaying-art

import Foundation

// ── Private MediaRemote bindings ──────────────────────────────────────────────

private typealias MRMediaRemoteGetNowPlayingInfoFn = @convention(c) (
    DispatchQueue,
    @escaping ([String: AnyObject]) -> Void
) -> Void

private let kMRMediaRemoteNowPlayingInfoArtworkData       = "kMRMediaRemoteNowPlayingInfoArtworkData"
private let kMRMediaRemoteNowPlayingInfoTitle             = "kMRMediaRemoteNowPlayingInfoTitle"
private let kMRMediaRemoteNowPlayingInfoArtist            = "kMRMediaRemoteNowPlayingInfoArtist"
private let kMRMediaRemoteNowPlayingInfoUniqueIdentifier  = "kMRMediaRemoteNowPlayingInfoUniqueIdentifier"
private let kMRMediaRemoteNowPlayingInfoAlbum             = "kMRMediaRemoteNowPlayingInfoAlbum"

// ── Load framework ────────────────────────────────────────────────────────────

guard let handle = dlopen(
    "/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote",
    RTLD_NOW
) else {
    fputs("error: could not dlopen MediaRemote\n", stderr)
    exit(1)
}

guard let sym = dlsym(handle, "MRMediaRemoteGetNowPlayingInfo") else {
    fputs("error: MRMediaRemoteGetNowPlayingInfo symbol not found\n", stderr)
    exit(1)
}
private let MRMediaRemoteGetNowPlayingInfo = unsafeBitCast(sym, to: MRMediaRemoteGetNowPlayingInfoFn.self)

// ── Query ─────────────────────────────────────────────────────────────────────

let sema = DispatchSemaphore(value: 0)
var exitCode: Int32 = 1

MRMediaRemoteGetNowPlayingInfo(.global(qos: .userInitiated)) { info in
    defer { sema.signal() }

    guard !info.isEmpty else { return }  // nothing playing

    // Identify the track — prefer unique ID, fall back to title+artist
    let uid   = info[kMRMediaRemoteNowPlayingInfoUniqueIdentifier] as? String
    let title  = info[kMRMediaRemoteNowPlayingInfoTitle]  as? String ?? ""
    let artist = info[kMRMediaRemoteNowPlayingInfoArtist] as? String ?? ""
    let album  = info[kMRMediaRemoteNowPlayingInfoAlbum]  as? String ?? ""
    let trackID = uid ?? "\(title)\t\(artist)\t\(album)"

    // Pull raw artwork bytes
    guard let artData = info[kMRMediaRemoteNowPlayingInfoArtworkData] as? Data,
          !artData.isEmpty else { return }

    // Detect format (JPEG: FF D8; PNG: 89 50; everything else → write as-is)
    let ext: String
    if artData.prefix(2).elementsEqual([0xFF, 0xD8]) {
        ext = "jpg"
    } else if artData.prefix(4).elementsEqual([0x89, 0x50, 0x4E, 0x47]) {
        ext = "png"
    } else {
        ext = "bin"
    }

    let tmpPath = NSTemporaryDirectory()
        .appending("nanod-art-\(ProcessInfo.processInfo.processIdentifier).\(ext)")
    do {
        try artData.write(to: URL(fileURLWithPath: tmpPath), options: .atomic)
    } catch {
        fputs("error: write failed: \(error)\n", stderr)
        return
    }

    // Emit single tab-separated line: <trackID>\t<path>
    print("\(trackID)\t\(tmpPath)")
    exitCode = 0
}

sema.wait()
exit(exitCode)
