import Cocoa

// NeverRed — lanzador nativo macOS. App Cocoa real (icono con punto, sin
// rebote eterno) que delega el trabajo pesado en los scripts de siempre:
// check-update.sh (código 42 = nueva app ya abierta, salir en silencio) y
// Launcher.sh --detach (reutiliza o arranca el servidor y abre el navegador).
// Vigila el pidfile: si el servidor muere (p. ej. autoapagado por
// inactividad), la app se cierra sola. Salir desde el Dock mata el servidor.
// Uso: NeverRed [macosDir] [dataDir]  (sin argumentos = rutas del bundle)

@discardableResult
func sh(_ path: String, _ args: [String]) -> Int32 {
  let p = Process()
  p.executableURL = URL(fileURLWithPath: path)
  p.arguments = args
  do {
    try p.run()
    p.waitUntilExit()
  } catch { return 127 }
  return p.terminationStatus
}

func readPid(_ f: String) -> Int32? {
  guard let s = try? String(contentsOfFile: f, encoding: .utf8) else { return nil }
  let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
  if t.isEmpty || t.rangeOfCharacter(from: CharacterSet.decimalDigits.inverted) != nil { return nil }
  return Int32(t)
}

func serverAlive(_ pidFile: String) -> Bool {
  guard let p = readPid(pidFile) else { return false }
  if kill(p, 0) != 0 { return false }
  // El PID podría haberse reutilizado: confirma que es nuestro server.py.
  let q = Process()
  q.executableURL = URL(fileURLWithPath: "/bin/ps")
  q.arguments = ["-p", String(p), "-o", "command="]
  let out = Pipe()
  q.standardOutput = out
  do {
    try q.run()
    q.waitUntilExit()
  } catch { return false }
  let cmd = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
  return cmd.contains("server.py")
}

func stopServer(_ pidFile: String) {
  if let p = readPid(pidFile) { kill(p, SIGTERM) }
  try? FileManager.default.removeItem(atPath: pidFile)
}

func alertAndExit(_ msg: String) -> Never {
  sh("/usr/bin/osascript", ["-e", "display alert \"NeverRed\" message \"\(msg)\""])
  exit(1)
}

let args = CommandLine.arguments
let macosDir: String
let dataDir: String
if args.count >= 3 {
  macosDir = args[1]; dataDir = args[2]
} else {
  macosDir = Bundle.main.bundlePath + "/Contents/MacOS"
  dataDir = NSHomeDirectory() + "/Library/Application Support/NeverRed"
}
let pidFile = dataDir + "/server.pid"
let port = ProcessInfo.processInfo.environment["PORT"] ?? "8000"
let appURL = "http://127.0.0.1:\(port)"

let upd = sh(macosDir + "/check-update.sh", [macosDir, dataDir])
if upd == 42 { exit(0) }
let det = sh("/bin/sh", [macosDir + "/Launcher.sh", "--detach"])
if det == 42 { exit(0) }
// El servidor puede tardar unos segundos en responder en el primer arranque.
var ok = det == 0
var tries = 0
while !ok && tries < 3 {
  Thread.sleep(forTimeInterval: 1)
  ok = serverAlive(pidFile)
  tries += 1
}
if !ok {
  alertAndExit("No se pudo arrancar el servidor. Mira \(dataDir)/server.log")
}

class Delegate: NSObject, NSApplicationDelegate {
  var timer: Timer?
  func applicationDidFinishLaunching(_ n: Notification) {
    NSApp.setActivationPolicy(.regular)
    timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in
      if !serverAlive(pidFile) { NSApp.terminate(nil) }
    }
  }
  func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows f: Bool) -> Bool {
    sh("/usr/bin/open", [appURL])
    return true
  }
  func applicationShouldTerminate(_ s: NSApplication) -> NSApplication.TerminateReply {
    stopServer(pidFile)
    return .terminateNow
  }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.run()
