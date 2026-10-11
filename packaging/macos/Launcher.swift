import AppKit
import Foundation
import Darwin

final class Launcher: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var label: NSTextField!
    private var openButton: NSButton!
    private var child: Process?
    private var timer: Timer?
    private var backendURL: URL?
    private var readyFile: URL!
    private var statusFile: URL!
    private var logHandle: FileHandle?
    private var checking = false
    private var quitting = false
    private var lastDiagnostic = ""
    private let smokeMode = CommandLine.arguments.contains("--smoke-test")
    private let files = FileManager.default
    private lazy var data: URL = {
        if smokeMode, let path = ProcessInfo.processInfo.environment["MAXIAOMA_SMOKE_DATA_DIR"] {
            return URL(fileURLWithPath: path, isDirectory: true).resolvingSymlinksInPath()
        }
        return files.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MaXiaoMaPublisher", isDirectory: true).resolvingSymlinksInPath()
    }()

    func applicationDidFinishLaunching(_ notification: Notification) {
        diagnostic("AppKit ready; data=\(data.path)")
        let menu = NSMenu()
        let root = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "退出马小马视频发布平台", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        root.submenu = appMenu
        menu.addItem(root)
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 230),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "马小马视频发布平台"
        window.isReleasedWhenClosed = false
        let title = NSTextField(labelWithString: "马小马视频发布平台")
        title.font = NSFont.boldSystemFont(ofSize: 23)
        label = NSTextField(wrappingLabelWithString: "正在启动…")
        label.alignment = .center
        label.widthAnchor.constraint(equalToConstant: 470).isActive = true
        openButton = NSButton(title: "正在启动", target: self, action: #selector(openOrRetry))
        openButton.isEnabled = false
        let logs = NSButton(title: "查看日志", target: self, action: #selector(showLogs))
        let quit = NSButton(title: "退出", target: NSApp, action: #selector(NSApplication.terminate(_:)))
        let buttons = NSStackView(views: [openButton, logs, quit])
        buttons.spacing = 12
        let stack = NSStackView(views: [title, label, buttons])
        stack.orientation = .vertical
        stack.spacing = 24
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: window.contentView!.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: window.contentView!.centerYAnchor)
        ])
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        launch()
    }

    private func launch() {
        guard child?.isRunning != true else { return }
        backendURL = nil
        openButton.isEnabled = false
        openButton.title = "正在启动"
        do {
            let runDirectory = data.appendingPathComponent("run", isDirectory: true)
            let logs = data.appendingPathComponent("logs", isDirectory: true)
            try files.createDirectory(at: runDirectory, withIntermediateDirectories: true)
            try files.createDirectory(at: logs, withIntermediateDirectories: true)
            let id = UUID().uuidString
            readyFile = runDirectory.appendingPathComponent("\(id)-ready.json")
            statusFile = runDirectory.appendingPathComponent("\(id)-status.json")
            let log = logs.appendingPathComponent("launcher.log")
            if !files.fileExists(atPath: log.path) { files.createFile(atPath: log.path, contents: nil) }
            logHandle = try FileHandle(forWritingTo: log)
            try logHandle!.seekToEnd()
            guard let resources = Bundle.main.resourceURL else { throw CocoaError(.fileNoSuchFile) }
            let process = Process()
            process.executableURL = resources.appendingPathComponent("python/bin/python3")
            process.arguments = ["-u", "-B", resources.appendingPathComponent("runtime.py").path]
            var env = ProcessInfo.processInfo.environment
            env["SAU_DATA_DIR"] = data.path
            env["SAU_PORT"] = "5409"
            env["SAU_READY_FILE"] = readyFile.path
            env["MAXIAOMA_STATUS_FILE"] = statusFile.path
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env.removeValue(forKey: "PYTHONPATH")
            env.removeValue(forKey: "PYTHONHOME")
            process.environment = env
            process.currentDirectoryURL = data
            process.standardOutput = logHandle
            process.standardError = logHandle
            process.terminationHandler = { [weak self] _ in
                DispatchQueue.main.async {
                    guard let self = self, !self.quitting else { return }
                    self.timer?.invalidate()
                    self.backendURL = nil
                    self.label.stringValue = "服务已停止。请查看日志，或点击重试。"
                    self.openButton.title = "重试"
                    self.openButton.isEnabled = true
                    self.window.makeKeyAndOrderFront(nil)
                    if self.smokeMode { exit(1) }
                }
            }
            child = process
            try process.run()
            diagnostic("Backend PID=\(process.processIdentifier); ready=\(readyFile.path)")
            timer?.invalidate()
            timer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in self?.poll() }
        } catch {
            diagnostic("Launch failed: \(error)")
            label.stringValue = "无法启动运行环境：\(error.localizedDescription)"
            openButton.title = "重试"
            openButton.isEnabled = true
            if smokeMode { exit(1) }
        }
    }

    private func diagnostic(_ message: String) {
        guard smokeMode, message != lastDiagnostic else { return }
        lastDiagnostic = message
        print("[launcher] \(message)")
        fflush(stdout)
    }

    private func matchesDataDirectory(_ path: String?) -> Bool {
        // Foundation may display /private/var as /var; Python resolves the opposite way.
        // Resolve both with the same POSIX API before checking backend ownership.
        guard let path = path, let actual = realpath(path, nil) else { return false }
        defer { free(actual) }
        guard let expected = realpath(data.path, nil) else { return false }
        defer { free(expected) }
        return strcmp(actual, expected) == 0
    }

    private func poll() {
        if let bytes = try? Data(contentsOf: statusFile),
           let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
           let text = object["message"] as? String { label.stringValue = text }
        guard !checking, backendURL == nil, child?.isRunning == true,
              let bytes = try? Data(contentsOf: readyFile),
              let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let port = object["port"] as? Int, let pid = object["pid"] as? Int,
              pid == Int(child!.processIdentifier), (1024...65535).contains(port),
              let url = URL(string: "http://127.0.0.1:\(port)") else { return }
        checking = true
        var request = URLRequest(url: url.appendingPathComponent("api/health"))
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] body, response, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.checking = false
                let health = body.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                if error == nil, (response as? HTTPURLResponse)?.statusCode == 200,
                   health?["db_ok"] as? Bool == true, self.matchesDataDirectory(health?["sau_data_dir"] as? String),
                   self.child?.isRunning == true {
                    self.backendURL = url
                    self.timer?.invalidate()
                    self.label.stringValue = "平台正在运行。关闭这个窗口不会停止服务；退出应用会停止服务。"
                    self.openButton.title = "打开平台"
                    self.openButton.isEnabled = true
                    if self.smokeMode, let path = ProcessInfo.processInfo.environment["MAXIAOMA_SMOKE_RESULT"] {
                        self.diagnostic("Health ready; writing result and quitting")
                        try? Data("launcher-ready".utf8).write(to: URL(fileURLWithPath: path))
                        NSApp.terminate(nil)
                    } else { NSWorkspace.shared.open(url) }
                } else {
                    let responseCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                    let databaseReady = health?["db_ok"] as? Bool == true
                    let matchesData = self.matchesDataDirectory(health?["sau_data_dir"] as? String)
                    let errorText = error?.localizedDescription ?? "none"
                    self.diagnostic("Health pending: HTTP=\(responseCode), error=\(errorText), db=\(databaseReady), dataMatches=\(matchesData)")
                }
            }
        }.resume()
    }

    @objc private func openOrRetry() {
        if let url = backendURL { NSWorkspace.shared.open(url) } else { launch() }
    }
    @objc private func showLogs() { NSWorkspace.shared.open(data.appendingPathComponent("logs")) }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if let url = backendURL { NSWorkspace.shared.open(url) }
        else { window.makeKeyAndOrderFront(nil) }
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if smokeMode { return .terminateNow }
        if child?.isRunning == true {
            let alert = NSAlert()
            alert.messageText = "退出马小马视频发布平台？"
            alert.informativeText = "退出会停止账号维护，并中断尚未完成的发布任务。"
            alert.addButton(withTitle: "退出")
            alert.addButton(withTitle: "继续运行")
            if alert.runModal() != .alertFirstButtonReturn { return .terminateCancel }
        }
        return .terminateNow
    }
    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        timer?.invalidate()
        if let process = child, process.isRunning {
            let pid = process.processIdentifier
            if getpgid(pid) == pid { kill(-pid, SIGTERM) } else { process.terminate() }
            let deadline = Date().addingTimeInterval(4)
            while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if process.isRunning {
                if getpgid(pid) == pid { kill(-pid, SIGKILL) } else { kill(pid, SIGKILL) }
            }
        }
        try? logHandle?.close()
        if let path = readyFile { try? files.removeItem(at: path) }
        if let path = statusFile { try? files.removeItem(at: path) }
    }
}

let application = NSApplication.shared
let delegate = Launcher()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
