// 工作流桌面窗口：在独立窗口（WebView2）里显示本地网页界面，不经过浏览器。
// 启动时：需要的话先拉起读取 D 盘 27B 模型的 Ollama（端口 11435，留在后台，与 启动网页.cmd 相同），
// 再拉起网页服务（端口 8790，不显示命令行窗口），就绪后把窗口切到界面。
// 关闭窗口时：结束由本程序拉起的网页服务；如果服务原本就在运行（例如另开了 启动网页.cmd），不去动它。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

const APP_URL: &str = "http://127.0.0.1:8790";
const NO_WINDOW: u32 = 0x0800_0000; // CREATE_NO_WINDOW：子进程不弹命令行窗口

/// 由本程序拉起的网页服务；关窗口时结束它
struct Server(Mutex<Option<Child>>);

/// 项目目录：从程序所在位置往上找含 tools\serve.py 的目录；找不到就用编译时的位置
fn project_root() -> PathBuf {
    let has_server = |p: &Path| p.join("tools").join("serve.py").is_file();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(found) = exe.ancestors().find(|p| has_server(p)) {
            return found.to_path_buf();
        }
    }
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// 简单的 HTTP GET：只看能不能拿到 200
fn http_ok(port: u16, path: &str) -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else { return false };
    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
    if s.write_all(format!("GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 16];
    matches!(s.read(&mut buf), Ok(n) if n >= 12 && &buf[9..12] == b"200")
}

fn start_ollama(root: &Path) {
    // 要由本程序拉起的 Ollama 服务（模型目录、用哪块显卡等）写在 model_settings.json 的 ollama 一项里，
    // 由 python -m tools.llm start 读取并启动；这里不写死任何本机路径或显卡
    let python = root.join(".venv").join("Scripts").join("python.exe");
    if !python.is_file() {
        return;
    }
    let _ = Command::new(python)
        .args(["-B", "-m", "tools.llm", "start"])
        .current_dir(root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(NO_WINDOW)
        .spawn();
}

fn start_server(root: &Path) -> Result<Child, String> {
    let python = root.join(".venv").join("Scripts").join("python.exe");
    if !python.is_file() {
        return Err(format!("找不到 Python 环境：{}", python.display()));
    }
    let log = std::fs::File::create(root.join("desktop").join("server.log")).ok();
    let mut cmd = Command::new(python);
    cmd.args(["-B", "-m", "tools.serve"])
        .current_dir(root)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .creation_flags(NO_WINDOW);
    match log.and_then(|f| f.try_clone().ok().map(|g| (f, g))) {
        Some((out, err)) => cmd.stdout(out).stderr(err),
        None => cmd.stdout(Stdio::null()).stderr(Stdio::null()),
    };
    cmd.spawn().map_err(|e| format!("没能启动网页服务：{e}"))
}

fn show_error(window: &tauri::WebviewWindow, text: &str) {
    let js = format!("window.showError({})", serde_json_string(text));
    let _ = window.eval(&js);
}

/// 把文字转成 JS 字符串字面量（只需处理引号、反斜杠和换行）
fn serde_json_string(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => {}
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Server(Mutex::new(None)))
        .setup(|app| {
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("工作流")
                .inner_size(1280.0, 800.0)
                .min_inner_size(960.0, 620.0)
                .center()
                .maximized(true) // 不同屏幕和缩放比例下都铺满，不会比屏幕还大
                .build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let root = project_root();
                start_ollama(&root);
                if !http_ok(8790, "/api/config") {
                    match start_server(&root) {
                        Ok(child) => *handle.state::<Server>().0.lock().unwrap() = Some(child),
                        Err(e) => return show_error(&window, &e),
                    }
                }
                let started = Instant::now();
                while !http_ok(8790, "/api/config") {
                    // 服务进程提前退出（例如端口被占、依赖缺失）就不必再等
                    if let Some(child) = handle.state::<Server>().0.lock().unwrap().as_mut() {
                        if let Ok(Some(code)) = child.try_wait() {
                            let log = root.join("desktop").join("server.log");
                            return show_error(&window, &format!("网页服务启动失败（退出码 {code}）。\n详情见 {}", log.display()));
                        }
                    }
                    if started.elapsed() > Duration::from_secs(60) {
                        return show_error(&window, "网页服务 60 秒内没有就绪。可以关闭窗口后再试，或双击 启动网页.cmd 查看输出。");
                    }
                    std::thread::sleep(Duration::from_millis(300));
                }
                if let Ok(url) = APP_URL.parse() {
                    let _ = window.navigate(url);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("创建窗口失败");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Server>().0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
