#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// Return true when this process is the first (and only) Konggu instance.
///
/// Two desktop instances share the same SQLite state and OCR worker: the second
/// one marks the first one's running jobs as interrupted and drops their source
/// paths, so the first window ends up with half-updated state. A named mutex
/// keeps a single instance alive per user session.
#[cfg(windows)]
fn acquire_single_instance() -> bool {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
    use windows_sys::Win32::System::Threading::CreateMutexW;

    let name: Vec<u16> = OsStr::new("Local\\KongguDesktopSingleInstance")
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        let handle = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        if handle.is_null() {
            // Cannot create the mutex: fail open instead of blocking startup.
            return true;
        }
        // The handle is intentionally leaked: the OS releases it when the process exits.
        GetLastError() != ERROR_ALREADY_EXISTS
    }
}

#[cfg(not(windows))]
fn acquire_single_instance() -> bool {
    true
}

fn main() {
    if !acquire_single_instance() {
        eprintln!("空谷已经在运行：请使用已打开的窗口，避免两个窗口同时处理课表。");
        std::process::exit(0);
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .run(tauri::generate_context!())
        .expect("error while running Konggu");
}
