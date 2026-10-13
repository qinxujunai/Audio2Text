use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fs,
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, State,
};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone, Serialize)]
struct DesktopRuntimeInfo {
    api_base: String,
    token: String,
}

#[derive(Clone, Serialize)]
struct DesktopBackendStatus {
    status: &'static str,
    message: Option<String>,
}

#[derive(Clone, Serialize)]
struct SavedArtifactInfo {
    file_name: String,
}

#[derive(Deserialize)]
struct StoredCapture {
    title: Option<String>,
    source: Option<StoredSource>,
    result: Option<StoredResult>,
}

#[derive(Deserialize)]
struct StoredSource {
    canonical_url: Option<String>,
}

#[derive(Deserialize)]
struct StoredResult {
    artifacts: Vec<StoredArtifact>,
}

#[derive(Deserialize)]
struct StoredArtifact {
    #[serde(rename = "type")]
    artifact_type: String,
    path: String,
    status: Option<String>,
}

#[derive(Default)]
struct DesktopState {
    runtime: Mutex<Option<DesktopRuntimeInfo>>,
    child: Mutex<Option<Child>>,
    background_task_active: Mutex<bool>,
    backend_status: Mutex<String>,
    saved_artifacts: Mutex<HashMap<String, PathBuf>>,
    shutting_down: AtomicBool,
}

fn validate_identifier(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 96
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err("内容标识无效。".into());
    }
    Ok(())
}

fn sanitize_file_stem(value: &str) -> String {
    let mut output: String = value
        .trim()
        .chars()
        .map(|character| {
            if character.is_control()
                || matches!(
                    character,
                    '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*'
                )
            {
                '_'
            } else {
                character
            }
        })
        .take(80)
        .collect();
    output = output
        .trim_matches(|character| character == ' ' || character == '.')
        .to_string();
    let reserved = [
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
        "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ];
    if output.is_empty()
        || reserved
            .iter()
            .any(|item| output.eq_ignore_ascii_case(item))
    {
        "万象成文".into()
    } else {
        output
    }
}

fn next_available_path(requested: &Path) -> PathBuf {
    if !requested.exists() {
        return requested.to_path_buf();
    }
    let parent = requested.parent().unwrap_or_else(|| Path::new("."));
    let stem = requested
        .file_stem()
        .and_then(|item| item.to_str())
        .unwrap_or("万象成文");
    let extension = requested
        .extension()
        .and_then(|item| item.to_str())
        .unwrap_or("");
    for index in 2..=10_000 {
        let file_name = if extension.is_empty() {
            format!("{stem} ({index})")
        } else {
            format!("{stem} ({index}).{extension}")
        };
        let candidate = parent.join(file_name);
        if !candidate.exists() {
            return candidate;
        }
    }
    parent.join(format!("{stem}-{}", uuid::Uuid::new_v4().simple()))
}

fn artifact_key(capture_id: &str, artifact_type: &str) -> String {
    format!("{capture_id}:{artifact_type}")
}

fn workspace_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|path| path.join("workspace"))
        .map_err(|error| format!("无法定位应用数据目录：{error}"))
}

fn resolve_artifact(
    app: &AppHandle,
    capture_id: &str,
    artifact_type: &str,
) -> Result<(PathBuf, String), String> {
    validate_identifier(capture_id)?;
    validate_identifier(artifact_type)?;
    let workspace = workspace_dir(app)?;
    let capture_path = workspace
        .join("captures")
        .join(capture_id)
        .join("capture.json");
    let raw = fs::read_to_string(&capture_path)
        .map_err(|_| "没有找到这条内容的本地记录。".to_string())?;
    let capture: StoredCapture =
        serde_json::from_str(&raw).map_err(|_| "这条内容的本地记录已损坏。".to_string())?;
    let artifact = capture
        .result
        .and_then(|result| {
            result
                .artifacts
                .into_iter()
                .find(|item| item.artifact_type == artifact_type)
        })
        .ok_or_else(|| "这个文件尚未准备完成。".to_string())?;
    if artifact.status.as_deref().unwrap_or("ready") != "ready" {
        return Err("这个文件尚未准备完成。".into());
    }
    let source = PathBuf::from(artifact.path)
        .canonicalize()
        .map_err(|_| "这个文件暂时不可用，请重新处理后再试。".to_string())?;
    let workspace = workspace
        .canonicalize()
        .map_err(|_| "本地工作目录暂时不可用。".to_string())?;
    if !source.starts_with(&workspace) || !source.is_file() {
        return Err("这个文件不在万象成文的安全目录中。".into());
    }
    let extension = source
        .extension()
        .and_then(|item| item.to_str())
        .unwrap_or("");
    let mut file_name = sanitize_file_stem(capture.title.as_deref().unwrap_or("万象成文"));
    if !extension.is_empty() {
        file_name.push('.');
        file_name.push_str(extension);
    }
    Ok((source, file_name))
}

fn stored_capture(app: &AppHandle, capture_id: &str) -> Result<StoredCapture, String> {
    validate_identifier(capture_id)?;
    let capture_path = workspace_dir(app)?
        .join("captures")
        .join(capture_id)
        .join("capture.json");
    let raw =
        fs::read_to_string(capture_path).map_err(|_| "没有找到这条内容的本地记录。".to_string())?;
    serde_json::from_str(&raw).map_err(|_| "这条内容的本地记录已损坏。".to_string())
}

fn validated_public_source_url(capture: &StoredCapture) -> Result<String, String> {
    let raw = capture
        .source
        .as_ref()
        .and_then(|source| source.canonical_url.as_deref())
        .ok_or_else(|| "这条内容没有可打开的来源页面。".to_string())?;
    let parsed = url::Url::parse(raw).map_err(|_| "来源地址无效。".to_string())?;
    if parsed.scheme() != "https" || parsed.host_str().is_none() {
        return Err("只允许打开安全的公开来源页面。".into());
    }
    Ok(parsed.to_string())
}

fn remember_saved_artifact(
    state: &DesktopState,
    capture_id: &str,
    artifact_type: &str,
    target: PathBuf,
) -> Result<SavedArtifactInfo, String> {
    let file_name = target
        .file_name()
        .and_then(|item| item.to_str())
        .unwrap_or("已保存文件")
        .to_string();
    state
        .saved_artifacts
        .lock()
        .map_err(|_| "保存状态暂时不可用。".to_string())?
        .insert(artifact_key(capture_id, artifact_type), target);
    Ok(SavedArtifactInfo { file_name })
}

fn last_saved_artifact(
    state: &DesktopState,
    capture_id: &str,
    artifact_type: &str,
) -> Result<PathBuf, String> {
    validate_identifier(capture_id)?;
    validate_identifier(artifact_type)?;
    let path = state
        .saved_artifacts
        .lock()
        .map_err(|_| "保存状态暂时不可用。".to_string())?
        .get(&artifact_key(capture_id, artifact_type))
        .cloned()
        .ok_or_else(|| "请先保存这个文件。".to_string())?;
    if !path.is_file() {
        return Err("保存的文件已被移动或删除。".into());
    }
    Ok(path)
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
        .env("AUDIO2TEXT_VERSION", env!("CARGO_PKG_VERSION"))
        .env(
            "AUDIO2TEXT_DESKTOP_PARENT_PID",
            std::process::id().to_string(),
        )
        .env("AUDIO2TEXT_TRANSCRIPTION_PROVIDER", "auto")
        .env("AUDIO2TEXT_DEVICE", "auto")
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
fn desktop_backend_status(state: State<'_, DesktopState>) -> Result<DesktopBackendStatus, String> {
    let status = state
        .backend_status
        .lock()
        .map_err(|_| "桌面运行状态暂时不可用。".to_string())?
        .clone();
    Ok(DesktopBackendStatus {
        status: match status.as_str() {
            "ready" => "ready",
            "failed" => "failed",
            _ => "starting",
        },
        message: None,
    })
}

#[tauri::command]
fn save_artifact(
    app: AppHandle,
    state: State<'_, DesktopState>,
    capture_id: String,
    artifact_type: String,
) -> Result<SavedArtifactInfo, String> {
    let (source, file_name) = resolve_artifact(&app, &capture_id, &artifact_type)?;
    let downloads = app
        .path()
        .download_dir()
        .map_err(|_| "无法定位系统下载目录，请使用另存为。".to_string())?
        .join("万象成文");
    fs::create_dir_all(&downloads)
        .map_err(|_| "无法创建万象成文下载目录，请检查磁盘权限。".to_string())?;
    let target = next_available_path(&downloads.join(file_name));
    fs::copy(source, &target).map_err(|error| format!("保存没有完成：{error}"))?;
    remember_saved_artifact(&state, &capture_id, &artifact_type, target)
}

#[tauri::command]
fn save_text_export(app: AppHandle, capture_id: String, version: String, text: String) -> Result<SavedArtifactInfo, String> {
    validate_identifier(&capture_id)?;
    if !matches!(version.as_str(), "original" | "corrected") || text.len() > 4_000_000 {
        return Err("文本版本或长度无效。".into());
    }
    let capture = stored_capture(&app, &capture_id)?;
    let label = if version == "corrected" { "校对版" } else { "原文" };
    let name = format!("{}_{}.txt", sanitize_file_stem(capture.title.as_deref().unwrap_or("万象成文")), label);
    let directory = app.path().download_dir().map_err(|_| "无法定位系统下载目录。".to_string())?.join("万象成文");
    fs::create_dir_all(&directory).map_err(|_| "无法创建下载目录。".to_string())?;
    let target = next_available_path(&directory.join(&name));
    use std::io::Write;
    let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&target).map_err(|_| "无法保存文本，请检查目录权限。".to_string())?;
    file.write_all(format!("\u{feff}{text}").as_bytes()).map_err(|_| "文本保存未完成。".to_string())?;
    Ok(SavedArtifactInfo { file_name: target.file_name().unwrap_or_default().to_string_lossy().into_owned() })
}

#[tauri::command]
async fn save_artifact_as(
    app: AppHandle,
    state: State<'_, DesktopState>,
    capture_id: String,
    artifact_type: String,
) -> Result<Option<SavedArtifactInfo>, String> {
    let (source, file_name) = resolve_artifact(&app, &capture_id, &artifact_type)?;
    let selection = app
        .dialog()
        .file()
        .set_title("另存为")
        .set_file_name(&file_name)
        .blocking_save_file();
    let Some(selection) = selection else {
        return Ok(None);
    };
    let target = selection
        .into_path()
        .map_err(|_| "选择的位置无法用于保存文件。".to_string())?;
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent).map_err(|_| "无法创建所选目录。".to_string())?;
    }
    fs::copy(source, &target).map_err(|error| format!("保存没有完成：{error}"))?;
    remember_saved_artifact(&state, &capture_id, &artifact_type, target).map(Some)
}

#[tauri::command]
fn open_saved_artifact(
    app: AppHandle,
    state: State<'_, DesktopState>,
    capture_id: String,
    artifact_type: String,
) -> Result<(), String> {
    let target = last_saved_artifact(&state, &capture_id, &artifact_type)?;
    app.opener()
        .open_path(target.to_string_lossy().to_string(), None::<&str>)
        .map_err(|_| "系统暂时无法打开这个文件。".to_string())
}

#[tauri::command]
fn open_capture_source(app: AppHandle, capture_id: String) -> Result<(), String> {
    let capture = stored_capture(&app, &capture_id)?;
    let source_url = validated_public_source_url(&capture)?;
    app.opener()
        .open_url(source_url, None::<&str>)
        .map_err(|_| "系统暂时无法打开来源页面。".to_string())
}

#[tauri::command]
fn reveal_saved_artifact(
    app: AppHandle,
    state: State<'_, DesktopState>,
    capture_id: String,
    artifact_type: String,
) -> Result<(), String> {
    let target = last_saved_artifact(&state, &capture_id, &artifact_type)?;
    app.opener()
        .reveal_item_in_dir(target)
        .map_err(|_| "系统暂时无法定位这个文件。".to_string())
}

#[tauri::command]
fn diagnostics_folder(app: AppHandle) -> Result<String, String> {
    app.path()
        .app_local_data_dir()
        .map(|path| path.join("workspace").join("logs").display().to_string())
        .map_err(|error| format!("无法定位诊断目录：{error}"))
}

#[tauri::command]
fn reveal_diagnostics_folder(app: AppHandle) -> Result<(), String> {
    let path = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("无法定位诊断目录：{error}"))?
        .join("workspace")
        .join("logs");
    fs::create_dir_all(&path).map_err(|_| "无法准备诊断目录。".to_string())?;
    app.opener()
        .open_path(path.to_string_lossy().to_string(), None::<&str>)
        .map_err(|_| "系统暂时无法打开诊断目录。".to_string())
}

fn find_edge_executable() -> Option<PathBuf> {
    ["PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"]
        .into_iter()
        .filter_map(std::env::var_os)
        .map(PathBuf::from)
        .map(|root| {
            root.join("Microsoft")
                .join("Edge")
                .join("Application")
                .join("msedge.exe")
        })
        .find(|candidate| candidate.is_file())
}

#[tauri::command]
fn open_platform_session(app: AppHandle, platform: String) -> Result<(), String> {
    let url = match platform.as_str() {
        "xiaohongshu" => "https://www.xiaohongshu.com/",
        "douyin" => "https://www.douyin.com/",
        _ => return Err("当前平台不支持会话更新。".into()),
    };
    let executable = find_edge_executable()
        .ok_or_else(|| "未找到 Microsoft Edge，请先完成系统更新。".to_string())?;
    let profile_dir = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("无法定位应用数据目录：{error}"))?
        .join("workspace")
        .join("runtime")
        .join("browser-profile");
    std::fs::create_dir_all(&profile_dir)
        .map_err(|error| format!("无法准备平台会话目录：{error}"))?;
    Command::new(executable)
        .arg(format!("--user-data-dir={}", profile_dir.display()))
        .arg("--no-first-run")
        .arg(url)
        .spawn()
        .map_err(|error| format!("无法打开平台页面：{error}"))?;
    Ok(())
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
    *state
        .backend_status
        .lock()
        .map_err(|_| "本地服务状态不可用。")? = "starting".into();
    let _ = app.emit(
        "desktop:backend-status",
        DesktopBackendStatus {
            status: "starting",
            message: None,
        },
    );
    stop_backend(&state);
    let (mut child, runtime) = spawn_backend(&app)?;
    if !wait_for_backend(&runtime, Duration::from_secs(8)) {
        let _ = child.kill();
        let _ = child.wait();
        *state
            .backend_status
            .lock()
            .map_err(|_| "本地服务状态不可用。")? = "failed".into();
        let message = "本地处理服务重新启动超时。".to_string();
        let _ = app.emit(
            "desktop:backend-status",
            DesktopBackendStatus {
                status: "failed",
                message: Some(message.clone()),
            },
        );
        return Err(message);
    }
    *state.child.lock().map_err(|_| "本地服务状态不可用。")? = Some(child);
    *state.runtime.lock().map_err(|_| "本地运行状态不可用。")? = Some(runtime);
    *state
        .backend_status
        .lock()
        .map_err(|_| "本地服务状态不可用。")? = "ready".into();
    let _ = app.emit(
        "desktop:backend-status",
        DesktopBackendStatus {
            status: "ready",
            message: None,
        },
    );
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
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(DesktopState::default())
        .invoke_handler(tauri::generate_handler![
            desktop_runtime_info,
            desktop_backend_status,
            diagnostics_folder,
            open_platform_session,
            open_capture_source,
            open_saved_artifact,
            reveal_diagnostics_folder,
            reveal_saved_artifact,
            restart_backend,
            save_artifact,
            save_artifact_as,
            save_text_export,
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

            if let Some(window) = app.get_webview_window("main") {
                window.show()?;
                window.set_focus()?;
            }
            let handle = app.handle().clone();
            let _ = handle.emit(
                "desktop:backend-status",
                DesktopBackendStatus {
                    status: "starting",
                    message: None,
                },
            );
            thread::spawn(move || {
                let status = match spawn_backend(&handle) {
                    Ok((mut child, runtime))
                        if wait_for_backend(&runtime, Duration::from_secs(8)) =>
                    {
                        let state = handle.state::<DesktopState>();
                        if state.shutting_down.load(Ordering::Acquire) {
                            let _ = child.kill();
                            let _ = child.wait();
                            return;
                        }
                        if let Ok(mut guard) = state.child.lock() {
                            *guard = Some(child);
                        }
                        if let Ok(mut guard) = state.runtime.lock() {
                            *guard = Some(runtime);
                        }
                        if let Ok(mut guard) = state.backend_status.lock() {
                            *guard = "ready".into();
                        }
                        DesktopBackendStatus {
                            status: "ready",
                            message: None,
                        }
                    }
                    Ok((mut child, _)) => {
                        let _ = child.kill();
                        let _ = child.wait();
                        if let Ok(mut guard) = handle.state::<DesktopState>().backend_status.lock()
                        {
                            *guard = "failed".into();
                        }
                        DesktopBackendStatus {
                            status: "failed",
                            message: Some("本地处理服务启动超时，请重新启动或导出诊断。".into()),
                        }
                    }
                    Err(message) => {
                        if let Ok(mut guard) = handle.state::<DesktopState>().backend_status.lock()
                        {
                            *guard = "failed".into();
                        }
                        DesktopBackendStatus {
                            status: "failed",
                            message: Some(message),
                        }
                    }
                };
                let _ = handle.emit("desktop:backend-status", status);
            });
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
                } else {
                    window.app_handle().exit(0);
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
                state.shutting_down.store(true, Ordering::Release);
                stop_backend(&state);
            }
        });
}

#[cfg(test)]
mod tests {
    use super::{
        next_available_path, sanitize_file_stem, validate_identifier, validated_public_source_url,
        StoredCapture, StoredSource,
    };
    use std::fs;

    #[test]
    fn identifier_validation_rejects_paths_and_accepts_capture_keys() {
        assert!(validate_identifier("capture-2026_08").is_ok());
        assert!(validate_identifier("../capture").is_err());
        assert!(validate_identifier("capture/image").is_err());
        assert!(validate_identifier("").is_err());
    }

    #[test]
    fn file_stem_is_safe_for_windows_downloads() {
        assert_eq!(sanitize_file_stem("  标题: 第一集?  "), "标题_ 第一集_");
        assert_eq!(sanitize_file_stem("CON"), "万象成文");
        assert_eq!(sanitize_file_stem("..."), "万象成文");
    }

    #[test]
    fn duplicate_names_receive_a_stable_suffix() {
        let root = std::env::temp_dir().join(format!("wanxiang-save-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).expect("temp directory");
        let requested = root.join("节目.m4a");
        fs::write(&requested, b"one").expect("seed file");
        assert_eq!(next_available_path(&requested), root.join("节目 (2).m4a"));
        fs::remove_dir_all(&root).expect("cleanup");
    }

    #[test]
    fn source_opening_accepts_https_and_rejects_unsafe_schemes() {
        let capture = |url: &str| StoredCapture {
            title: None,
            source: Some(StoredSource {
                canonical_url: Some(url.into()),
            }),
            result: None,
        };
        assert_eq!(
            validated_public_source_url(&capture("https://www.xiaoyuzhoufm.com/episode/123"))
                .expect("https source"),
            "https://www.xiaoyuzhoufm.com/episode/123"
        );
        assert!(validated_public_source_url(&capture("file:///C:/Windows/win.ini")).is_err());
        assert!(validated_public_source_url(&capture("javascript:alert(1)")).is_err());
    }
}
