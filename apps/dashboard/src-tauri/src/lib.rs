mod sessions;

use std::path::PathBuf;
use std::process::Command;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, WindowEvent};

#[tauri::command]
fn dashboard_snapshot() -> Result<sessions::DashboardSnapshot, String> {
    sessions::load_snapshot().map_err(|error| error.to_string())
}

#[tauri::command]
fn open_in_codex(session_id: String, cwd: Option<String>) -> Result<(), String> {
    if session_id.trim().is_empty() {
        return Err("session id is empty".into());
    }

    #[cfg(target_os = "windows")]
    let mut command = {
        let mut value = Command::new("cmd.exe");
        value.args(["/D", "/C", "start", "", "codex", "resume", &session_id]);
        value
    };

    #[cfg(target_os = "macos")]
    let mut command = {
        let script = format!(
            "tell application \"Terminal\" to do script \"codex resume {}\"",
            session_id
        );
        let mut value = Command::new("osascript");
        value.args(["-e", &script]);
        value
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut value = Command::new("x-terminal-emulator");
        value.args(["-e", "codex", "resume", &session_id]);
        value
    };

    if let Some(path) = cwd.map(PathBuf::from).filter(|path| path.is_dir()) {
        command.current_dir(path);
    }
    command.spawn().map_err(|error| error.to_string())?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![dashboard_snapshot, open_in_codex])
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "Show ThreadRecap", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            let mut tray = TrayIconBuilder::new()
                .tooltip("ThreadRecap")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        if let Some(window) = tray.app_handle().get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ThreadRecap Dashboard");
}
