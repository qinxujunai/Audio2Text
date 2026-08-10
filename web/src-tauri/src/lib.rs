use serde::Serialize;
use std::{
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, State,
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone, Serialize)]
struct DesktopRuntimeInfo {
    api_base: String,
    token: String,
}

#[derive(Default)]
struct DesktopState {
    runtime: Mutex<Option<DesktopRuntimeInfo>>,
    child: Mutex<Option<Child>>,
    background_task_active: Mutex<bool>,
}

fn choose_loopback_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|error| format!("无法分配本地服务端口：{error}"))
}

fn project_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn sidecar_command(app: &AppHandle) -> Result<(Command, PathBuf), String> {
    if cfg!(debug_assertions) {
        let root = project_root();
        let python = root.join(".venv").join("Scripts").join("python.exe");
        if !python.is_file() {
            return Err("开发环境缺少可用的项目 Python。".into());
        }
        let mut command = Command::new(python);
        command.args(["-m", "scripts.start_api"]);
        return Ok((command, root));
    }

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("无法定位应用资源目录：{error}"))?;
    let sidecar_dir = resource_dir.join("sidecar").join("wanxiang-api");
    let executable = sidecar_dir.join("wanxiang-api.exe");
    if !executable.is_file() {
        return Err("应用运行组件不完整，请重新安装万象成文。".into());
    }
    Ok((Command::new(executable), sidecar_dir))
}

fn spawn_backend(app: &AppHandle) -> Result<(Child, DesktopRuntimeInfo), String> {
    let port = choose_loopback_port()?;
    let token = uuid::Uuid::new_v4().simple().to_string();
    let (mut command, working_dir) = sidecar_command(app)?;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("无法定位应用资源目录：{error}"))?;
    let workspace_dir = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("无法定位应用数据目录：{error}"))?
        .join("workspace");
    command
        .current_dir(working_dir)
        .env("AUDIO2TEXT_API_HOST", "127.0.0.1")
        .env("AUDIO2TEXT_API_PORT", port.to_string())
        .env("AUDIO2TEXT_RUNTIME_TARGET", "windows_desktop")
        .env(
            "AUDIO2TEXT_DESKTOP_PARENT_PID",
            std::process::id().to_string(),
        )
        .env("AUDIO2TEXT_TRANSCRIPTION_PROVIDER", "auto")
        .env("AUDIO2TEXT_DESKTOP_TOKEN", &token)
        .env("AUDIO2TEXT_WORKSPACE_DIR", workspace_dir)
        .env(
            "AUDIO2TEXT_RUNTIME_PACK_MANIFEST",
            resource_dir.join("runtime-packs.json"),
        )
        .env(
            "AUDIO2TEXT_ALLOWED_ORIGINS",
            "http://tauri.localhost,https://tauri.localhost,tauri://localhost",
        )
        .env("AUDIO2TEXT_ALLOW_DEGRADED_START", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let child = command
        .spawn()
        .map_err(|error| format!("无法启动本地处理服务：{error}"))?;
    let runtime = DesktopRuntimeInfo {
        api_base: format!("http://127.0.0.1:{port}"),
        token,
    };
    Ok((child, runtime))
}

fn wait_for_backend(runtime: &DesktopRuntimeInfo, timeout: Duration) -> bool {
    let address: SocketAddr = match runtime.api_base.trim_start_matches("http://").parse() {
        Ok(address) => address,
        Err(_) => return false,
    };
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(100));
    }
    false
}

#[tauri::command]
fn desktop_runtime_info(state: State<'_, DesktopState>) -> Result<DesktopRuntimeInfo, String> {
    state
        .runtime
        .lock()
        .map_err(|_| "桌面运行状态暂时不可用。".to_string())?
        .clone()
        .ok_or_else(|| "本地处理服务尚未准备完成。".to_string())
}

#[tauri::command]
fn diagnostics_folder(app: AppHandle) -> Result<String, String> {
    app.path()
        .app_local_data_dir()
        .map(|path| path.join("workspace").join("logs").display().to_string())
        .map_err(|error| format!("无法定位诊断目录：{error}"))
}

fn stop_backend(state: &DesktopState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(child) = guard.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        *guard = None;
    }
}

#[tauri::command]
fn restart_backend(app: AppHandle, state: State<'_, DesktopState>) -> Result<(), String> {
    stop_backend(&state);
    let (mut child, runtime) = spawn_backend(&app)?;
    if !wait_for_backend(&runtime, Duration::from_secs(8)) {
        let _ = child.kill();
        let _ = child.wait();
        return Err("本地处理服务重新启动超时。".into());
    }
    *state.child.lock().map_err(|_| "本地服务状态不可用。")? = Some(child);
    *state.runtime.lock().map_err(|_| "本地运行状态不可用。")? = Some(runtime);
    Ok(())
}

#[tauri::command]
fn set_background_task_active(active: bool, state: State<'_, DesktopState>) -> Result<(), String> {
    *state
        .background_task_active
        .lock()
        .map_err(|_| "任务状态不可用。")? = active;
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .manage(DesktopState::default())
        .invoke_handler(tauri::generate_handler![
            desktop_runtime_info,
            diagnostics_folder,
            restart_backend,
            set_background_task_active
        ])
        .setup(|app| {
            let open_item = MenuItem::with_id(app, "open", "打开万象成文", true, None::<&str>)?;
            let diagnostics_item =
                MenuItem::with_id(app, "diagnostics", "导出诊断", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_item, &diagnostics_item, &quit_item])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().expect("application icon").clone())
                .tooltip("万象成文")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "diagnostics" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.emit("desktop:diagnostics-requested", ());
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            let (child, runtime) = spawn_backend(app.handle()).map_err(std::io::Error::other)?;
            if !wait_for_backend(&runtime, Duration::from_secs(5)) {
                return Err(
                    std::io::Error::other("本地处理服务启动超时，请导出诊断后重新启动。").into(),
                );
            }
            let state = app.state::<DesktopState>();
            *state
                .child
                .lock()
                .map_err(|_| std::io::Error::other("backend child lock"))? = Some(child);
            *state
                .runtime
                .lock()
                .map_err(|_| std::io::Error::other("runtime lock"))? = Some(runtime);
            if let Some(window) = app.get_webview_window("main") {
                window.show()?;
                window.set_focus()?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<DesktopState>();
                let has_active_task = state
                    .background_task_active
                    .lock()
                    .map(|guard| *guard)
                    .unwrap_or(false);
                if has_active_task {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Wanxiang Chengwen")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
            ) {
                let state = app.state::<DesktopState>();
                stop_backend(&state);
            }
        });
}
