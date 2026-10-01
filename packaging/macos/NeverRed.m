// NeverRed — lanzador nativo macOS (Objective-C, solo clang de serie).
// App Cocoa real (icono con punto, sin rebote eterno) que delega el trabajo
// pesado en los scripts de siempre: check-update.sh (código 42 = nueva app
// ya abierta, salir en silencio) y Launcher.sh --detach (reutiliza o arranca
// el servidor y abre el navegador). Vigila el pidfile: si el servidor muere
// (p. ej. autoapagado por inactividad), la app se cierra sola. Salir desde
// el Dock mata el servidor.
// Uso: NeverRed [macosDir] [dataDir]  (sin argumentos = rutas del bundle)

#import <Cocoa/Cocoa.h>
#include <signal.h>
#include <unistd.h>

static NSString *gMacosDir = nil;
static NSString *gDataDir = nil;
static NSString *gPidFile = nil;
static NSString *gAppURL = nil;

// Ejecuta un programa y devuelve su código de salida (127 = no arrancó).
static int runProg(NSString *path, NSArray<NSString *> *args) {
  NSTask *t = [[NSTask alloc] init];
  t.executableURL = [NSURL fileURLWithPath:path];
  t.arguments = args;
  @try {
    [t launch];
    [t waitUntilExit];
  } @catch (NSException *e) { return 127; }
  return t.terminationStatus;
}

static NSNumber *readPid(NSString *f) {
  NSError *err = nil;
  NSString *s = [NSString stringWithContentsOfFile:f encoding:NSUTF8StringEncoding error:&err];
  if (!s) return nil;
  s = [s stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
  if (s.length == 0) return nil;
  NSScanner *sc = [NSScanner scannerWithString:s];
  int v = 0;
  if (![sc scanInt:&v] || !sc.isAtEnd) return nil;
  return @(v);
}

// True si el PID existe Y es nuestro server.py (anti-reutilización de PIDs).
static BOOL serverAlive(NSString *pidFile) {
  NSNumber *n = readPid(pidFile);
  if (!n) return NO;
  pid_t pid = (pid_t)n.intValue;
  if (kill(pid, 0) != 0) return NO;
  NSTask *t = [[NSTask alloc] init];
  t.executableURL = [NSURL fileURLWithPath:@"/bin/ps"];
  t.arguments = @[ @"-p", [NSString stringWithFormat:@"%d", pid], @"-o", @"command=" ];
  NSPipe *out = [NSPipe pipe];
  t.standardOutput = out;
  @try {
    [t launch];
    [t waitUntilExit];
  } @catch (NSException *e) { return NO; }
  NSString *cmd = [[NSString alloc] initWithData:[[out fileHandleForReading] readDataToEndOfFile]
                                        encoding:NSUTF8StringEncoding];
  return cmd && [cmd rangeOfString:@"server.py"].location != NSNotFound;
}

static void stopServer(NSString *pidFile) {
  NSNumber *n = readPid(pidFile);
  if (n) kill((pid_t)n.intValue, SIGTERM);
  [[NSFileManager defaultManager] removeItemAtPath:pidFile error:nil];
}

static void alertAndExit(NSString *msg) {
  runProg(@"/usr/bin/osascript", @[ @"-e", [NSString stringWithFormat:@"display alert \"NeverRed\" message \"%@\"", msg] ]);
  exit(1);
}

@interface Delegate : NSObject <NSApplicationDelegate>
@end

@implementation Delegate
- (void)applicationDidFinishLaunching:(NSNotification *)n {
  (void)n;
  [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
  [NSTimer scheduledTimerWithTimeInterval:2.0 repeats:YES block:^(NSTimer *t) {
    (void)t;
    if (!serverAlive(gPidFile)) [NSApp terminate:nil];
  }];
}
- (BOOL)applicationShouldHandleReopen:(NSApplication *)s hasVisibleWindows:(BOOL)f {
  (void)s;
  (void)f;
  runProg(@"/usr/bin/open", @[ gAppURL ]);
  return YES;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)s {
  (void)s;
  stopServer(gPidFile);
  return NSTerminateNow;
}
@end

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    NSString *macos, *data;
    if (argc >= 3) {
      macos = [NSString stringWithUTF8String:argv[1]];
      data = [NSString stringWithUTF8String:argv[2]];
    } else {
      macos = [[NSBundle mainBundle].bundlePath stringByAppendingString:@"/Contents/MacOS"];
      data = [NSHomeDirectory() stringByAppendingString:@"/Library/Application Support/NeverRed"];
    }
    gMacosDir = macos;
    gDataDir = data;
    gPidFile = [data stringByAppendingString:@"/server.pid"];
    NSString *port = [[[NSProcessInfo processInfo] environment] objectForKey:@"PORT"];
    if (!port || port.length == 0) port = @"8000";
    gAppURL = [NSString stringWithFormat:@"http://127.0.0.1:%@", port];

    int upd = runProg([macos stringByAppendingString:@"/check-update.sh"], @[ macos, data ]);
    if (upd == 42) return 0;
    int det = runProg(@"/bin/sh", @[ [macos stringByAppendingString:@"/Launcher.sh"], @"--detach" ]);
    if (det == 42) return 0;
    // El servidor puede tardar unos segundos en responder en el primer arranque.
    BOOL ok = (det == 0);
    for (int i = 0; i < 3 && !ok; i++) {
      [NSThread sleepForTimeInterval:1.0];
      ok = serverAlive(gPidFile);
    }
    if (!ok) alertAndExit([NSString stringWithFormat:@"No se pudo arrancar el servidor. Mira %@/server.log", data]);

    NSApplication *app = [NSApplication sharedApplication];
    Delegate *d = [[Delegate alloc] init];
    app.delegate = d;
    [app run];
  }
  return 0;
}
