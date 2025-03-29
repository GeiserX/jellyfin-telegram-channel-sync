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
    participants = client.get_participants(channel_username)
    telegram_users = {}
    for user in participants:
        telegram_users[str(user.id)] = {
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or ""
        }
    print(f"Processed Participants: fetched {len(telegram_users)} users.")
    client.disconnect()
    return telegram_users

def main():
    print("Fetching Jellyfin users...")
    jellyfin_users = get_jellyfin_users()
    print("Jellyfin users fetched successfully.")

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    cursor.execute('SELECT ID, JellyfinUser, Enabled FROM users')
    rows = cursor.fetchall()
    df = [{'ID': row[0], 'JellyfinUser': row[1], 'Enabled': bool(row[2])} for row in cursor.fetchall()]
    print("Loaded DB successfully.")

    print("Fetching Telegram users...")
    telegram_users_present = fetch_telegram_users()
    telegram_ids_present = set(telegram_users_present.keys())
    print(f"Telegram users fetched successfully ({len(telegram_users_present)} IDs).")

    known_telegram_ids = set()
    updated_db = False

    for row in cursor.execute("SELECT ID, JellyfinUser, Enabled FROM jellyfin_users"):
        user_ids_str, jf_user, enabled = row
        csv_ids = set(str(user_id).strip() for user_id in row[0].split()) if row['ID'] else set()
        known_telegram_ids.update(csv_ids)

        if not csv_ids:
            print(f"⚠️ User '{jf_user}' has no Telegram IDs in DB; skipping check.")
            continue

        user_present_in_channel = bool(csv_ids & telegram_ids_present)
        if row['Enabled'] != user_present_in_channel:
            print(f"➡️ User '{jf_user}' status changed from {row['Enabled']} to {user_present_in_channel}.")
            
            cursor.execute('UPDATE users SET Enabled=? WHERE JellyfinUser=?', (user_present_in_channel, jf_user))
            conn.commit()

            if jf_user in jellyfin_users:
                set_jellyfin_user_enabled(jellyfin_users[jf_user]['Id'], jf_user, user_present_in_channel)
                action_str = 'ENABLED ✅' if user_present_in_channel else 'DISABLED ⛔'
                print(f"✅ User '{jf_user}' has been {action_str} in Jellyfin.")
            else:
                print(f"⚠️ Jellyfin user '{jf_user}' not found. No action taken.")
        else:
            print(f"🔸 No change for user '{jf_user}' (Enabled = {row['Enabled']}).")

    unknown_tg_ids = telegram_ids_present - known_telegram_ids
    if unknown_tg_ids:
        print('\n🚨 Unrecognized Telegram users (add them to DB manually):')
        for uid in unknown_tg_ids:
            user = telegram_users_present[uid]
            display_name = f"{user['first_name']} {user['last_name']}".strip() or user['username'] or "NoName"
            username_field = f"@{user['username']}" if user['username'] else "No Username"
            print(f"🔹 ID: {uid} | Name: {display_name} | Username: {username_field}")
    else:
        print("\n✅ No unknown Telegram IDs present.")

    conn.close()

def main_loop():
    while True:
        try:
            print("Starting user synchronization...")
            main()
        except Exception as e:
            print(f"An error occurred: {e}")
        print(f"Sync complete. Sleeping {interval} seconds.")
        time.sleep(interval)

if __name__ == "__main__":
    main_loop()