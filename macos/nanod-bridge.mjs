#!/usr/bin/env node
// nanod-bridge.mjs — Nano_D++ macOS companion daemon (Node 22 ESM)
//
// Connects to the Nano_D++ TCP JSON server on port 3333, performs the
// mutual HMAC-SHA256 handshake, then maps device events to macOS actions
// (volume knob, media keys, custom Shortcuts) via osascript.
//
// Usage:
//   NANOD_IP=192.168.1.42 NANOD_PSK=mysecret node nanod-bridge.mjs
//   node nanod-bridge.mjs --config ./nanod-config.json
//
// Config JSON keys: ip, psk, port (optional, default 3333)

import net from "node:net";
import crypto from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile } from "node:fs/promises";
import { argv, env, exit } from "node:process";

const execFileAsync = promisify(execFile);

// ─── Config loading ───────────────────────────────────────────────────────────

async function loadConfig() {
  // --config <path> takes precedence, then env vars, then defaults
  const configFlagIdx = argv.indexOf("--config");
  if (configFlagIdx !== -1 && argv[configFlagIdx + 1]) {
    const raw = await readFile(argv[configFlagIdx + 1], "utf8");
    const file = JSON.parse(raw);
    return {
      ip:   file.ip   ?? env.NANOD_IP  ?? "192.168.1.42",
      psk:  file.psk  ?? env.NANOD_PSK ?? "",
      port: file.port ?? 3333,
    };
  }
  return {
    ip:   env.NANOD_IP  ?? "192.168.1.42",
    psk:  env.NANOD_PSK ?? "",
    port: 3333,
  };
}

// ─── HMAC helpers ─────────────────────────────────────────────────────────────

/** HMAC-SHA256(psk_string, msgBytes) → 32-byte Buffer */
function hmacSha256(pskString, msgBuffer) {
  return crypto.createHmac("sha256", pskString).update(msgBuffer).digest();
}

/** Decode a hex string to a Buffer; throws on bad input */
function hexDecode(hex) {
  if (typeof hex !== "string" || hex.length % 2 !== 0)
    throw new Error(`Invalid hex string: ${hex}`);
  return Buffer.from(hex, "hex");
}

/** Constant-time Buffer comparison (same length assumed) */
function ctEq(a, b) {
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

// ─── macOS action helpers ─────────────────────────────────────────────────────

async function osascript(script) {
  try {
    await execFileAsync("osascript", ["-e", script]);
  } catch (err) {
    log(`[ACTION] osascript error: ${err.message.trim()}`);
  }
}

async function shortcutsRun(name) {
  try {
    await execFileAsync("shortcuts", ["run", name]);
  } catch (err) {
    log(`[ACTION] shortcuts error: ${err.message.trim()}`);
  }
}

/** Clamp a number to [lo, hi] */
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// ─── Volume state tracker ─────────────────────────────────────────────────────
// We track a floating accumulator keyed to the knob angle (radians) so
// coarse detents map predictably to volume percentage steps.

const volumeState = {
  lastAngle: null,   // last knob angle in radians (float from "a" field)
  volume: null,      // last known macOS volume (0-100), null = unknown
};

async function getSystemVolume() {
  try {
    const { stdout } = await execFileAsync("osascript", [
      "-e", "output volume of (get volume settings)",
    ]);
    return parseInt(stdout.trim(), 10);
  } catch {
    return 50; // safe default if query fails
  }
}

async function setSystemVolume(vol) {
  const v = clamp(Math.round(vol), 0, 100);
  await osascript(`set volume output volume ${v}`);
  volumeState.volume = v;
}

// ─── Mapping table ────────────────────────────────────────────────────────────
//
// Edit this object to customise device behaviour.  Each action is an async
// function; returning without throwing is considered success.
//
// knob.onAngle(deltaRad, totalAngle, velocity)
//   Called on every {"a","t","v"} telemetry frame.  deltaRad is the signed
//   change in shaft angle since the last frame (positive = clockwise).
//
// keys.onDown[n](keyMask)  — called on {"kd": n} (key index 0-3 = A-D)
// keys.onUp[n](keyMask)    — called on {"ku": n} (key index 0-3 = A-D)
//
// All fields are optional; missing entries are silently ignored.

const MAPPING = {
  knob: {
    // Volume: each 2π radian full rotation = 40 volume steps (~2.5% per step).
    // Adjust VOLUME_PER_RAD to taste (e.g. 3.18 ≈ 1% per 18°).
    VOLUME_PER_RAD: 6.37, // volume units per radian (≈ 1% per ~9° arc)

    async onAngle(deltaRad, _totalAngle, _velocity) {
      if (volumeState.volume === null) {
        volumeState.volume = await getSystemVolume();
      }
      const newVol = volumeState.volume + deltaRad * MAPPING.knob.VOLUME_PER_RAD;
      await setSystemVolume(newVol);
      log(`[VOL] ${volumeState.volume}%`);
    },
  },

  keys: {
    // Key A (index 0) — Play / Pause
    onDown: {
      0: async () => {
        log("[KEY A] Play/Pause");
        await osascript(`tell application "System Events" to key code 49 using {}`);
        // key code 49 = space; works in Music.app and Spotify when they are in front.
        // Alternatively use the dedicated media key via osascript:
        // await osascript(`tell application "System Events" to keystroke (ASCII character 29) using {}`);
        // Or call a Shortcut:
        // await shortcutsRun("Play Pause");
      },
    },

    // Key B (index 1) — Next track
    onDown_B: undefined, // placeholder — actual handler below

    // Key C (index 2) — Previous track
    onDown_C: undefined,

    // Key D (index 3) — Mute toggle
    onDown_D: undefined,

    // onUp handlers — add if you need key-release actions
    onUp: {},
  },
};

// Fill in remaining key handlers using AppleScript media commands.
// These work system-wide regardless of which music app is focused.
MAPPING.keys.onDown[1] = async () => {
  log("[KEY B] Next track");
  // next track via System Events media key (key code 124 = right arrow won't work
  // for media; we use osascript "next track" targeting Music.app, or a Shortcut)
  await osascript(`
    tell application "Music"
      if it is running then next track
    end tell
  `);
  // Uncomment to use Spotify instead:
  // await osascript(`tell application "Spotify" to next track`);
  // Uncomment to use a macOS Shortcut instead:
  // await shortcutsRun("Next Track");
};

MAPPING.keys.onDown[2] = async () => {
  log("[KEY C] Previous track");
  await osascript(`
    tell application "Music"
      if it is running then back track
    end tell
  `);
  // await osascript(`tell application "Spotify" to previous track`);
};

MAPPING.keys.onDown[3] = async () => {
  log("[KEY D] Mute toggle");
  await osascript(`
    set muteState to output muted of (get volume settings)
    set volume output muted (not muteState)
  `);
};

// ─── Logging ──────────────────────────────────────────────────────────────────

function log(msg) {
  const ts = new Date().toISOString().replace("T", " ").replace("Z", "");
  console.log(`[${ts}] ${msg}`);
}

// ─── Event dispatcher ─────────────────────────────────────────────────────────

async function dispatchEvent(msg) {
  // Knob telemetry — prefer "a" (angle in radians) over legacy "p"
  if ("a" in msg) {
    const angle = msg.a;
    const delta = volumeState.lastAngle === null ? 0 : angle - volumeState.lastAngle;
    volumeState.lastAngle = angle;
    if (delta !== 0 && MAPPING.knob?.onAngle) {
      await MAPPING.knob.onAngle(delta, angle, msg.v ?? 0).catch((e) =>
        log(`[ERROR] knob handler: ${e.message}`)
      );
    }
    return;
  }

  // Legacy knob position — only used if "a" is absent
  if ("p" in msg && !("a" in msg)) {
    log(`[KNOB] legacy p=${msg.p} (no angle data; upgrade firmware for full mapping)`);
    return;
  }

  // Key down
  if ("kd" in msg) {
    const idx = msg.kd;
    const handler = MAPPING.keys?.onDown?.[idx];
    if (handler) {
      await handler(msg.ks ?? 0).catch((e) =>
        log(`[ERROR] key-down[${idx}] handler: ${e.message}`)
      );
    } else {
      log(`[KEY ${["A","B","C","D"][idx] ?? idx}] down (no mapping)`);
    }
    return;
  }

  // Key up
  if ("ku" in msg) {
    const idx = msg.ku;
    const handler = MAPPING.keys?.onUp?.[idx];
    if (handler) {
      await handler(msg.ks ?? 0).catch((e) =>
        log(`[ERROR] key-up[${idx}] handler: ${e.message}`)
      );
    }
    return;
  }

  // Informational messages — log but don't act
  if ("ack" in msg)     { log(`[ACK] ${msg.ack} ok=${msg.ok}`); return; }
  if ("debug" in msg)   { log(`[DBG] ${msg.debug}`); return; }
  if ("error" in msg)   { log(`[ERR] ${msg.error}${msg.msg ? " — " + msg.msg : ""}`); return; }
  if ("idle" in msg)    { /* suppress idle heartbeat noise */ return; }
  if ("current" in msg) { log(`[PROFILE] ${msg.current}`); return; }
  if ("saved" in msg)   { log(`[SAVED]`); return; }
  if ("connected" in msg) {
    log(`[DEVICE] ${msg.device ?? "?"} fw=${msg.fw ?? "?"} ip=${msg.ip ?? "?"}`);
    return;
  }
}

// ─── TCP connection + handshake ───────────────────────────────────────────────

class NanodBridge {
  constructor(config) {
    this._ip    = config.ip;
    this._port  = config.port;
    this._psk   = config.psk;
    this._sock  = null;
    this._buf   = "";
    this._authed = false;
    this._backoffMs = 1000;
    this._reconnectTimer = null;
    this._stopping = false;
  }

  start() {
    log(`[BRIDGE] Starting — device ${this._ip}:${this._port}`);
    if (!this._psk) {
      log("[BRIDGE] WARNING: No PSK set. The device will refuse the connection.");
      log("[BRIDGE] Set NANOD_PSK env var or add \"psk\" to your config file.");
    }
    this._connect();
  }

  stop() {
    this._stopping = true;
    clearTimeout(this._reconnectTimer);
    if (this._sock) {
      this._sock.destroy();
      this._sock = null;
    }
    log("[BRIDGE] Stopped.");
  }

  // ── Internal ──────────────────────────────────────────────────────────────

  _connect() {
    if (this._stopping) return;
    log(`[BRIDGE] Connecting to ${this._ip}:${this._port} …`);

    const sock = net.createConnection({ host: this._ip, port: this._port });
    this._sock   = sock;
    this._buf    = "";
    this._authed = false;

    sock.setEncoding("utf8");
    sock.setKeepAlive(true, 10_000);

    sock.on("connect", () => {
      log("[BRIDGE] TCP connected — awaiting hello");
      // Handshake is driven by incoming data; nothing to send yet.
    });

    sock.on("data", (chunk) => {
      this._buf += chunk;
      let nl;
      while ((nl = this._buf.indexOf("\n")) !== -1) {
        const line = this._buf.slice(0, nl).trim();
        this._buf  = this._buf.slice(nl + 1);
        if (line.length === 0) continue;
        if (!line.startsWith("{")) {
          log(`[DEVICE] ${line}`);
          continue;
        }
        let msg;
        try {
          msg = JSON.parse(line);
        } catch {
          log(`[PARSE] Bad JSON: ${line.slice(0, 120)}`);
          continue;
        }
        this._onMessage(msg, sock);
      }
    });

    sock.on("error", (err) => {
      log(`[BRIDGE] Socket error: ${err.message}`);
    });

    sock.on("close", () => {
      if (this._stopping) return;
      log(`[BRIDGE] Disconnected — reconnecting in ${this._backoffMs}ms`);
      volumeState.lastAngle = null; // reset knob tracking on disconnect
      this._scheduleReconnect();
    });
  }

  _scheduleReconnect() {
    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = setTimeout(() => {
      if (!this._stopping) this._connect();
    }, this._backoffMs);
    // Exponential backoff capped at 30 s
    this._backoffMs = Math.min(this._backoffMs * 2, 30_000);
  }

  _send(obj) {
    if (!this._sock || this._sock.destroyed) return;
    this._sock.write(JSON.stringify(obj) + "\n");
  }

  async _onMessage(msg, _sock) {
    // ── Handshake phase ──────────────────────────────────────────────────────
    if (!this._authed) {
      // Step 1: device sends hello with nonce
      if (msg.hello) {
        const { nonce: deviceNonceHex, proto } = msg.hello;
        if (proto !== 1) {
          log(`[AUTH] Unsupported protocol version: ${proto}`);
          this._sock?.destroy();
          return;
        }
        log(`[AUTH] Got hello (proto ${proto}) — sending auth`);

        // Decode device nonce from hex → raw bytes
        let deviceNonceBytes;
        try {
          deviceNonceBytes = hexDecode(deviceNonceHex);
        } catch (e) {
          log(`[AUTH] Bad device nonce hex: ${e.message}`);
          this._sock?.destroy();
          return;
        }

        // Compute client HMAC: HMAC-SHA256(psk, deviceNonceBytes)
        const clientHmac = hmacSha256(this._psk, deviceNonceBytes);

        // Generate 16-byte client nonce
        const clientNonceBytes = crypto.randomBytes(16);
        const clientNonceHex   = clientNonceBytes.toString("hex");

        // Send auth frame
        this._send({
          auth: {
            hmac:  clientHmac.toString("hex"),
            nonce: clientNonceHex,
          },
        });

        // Stash client nonce for proof verification in the next step
        this._clientNonceBytes = clientNonceBytes;
        return;
      }

      // Step 2: device replies with {auth:{ok:true,hmac:"<deviceProof>"}}
      if (msg.auth) {
        if (!msg.auth.ok) {
          log("[AUTH] Authentication rejected by device (wrong PSK?)");
          this._sock?.destroy();
          return;
        }
        // Verify device proof: HMAC-SHA256(psk, clientNonceBytes)
        const expectedProof = hmacSha256(this._psk, this._clientNonceBytes);
        let deviceProofBytes;
        try {
          deviceProofBytes = hexDecode(msg.auth.hmac);
        } catch (e) {
          log(`[AUTH] Bad device proof hex: ${e.message}`);
          this._sock?.destroy();
          return;
        }

        if (!ctEq(expectedProof, deviceProofBytes)) {
          log("[AUTH] Device proof MISMATCH — possible MITM; closing connection");
          this._sock?.destroy();
          return;
        }

        log("[AUTH] Mutual authentication successful");
        this._authed    = true;
        this._backoffMs = 1000; // reset backoff on successful auth
        this._clientNonceBytes = null;
        return;
      }

      // Any other frame before auth — ignore (device may send greeting first)
      // The TCP greeting {"connected":true,...} arrives before hello; dispatch it
      if (msg.connected !== undefined) {
        await dispatchEvent(msg);
      }
      return;
    }

    // ── Normal operation ─────────────────────────────────────────────────────
    await dispatchEvent(msg);
  }
}

// ─── Entry point ─────────────────────────────────────────────────────────────

const config = await loadConfig();
log(`[BRIDGE] Config: ip=${config.ip} port=${config.port} psk=${config.psk ? "***" : "(not set)"}`);

const bridge = new NanodBridge(config);
bridge.start();

// Graceful shutdown
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    log(`[BRIDGE] Received ${sig}, shutting down`);
    bridge.stop();
    exit(0);
  });
}
