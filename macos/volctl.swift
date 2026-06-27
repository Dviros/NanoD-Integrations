// volctl — persistent CoreAudio volume setter. Reads a target volume (0-100)
// per line on stdin and sets the default output device volume instantly.
// Replaces per-change `osascript` spawns (~50-100ms) with a ~1ms CoreAudio call.
import CoreAudio
import Foundation

func defaultOutput() -> AudioDeviceID {
    var id = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    var a = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice,
                                       mScope: kAudioObjectPropertyScopeGlobal,
                                       mElement: kAudioObjectPropertyElementMain)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, &id)
    return id
}

func setVolume(_ dev: AudioDeviceID, _ vol: Float32) {
    var v = max(Float32(0), min(Float32(1), vol))
    let size = UInt32(MemoryLayout<Float32>.size)
    var a = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyVolumeScalar,
                                       mScope: kAudioDevicePropertyScopeOutput,
                                       mElement: kAudioObjectPropertyElementMain)
    if AudioObjectHasProperty(dev, &a) {
        AudioObjectSetPropertyData(dev, &a, 0, nil, size, &v)
    } else {                                   // master not settable → per-channel
        for ch in [UInt32(1), UInt32(2)] {
            a.mElement = ch
            if AudioObjectHasProperty(dev, &a) { AudioObjectSetPropertyData(dev, &a, 0, nil, size, &v) }
        }
    }
}

let dev = defaultOutput()
setvbuf(stdin, nil, _IONBF, 0)
while let line = readLine() {
    if let v = Float32(line.trimmingCharacters(in: .whitespaces)) { setVolume(dev, v / 100.0) }
}
