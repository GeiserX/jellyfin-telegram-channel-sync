import os
import sqlite3
import requests
import time
from telethon.sync import TelegramClient

api_id = os.getenv('TELEGRAM_API_ID')
api_hash = os.getenv('TELEGRAM_API_HASH')
channel_username = int(os.getenv('TELEGRAM_CHANNEL'))
threshold_guardrail=int(os.getenv('THRESHOLD_ENTRIES'))

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
        print(f"✅ Jellyfin user '{username}' set enabled={enabled_state}.")
    else:
        print(f"🚨 Error setting '{username}': {resp.status_code} {resp.text}")

def fetch_telegram_users():
    client = TelegramClient(telegram_session_file, api_id, api_hash)
    client.connect()
    if not client.is_user_authorized():
        print("Telegram client is not authorized! Check your session file.")
        client.disconnect()
        exit(1)
    print("Fetching Participants...")
    
    participants = client.get_participants(channel_username, aggressive=True)

    telegram_users = {}
    for user in participants:
        telegram_users[str(user.id)] = {
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or ""
        }
    num_users = len(telegram_users)
    print(f"Processed {num_users} Participants.")

    if num_users < threshold_guardrail:  # your safety threshold (adjust to your group size if needed)
        print(f"🚨 Too few Telegram users fetched ({num_users}); probably an API or connection issue. Skip disabling users this run.")
        return None # return None for detection later

    client.disconnect()
    return telegram_users

def main():
    print("\n*** Starting Main Sync Loop ***\n")
    jellyfin_users = get_jellyfin_users()
    print("Fetched Jellyfin users.\n")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT ID, JellyfinUser, Enabled FROM users")
    rows = cursor.fetchall()
    print(f"Loaded DB successfully. {len(rows)} total users in DB.\n")

    telegram_users_present = fetch_telegram_users()
    telegram_ids_present = set(telegram_users_present.keys())

    known_telegram_ids = set()
    updated_db = False

    for row in rows:
        jf_user = row['JellyfinUser']
        db_ids_raw = row['ID']
        db_ids = set(db_ids_raw.strip().split()) if db_ids_raw else set()

        # 👇 DEBUGGING LOG - explicitly log all DB IDs for each user
        print(f"\n👤 Checking DB user '{jf_user}': DB IDs: {db_ids}")

        if not db_ids:
            print(f"⚠️ User '{jf_user}' has no Telegram IDs in DB; skipping check.")
            continue

        user_present_in_channel = bool(db_ids & telegram_ids_present)

        if bool(row['Enabled']) != user_present_in_channel:
            action_str = 'ENABLED ✅' if user_present_in_channel else 'DISABLED ⛔'
            print(f"➡️ Status change for '{jf_user}': {bool(row['Enabled'])} → {user_present_in_channel} ({action_str})")

            cursor.execute("UPDATE users SET Enabled = ? WHERE JellyfinUser = ?", (int(user_present_in_channel), jf_user))
            conn.commit()
            updated_db = True

            if jf_user in jellyfin_users:
                set_jellyfin_user_enabled(jellyfin_users[jf_user]['Id'], jf_user, user_present_in_channel)
        else:
            print(f"🔸 No change for '{jf_user}' (Enabled={bool(row['Enabled'])}).")

        known_telegram_ids.update(db_ids)

    unknown_tg_ids = telegram_ids_present - known_telegram_ids
    if unknown_tg_ids:
        print('\n🚨 Unrecognized Telegram users (ADD TO DB):')
        for uid in unknown_tg_ids:
            user = telegram_users_present[uid]
            name = f"{user['first_name']} {user['last_name']}".strip() or user['username'] or "NoName"
            username = f"@{user['username']}" if user['username'] else "No Username"
            print(f" - ID: {uid}, Name: {name}, Username: {username}")
    else:
        print("\n✅ No unknown Telegram IDs.\n")

    if not updated_db:
        print("ℹ️ No DB updates this run.\n")

    conn.close()

def main_loop():
    while True:
        try:
            print("\n==== Starting user synchronization... ====")
            main()
        except Exception as e:
            print(f"An error occurred: {e}")
        print(f"==== Sync complete. Sleeping {interval} seconds. ====\n")
        time.sleep(interval)

if __name__ == "__main__":
    main_loop()