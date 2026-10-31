//! Automatic Discord account detection.
//!
//! Reads the encrypted token the Discord desktop client stores in its Chromium
//! profile (Local State -> DPAPI-protected AES key, Local Storage/leveldb ->
//! AES-256-GCM token payloads), then resolves the profile through the Discord API.
//! This mirrors what the reference Discord Quest Helper does natively, so account
//! detection no longer depends on a Python interpreter being installed.

use std::time::Duration;

use base64::{engine::general_purpose::STANDARD as BASE64, Engine};
use serde::Serialize;

#[cfg(target_os = "windows")]
use std::collections::HashSet;
#[cfg(target_os = "windows")]
use std::path::{Path, PathBuf};

const API_ME: &str = "https://discord.com/api/v9/users/@me";
const USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9184 Chrome/134.0.6998.205 Electron/35.2.1 Safari/537.36";

/// Folder under %APPDATA% and the label shown in the account list.
#[cfg(target_os = "windows")]
const CLIENTS: [(&str, &str); 6] = [
    ("discord", "Discord"),
    ("discordcanary", "Discord Canary"),
    ("discordptb", "Discord PTB"),
    ("discorddevelopment", "Discord Development"),
    ("vesktop", "Vesktop"),
    ("Lightcord", "Lightcord"),
];

#[derive(Debug, Clone, Default, Serialize)]
pub struct Profile {
    pub id: Option<String>,
    pub username: Option<String>,
    pub global_name: Option<String>,
    pub avatar_url: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct DetectedAccount {
    pub client: String,
    pub token: String,
    #[serde(flatten)]
    pub profile: Profile,
}

#[derive(Debug, Default, Serialize)]
pub struct ScanReport {
    pub accounts: Vec<DetectedAccount>,
    pub notes: Vec<String>,
}

/// Scan every known Discord profile and attach the matching Discord profile.
pub async fn scan_with_profiles() -> ScanReport {
    let (found, mut notes) = scan_local_profiles();
    let mut accounts = Vec::new();
    if found.is_empty() {
        return ScanReport { accounts, notes };
    }

    let client = match build_http_client() {
        Ok(client) => Some(client),
        Err(error) => {
            notes.push(error);
            None
        }
    };

    for (label, token) in found {
        let profile = match &client {
            Some(client) => match fetch_profile_with(client, &token).await {
                Ok(profile) => profile,
                Err(error) => {
                    notes.push(format!(
                        "{label}: token found, but the profile lookup failed ({error})"
                    ));
                    Profile::default()
                }
            },
            None => Profile::default(),
        };
        accounts.push(DetectedAccount { client: label, token, profile });
    }

    ScanReport { accounts, notes }
}

/// Fetch `GET /users/@me` for a token.
pub async fn fetch_profile(token: &str) -> Result<Profile, String> {
    let client = build_http_client()?;
    fetch_profile_with(&client, token).await
}

fn build_http_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .user_agent(USER_AGENT)
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|error| format!("could not create HTTP client: {error}"))
}

async fn fetch_profile_with(client: &reqwest::Client, token: &str) -> Result<Profile, String> {
    let response = client
        .get(API_ME)
        .header("Authorization", token)
        .send()
        .await
        .map_err(|error| format!("network error: {error}"))?;

    let status = response.status();
    if status.as_u16() == 401 || status.as_u16() == 403 {
        return Err("Discord rejected this token".to_string());
    }
    if !status.is_success() {
        return Err(format!("Discord replied with HTTP {}", status.as_u16()));
    }

    let body: serde_json::Value = response
        .json()
        .await
        .map_err(|error| format!("could not read Discord's response: {error}"))?;
    Ok(profile_from_json(&body))
}

fn profile_from_json(value: &serde_json::Value) -> Profile {
    let id = value
        .get("id")
        .and_then(|field| field.as_str())
        .unwrap_or_default()
        .to_string();
    let username = value
        .get("username")
        .and_then(|field| field.as_str())
        .map(str::to_string);
    let global_name = value
        .get("global_name")
        .and_then(|field| field.as_str())
        .map(str::to_string);
    let avatar = value
        .get("avatar")
        .and_then(|field| field.as_str())
        .filter(|hash| !hash.is_empty());

    let avatar_url = if id.is_empty() {
        None
    } else {
        Some(match avatar {
            Some(hash) => format!("https://cdn.discordapp.com/avatars/{id}/{hash}.png?size=128"),
            None => {
                let index = id.parse::<u64>().map(|snowflake| (snowflake >> 22) % 6).unwrap_or(0);
                format!("https://cdn.discordapp.com/embed/avatars/{index}.png")
            }
        })
    };

    Profile {
        id: (!id.is_empty()).then_some(id),
        username,
        global_name,
        avatar_url,
    }
}

#[cfg(target_os = "windows")]
fn scan_local_profiles() -> (Vec<(String, String)>, Vec<String>) {
    let mut found = Vec::new();
    let mut notes = Vec::new();
    let mut seen = HashSet::new();

    let Some(appdata) = std::env::var_os("APPDATA").map(PathBuf::from) else {
        notes.push("APPDATA is not set, so Discord profiles cannot be scanned.".to_string());
        return (found, notes);
    };

    let mut any_profile = false;
    for (folder, label) in CLIENTS {
        let profile_dir = appdata.join(folder);
        if !profile_dir.is_dir() {
            continue;
        }
        any_profile = true;
        match tokens_from_profile(&profile_dir) {
            Ok(tokens) => {
                if tokens.is_empty() {
                    notes.push(format!(
                        "{label}: no saved token found. Open Discord, sign in, then detect again."
                    ));
                }
                for token in tokens {
                    if seen.insert(token.clone()) {
                        found.push((label.to_string(), token));
                    }
                }
            }
            Err(reason) => notes.push(format!("{label}: {reason}")),
        }
    }

    if !any_profile {
        notes.push(
            "No Discord desktop profile found in %APPDATA%. Install Discord and sign in first."
                .to_string(),
        );
    }

    (found, notes)
}

#[cfg(not(target_os = "windows"))]
fn scan_local_profiles() -> (Vec<(String, String)>, Vec<String>) {
    (
        Vec::new(),
        vec!["Automatic token detection is only available on Windows.".to_string()],
    )
}

#[cfg(target_os = "windows")]
fn tokens_from_profile(profile_dir: &Path) -> Result<Vec<String>, String> {
    let state_path = profile_dir.join("Local State");
    let leveldb_path = profile_dir.join("Local Storage").join("leveldb");

    if !state_path.is_file() {
        return Err("Local State file is missing".to_string());
    }

    let state_text = std::fs::read_to_string(&state_path)
        .map_err(|error| format!("could not read Local State ({error})"))?;
    let state: serde_json::Value = serde_json::from_str(&state_text)
        .map_err(|_| "Local State is not valid JSON".to_string())?;
    let encoded_key = state
        .pointer("/os_crypt/encrypted_key")
        .and_then(|value| value.as_str())
        .ok_or("Local State has no os_crypt.encrypted_key")?;
    let key_blob = BASE64
        .decode(encoded_key)
        .map_err(|_| "encrypted_key is not valid base64".to_string())?;
    let key_blob = key_blob
        .strip_prefix(b"DPAPI")
        .ok_or("encrypted_key is not DPAPI-protected")?;
    let master_key = unprotect(key_blob)?;

    if !leveldb_path.is_dir() {
        return Err("Local Storage/leveldb folder is missing".to_string());
    }

    let pattern = regex::Regex::new(r"dQw4w9WgXcQ:([A-Za-z0-9+/=]+)")
        .expect("token regex is valid");
    let mut tokens: Vec<String> = Vec::new();
    let mut saw_payload = false;

    let entries = std::fs::read_dir(&leveldb_path)
        .map_err(|error| format!("could not read leveldb ({error})"))?;
    for entry in entries.flatten() {
        let path = entry.path();
        let is_data = matches!(
            path.extension().and_then(|extension| extension.to_str()),
            Some("ldb") | Some("log")
        );
        if !is_data {
            continue;
        }
        let Ok(data) = std::fs::read(&path) else {
            continue;
        };
        let content = String::from_utf8_lossy(&data);
        for capture in pattern.captures_iter(&content) {
            let Some(payload_match) = capture.get(1) else {
                continue;
            };
            let Ok(payload) = BASE64.decode(payload_match.as_str()) else {
                continue;
            };
            saw_payload = true;
            if let Some(token) = decrypt_token(&payload, &master_key) {
                if !tokens.contains(&token) {
                    tokens.push(token);
                }
            }
        }
    }

    if tokens.is_empty() && saw_payload {
        return Err("found an encrypted token but could not decrypt it".to_string());
    }

    Ok(tokens)
}

#[cfg(target_os = "windows")]
fn decrypt_token(payload: &[u8], key: &[u8]) -> Option<String> {
    use aes_gcm::aead::{Aead, KeyInit};
    use aes_gcm::{Aes256Gcm, Nonce};

    if payload.len() <= 15 {
        return None;
    }
    if &payload[..3] != b"v10" && &payload[..3] != b"v11" {
        return None;
    }

    let cipher = Aes256Gcm::new_from_slice(key).ok()?;
    let nonce = Nonce::from_slice(&payload[3..15]);
    let plaintext = cipher.decrypt(nonce, &payload[15..]).ok()?;
    let token = String::from_utf8(plaintext).ok()?;

    let looks_like_token = token.len() >= 40
        && token.len() <= 200
        && token.chars().all(|character| character.is_ascii_graphic());
    looks_like_token.then_some(token)
}

#[cfg(target_os = "windows")]
#[repr(C)]
struct DataBlob {
    cb_data: u32,
    pb_data: *mut u8,
}

#[cfg(target_os = "windows")]
#[link(name = "crypt32")]
extern "system" {
    fn CryptUnprotectData(
        data_in: *const DataBlob,
        description: *mut *mut u16,
        entropy: *const DataBlob,
        reserved: *mut core::ffi::c_void,
        prompt: *mut core::ffi::c_void,
        flags: u32,
        data_out: *mut DataBlob,
    ) -> i32;
}

#[cfg(target_os = "windows")]
#[link(name = "kernel32")]
extern "system" {
    fn LocalFree(memory: *mut core::ffi::c_void) -> *mut core::ffi::c_void;
}

#[cfg(test)]
mod tests {
    /// Manual check against the Discord client signed in on this machine.
    /// Prints counts and token lengths only, never the tokens themselves.
    #[test]
    #[ignore = "reads the signed-in Discord client on this machine"]
    fn detects_local_accounts() {
        let (tokens, notes) = super::scan_local_profiles();
        println!("detected accounts: {}", tokens.len());
        for (client, token) in &tokens {
            println!("{client}: token length {}", token.len());
        }
        for note in notes {
            println!("note: {note}");
        }
    }

    /// Manual end-to-end check: detection plus the Discord profile lookup.
    #[test]
    #[ignore = "calls the Discord API with the token signed in on this machine"]
    fn resolves_local_account_profile() {
        let (tokens, _) = super::scan_local_profiles();
        let Some((_, token)) = tokens.first() else {
            println!("no local account to resolve");
            return;
        };
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("runtime");
        match runtime.block_on(super::fetch_profile(token)) {
            Ok(profile) => println!(
                "profile lookup ok: id {}, username {}, avatar {}",
                profile.id.is_some(),
                profile.username.is_some(),
                profile.avatar_url.is_some()
            ),
            Err(error) => println!("profile lookup failed: {error}"),
        }
    }
}

#[cfg(target_os = "windows")]
fn unprotect(data: &[u8]) -> Result<Vec<u8>, String> {
    if data.is_empty() {
        return Err("DPAPI blob is empty".to_string());
    }

    let input = DataBlob {
        cb_data: data.len() as u32,
        pb_data: data.as_ptr() as *mut u8,
    };
    let mut output = DataBlob { cb_data: 0, pb_data: std::ptr::null_mut() };

    let ok = unsafe {
        CryptUnprotectData(
            &input,
            std::ptr::null_mut(),
            std::ptr::null(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            0,
            &mut output,
        )
    };
    if ok == 0 {
        return Err("DPAPI decryption failed".to_string());
    }

    let bytes = unsafe {
        std::slice::from_raw_parts(output.pb_data, output.cb_data as usize).to_vec()
    };
    unsafe {
        LocalFree(output.pb_data as *mut core::ffi::c_void);
    }
    Ok(bytes)
}
