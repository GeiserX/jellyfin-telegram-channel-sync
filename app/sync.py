import os
import sqlite3
import requests
import time
from telethon.sync import TelegramClient

api_id = os.getenv('TELEGRAM_API_ID')
api_hash = os.getenv('TELEGRAM_API_HASH')
channel_username = int(os.getenv('TELEGRAM_CHANNEL'))

jellyfin_url = os.getenv('JELLYFIN_URL')
jellyfin_api_key = os.getenv('JELLYFIN_API_KEY')

interval = int(os.getenv('SCRIPT_INTERVAL', '3600'))

db_file = '/app/data/jellyfin_users.db'
telegram_session_file = '/app/data/session_name'

jf_headers = {'X-Emby-Token': jellyfin_api_key}

def get_jellyfin_users():
    resp = requests.get(f'{jellyfin_url}/Users', headers=jf_headers)
    resp.raise_for_status()
    users = resp.json()
    return {user['Name']: {'Id': user['Id'], 'IsDisabled': user['Policy']['IsDisabled']}
            for user in users if user['Name'].lower() != 'root'}

def set_jellyfin_user_enabled(user_id, username, enabled_state):
    url = f'{jellyfin_url}/Users/{user_id}/Policy'
    resp = requests.post(url, headers=jf_headers, json={"IsDisabled": not enabled_state})
    if resp.status_code == 204:
        print(f"✅ Jellyfin user '{username}' set enabled={enabled_state}.", flush=True)
    else:
        print(f"🚨 Error setting '{username}': {resp.status_code} {resp.text}", flush=True)

def fetch_telegram_users():
    client = TelegramClient(telegram_session_file, api_id, api_hash)
    client.connect()
    if not client.is_user_authorized():
        print("Telegram client is not authorized! Check your session file.", flush=True)
        client.disconnect()
        exit(1)
    print("Fetching Participants...", flush=True)
    participants = client.get_participants(channel_username)
    telegram_users = {}
    for user in participants:
        telegram_users[str(user.id)] = {
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or ""
        }
    print(f"Processed Participants: fetched {len(telegram_users)} users.", flush=True)
    client.disconnect()
    return telegram_users

def main():
    print("Fetching Jellyfin users...", flush=True)
    jellyfin_users = get_jellyfin_users()
    print("Jellyfin users fetched successfully.", flush=True)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT ID, JellyfinUser, Enabled FROM users")
    rows = cursor.fetchall()
    print("Loaded DB successfully.", flush=True)

    print("Fetching Telegram users...", flush=True)
    telegram_users_present = fetch_telegram_users()
    telegram_ids_present = set(telegram_users_present.keys())
    print(f"Telegram users fetched successfully ({len(telegram_users_present)} IDs).", flush=True)

    known_telegram_ids = set()
    updated_db = False

    for row in rows:
        jf_user = row['JellyfinUser']
        csv_ids = set(row['ID'].strip().split()) if row['ID'] else set()
        known_telegram_ids.update(csv_ids)

        if not csv_ids:
            print(f"⚠️ User '{jf_user}' has no Telegram IDs in DB; skipping check.", flush=True)
            continue

        user_present_in_channel = bool(csv_ids & telegram_ids_present)
        if bool(row['Enabled']) != user_present_in_channel:
            action_str = 'ENABLED ✅' if user_present_in_channel else 'DISABLED ⛔'
            print(f"➡️ User '{jf_user}' status changed from {bool(row['Enabled'])} to {user_present_in_channel} ({action_str}).", flush=True)

            cursor.execute("UPDATE users SET Enabled = ? WHERE JellyfinUser = ?", (int(user_present_in_channel), jf_user))
            conn.commit()
            updated_db = True

            if jf_user in jellyfin_users:
                set_jellyfin_user_enabled(jellyfin_users[jf_user]['Id'], jf_user, user_present_in_channel)
        else:
            print(f"🔸 No change for user '{jf_user}' (Enabled = {bool(row['Enabled'])}).", flush=True)

    unknown_tg_ids = telegram_ids_present - known_telegram_ids
    if unknown_tg_ids:
        print('\n🚨 Unrecognized Telegram users (add them to DB manually):', flush=True)
        for uid in unknown_tg_ids:
            user = telegram_users_present[uid]
            display_name = f"{user['first_name']} {user['last_name']}".strip() or user['username'] or "NoName"
            username_field = f"@{user['username']}" if user['username'] else "No Username"
            print(f"🔹 ID: {uid} | Name: {display_name} | Username: {username_field}", flush=True)
    else:
        print("\n✅ No unknown Telegram IDs present.", flush=True)

    if not updated_db:
        print("ℹ️ No DB updates needed this run.", flush=True)

    conn.close()

def main_loop():
    while True:
        try:
            print("Starting user synchronization...", flush=True)
            main()
        except Exception as e:
            print(f"An error occurred: {e}", flush=True)
        print(f"Sync complete. Sleeping {interval} seconds.", flush=True)
        time.sleep(interval)

if __name__ == "__main__":
    main_loop()