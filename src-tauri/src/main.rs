#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

mod bot;
mod token_extract;

use bot::BotState;
use tauri::Manager;

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            app.manage(BotState::new(app.handle().clone()));
            let handle = app.handle().clone();
            let child = {
                let state = app.state::<BotState>();
                state.child.clone()
            };
            tauri::async_runtime::spawn(bot::watch_state(handle, child));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bot::app_config,
            bot::write_env_multi,
            bot::account_profile,
            bot::scan_tokens,
            bot::start_bot,
            bot::restart_bot,
            bot::stop_bot,
            bot::terminate_bot,
            bot::bot_status,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(state) = app_handle.try_state::<BotState>() {
                bot::kill_on_exit(&state);
            }
        }
    });
}
