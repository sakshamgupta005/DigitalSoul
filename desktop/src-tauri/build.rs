fn main() {
    let mut windows = tauri_build::WindowsAttributes::new();
    let temp_icon = std::env::temp_dir().join("digitalsoul_icon.ico");
    if let Ok(icon_data) = std::fs::read("icons/icon.ico") {
        let _ = std::fs::write(&temp_icon, icon_data);
        windows = windows.window_icon_path(temp_icon);
    }
    let attrs = tauri_build::Attributes::new().windows_attributes(windows);
    tauri_build::try_build(attrs).expect("failed to run tauri build");
}
