#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{atomic::{AtomicBool, AtomicU64, Ordering}, mpsc, Arc, Mutex};
use std::time::Duration;
use tauri::{Emitter, Manager};
#[cfg(windows)]
use std::os::windows::process::CommandExt;

type Reply = Result<Value, String>;

/// True between the backend's recording_started and the end of that recording. The notch may only
/// hide while this is set, and the backend's own end events bring it back, so a failed save, a
/// crashed recorder or a late hide request from the webview can never leave it hidden for good.
static RECORDING: AtomicBool = AtomicBool::new(false);

fn show_notch(app: &tauri::AppHandle) {
    RECORDING.store(false, Ordering::SeqCst);
    if let Some(window) = app.get_webview_window("notch") { let _ = window.show(); }
}
type Pending = Arc<Mutex<HashMap<String, mpsc::Sender<Reply>>>>;

struct Bridge {
    input: Mutex<Option<ChildStdin>>,
    child: Mutex<Child>,
    pending: Pending,
    next_id: AtomicU64,
}

impl Bridge {
    fn start(app: tauri::AppHandle) -> Result<Self, String> {
        let root = std::env::var_os("DIGITALSOUL_PROJECT_DIR").map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."));
        let root = root.canonicalize().map_err(|e| format!("Project folder unavailable: {e}"))?;
        let python = root.join(".venv").join(if cfg!(windows) { "Scripts/python.exe" } else { "bin/python" });
        if !python.is_file() {
            return Err("Project .venv Python not found. Follow desktop/README.md to install dependencies.".into());
        }
        let mut command = Command::new(python);
        command.args(["-u", "-m", "agent.ghost.desktop.notch_bridge"]).arg(&root)
            .current_dir(&root).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let mut child = command.spawn().map_err(|e| format!("Cannot start desktop backend: {e}"))?;
        let input = child.stdin.take().ok_or("Backend stdin unavailable")?;
        let output = child.stdout.take().ok_or("Backend stdout unavailable")?;
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let replies = Arc::clone(&pending);
        std::thread::spawn(move || {
            for line in BufReader::new(output).lines() {
                let Ok(line) = line else { break };
                let Ok(value) = serde_json::from_str::<Value>(&line) else { continue };
                if let Some(id) = value.get("id").and_then(Value::as_str) {
                    if let Some(sender) = replies.lock().unwrap().remove(id) {
                        let result = if value["ok"].as_bool() == Some(true) {
                            Ok(value["result"].clone())
                        } else {
                            Err(value["error"].as_str().unwrap_or("Desktop command failed").to_owned())
                        };
                        let _ = sender.send(result);
                    }
                } else {
                    match value.get("event").and_then(Value::as_str) {
                        Some("recording_started") => RECORDING.store(true, Ordering::SeqCst),
                        Some("recording_saved" | "error") => show_notch(&app),
                        _ => {}
                    }
                    let _ = app.emit("desktop-event", value);
                }
            }
            for (_, sender) in replies.lock().unwrap().drain() {
                let _ = sender.send(Err("Desktop backend disconnected; reopen the app".into()));
            }
            show_notch(&app);
            let _ = app.emit("desktop-event", json!({"event":"disconnected"}));
        });
        Ok(Self { input: Mutex::new(Some(input)), child: Mutex::new(child), pending, next_id: AtomicU64::new(1) })
    }

    fn request(&self, method: String, payload: Value) -> Reply {
        if !["hello", "list", "detail", "start", "stop", "rename", "delete", "replay", "schedule", "cancel", "stop_jobs", "resume_jobs", "replay_steps", "voice_start", "voice_stop", "voice_command", "shopify_generate", "shopify_run", "shopify_cancel"].contains(&method.as_str()) {
            return Err("Unsupported desktop command".into());
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed).to_string();
        let line = serde_json::to_string(&json!({"id":id,"method":method,"payload":payload})).map_err(|e| e.to_string())?;
        if line.len() > 65536 { return Err("Desktop request too large".into()) }
        let (sender, receiver) = mpsc::channel();
        self.pending.lock().unwrap().insert(id.clone(), sender);
        let written = (|| -> Result<(), String> {
            let mut guard = self.input.lock().map_err(|e| e.to_string())?;
            let input = guard.as_mut().ok_or("Desktop backend is closed")?;
            writeln!(input, "{line}").map_err(|e| e.to_string())?;
            input.flush().map_err(|e| e.to_string())
        })();
        if let Err(error) = written {
            self.pending.lock().unwrap().remove(&id);
            return Err(error);
        }
        // Shopify runs a real browser (and a model call) inside the backend; give it time to finish.
        let wait = if method.starts_with("shopify_") { 240 } else { 20 };
        let result = receiver.recv_timeout(Duration::from_secs(wait));
        self.pending.lock().unwrap().remove(&id);
        result.map_err(|_| "Desktop command timed out; check the app before retrying".to_string())?
    }

    fn shutdown(&self) {
        if let Some(mut input) = self.input.lock().unwrap().take() {
            let _ = writeln!(input, "{{\"method\":\"shutdown\"}}");
            let _ = input.flush();
        }
        let mut child = self.child.lock().unwrap();
        for _ in 0..20 {
            if matches!(child.try_wait(), Ok(Some(_))) { return }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

struct Backend(Result<Bridge, String>);

#[tauri::command]
async fn backend_request(app: tauri::AppHandle, window: tauri::WebviewWindow, method: String, payload: Value) -> Reply {
    // Only the notch UI drives the backend; the ghost overlay is display-only.
    if window.label() != "notch" { return Err("Unsupported desktop command".into()) }
    tauri::async_runtime::spawn_blocking(move || {
        let backend = app.state::<Backend>();
        match &backend.0 { Ok(bridge) => bridge.request(method, payload), Err(error) => Err(error.clone()) }
    }).await.map_err(|e| e.to_string())?
}

fn position(window: &tauri::WebviewWindow, expanded: bool, keep: bool) -> Result<(), String> {
    let monitor = window.current_monitor().map_err(|e| e.to_string())?
        .or(window.primary_monitor().map_err(|e| e.to_string())?).ok_or("No monitor available")?;
    let scale = monitor.scale_factor();
    let (left, top) = (monitor.position().x as f64, monitor.position().y as f64);
    let (screen_w, screen_h) = (monitor.size().width as f64, monitor.size().height as f64);
    let width = (if expanded { 660.0_f64 } else { 420.0_f64 }).min(screen_w / scale);
    let height = (if expanded { 680.0_f64 } else { 53.0_f64 }).min(screen_h / scale - 16.0);
    // After the user drags the banner, grow and shrink around where they left it.
    let (center_x, y) = match (keep, window.outer_position(), window.outer_size()) {
        (true, Ok(pos), Ok(size)) => (pos.x as f64 + size.width as f64 / 2.0, pos.y as f64),
        _ => (left + screen_w / 2.0, top),
    };
    let x = (center_x - width * scale / 2.0).clamp(left, left + screen_w - width * scale);
    let y = y.clamp(top, top + screen_h - height * scale);
    window.set_size(tauri::LogicalSize::new(width, height)).map_err(|e| e.to_string())?;
    window.set_position(tauri::PhysicalPosition::new(x as i32, y as i32)).map_err(|e| e.to_string())
}

#[tauri::command]
fn set_notch_expanded(window: tauri::WebviewWindow, expanded: bool) -> Result<(), String> { position(&window, expanded, true) }

#[tauri::command]
fn quit_app(app: tauri::AppHandle) { app.exit(0) }

/// While recording the notch gets out of the way entirely; F8 stops and brings it back.
#[tauri::command]
fn set_notch_hidden(window: tauri::WebviewWindow, hidden: bool) -> Result<(), String> {
    if hidden && !RECORDING.load(Ordering::SeqCst) { return Ok(()) }
    if hidden { window.hide() } else { window.show() }.map_err(|e| e.to_string())
}

/// The replay ghost: a click-through overlay that the Python replayer flies to each control it acts on.
/// It is created once and never focused, so moving it cannot steal focus from the app being replayed.
/// 72x72 logical leaves room for the ghost's glow inside the transparent window (no square halo).
const GHOST_PARKED: (i32, i32) = (-4000, -4000);

fn main() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![backend_request, set_notch_expanded, set_notch_hidden, quit_app])
        .setup(|app| {
            let backend = Bridge::start(app.handle().clone());
            app.manage(Backend(backend));
            if let Some(window) = app.get_webview_window("notch") { let _ = position(&window, false, false); }
            let ghost = tauri::WebviewWindowBuilder::new(app, "ghost-cursor", tauri::WebviewUrl::App("ghost.html".into()))
                .title("DigitalSoul ghost").inner_size(72.0, 72.0).position(GHOST_PARKED.0 as f64, GHOST_PARKED.1 as f64)
                .decorations(false).transparent(true).shadow(false).resizable(false)
                .always_on_top(true).skip_taskbar(true).focused(false).visible(true)
                .build()?;
            let _ = ghost.set_ignore_cursor_events(true);
            // Windows ignores a far-off builder position and enforces a minimum width; park it explicitly.
            let _ = ghost.set_size(tauri::LogicalSize::new(72.0, 72.0));
            let _ = ghost.set_position(tauri::PhysicalPosition::new(GHOST_PARKED.0, GHOST_PARKED.1));
            Ok(())
        })
        .build(tauri::generate_context!()).expect("Unable to create DigitalSoul window");
    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Ok(bridge) = &app.state::<Backend>().0 { bridge.shutdown(); }
        }
    });
}
