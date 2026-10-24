//! Generate the explicit finite application ACL and bundled-asset context.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["gui_snapshot", "gui_action", "gui_detail"]),
    ))?;
    Ok(())
}
