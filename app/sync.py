import os
import pandas as pd
import requests
import time
from telethon.sync import TelegramClient

api_id = os.getenv('TELEGRAM_API_ID')
api_hash = os.getenv('TELEGRAM_API_HASH')
channel_username = os.getenv('TELEGRAM_CHANNEL')

jellyfin_url = os.getenv('JELLYFIN_URL')
jellyfin_api_key = os.getenv('JELLYFIN_API_KEY')

interval = int(os.getenv('SCRIPT_INTERVAL', '3600'))

csv_file = 'users.csv'

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
        print(f"Set Jellyfin user '{username}' enabled = {enabled_state}")
    else:
        print(f"Error setting '{username}': {resp.status_code} {resp.text}")

def fetch_telegram_ids():
    client = TelegramClient('session_name', api_id, api_hash)
    client.start()
    participants = client.get_participants(channel_username, aggressive=True)
    telegram_ids = {str(user.id) for user in participants}
    client.disconnect()
    return telegram_ids

def initialize_csv(jellyfin_users):
    df = pd.DataFrame([{'ID':'', 'JellyfinUser': name, 'Enabled': not info['IsDisabled']} 
                       for name, info in jellyfin_users.items()])
    df.to_csv(csv_file, index=False)
    print("Initialized new CSV with Jellyfin users.")
    return df

def main():
    jellyfin_users = get_jellyfin_users()

    # Load or initialize CSV
    if not os.path.exists(csv_file):
        print("CSV doesn't exist. Creating new one.")
        df = initialize_csv(jellyfin_users)
    else:
        df = pd.read_csv(csv_file, dtype={'ID': str, 'JellyfinUser': str, 'Enabled': bool})
        print("Loaded CSV.")

        # Add new jellyfin users to CSV if not present yet
        csv_usernames = set(df['JellyfinUser'])
        new_entries = []
        for name, info in jellyfin_users.items():
            if name not in csv_usernames:
                new_entries.append({'ID':'', 'JellyfinUser':name, 'Enabled': not info['IsDisabled']})
                print(f"New Jellyfin user '{name}' added to CSV.")
        if new_entries:
            df = pd.concat([df, pd.DataFrame(new_entries)], ignore_index=True)
            df.to_csv(csv_file, index=False)
            print("CSV updated with new Jellyfin users.")

    telegram_ids_present = fetch_telegram_ids()

    known_telegram_ids = set()
    updated_csv = False

    for idx, row in df.iterrows():
        jf_user = row['JellyfinUser']
        csv_ids = set(str(row['ID']).strip().split()) if pd.notnull(row['ID']) and row['ID'].strip() else set()
        known_telegram_ids.update(csv_ids)

        if not csv_ids:
            continue  # Skip if no Telegram IDs assigned yet

        user_present_in_channel = bool(csv_ids & telegram_ids_present)
        if row['Enabled'] != user_present_in_channel:
            df.at[idx, 'Enabled'] = user_present_in_channel
            updated_csv = True
            if jf_user in jellyfin_users:
                set_jellyfin_user_enabled(jellyfin_users[jf_user]['Id'], jf_user, user_present_in_channel)

    unknown_tg_ids = telegram_ids_present - known_telegram_ids
    if unknown_tg_ids:
        print('\n🚨 Unrecognized Telegram IDs (add them to CSV manually):')
        print('\n'.join(unknown_tg_ids))

    if updated_csv:
        df.to_csv(csv_file, index=False)
        print("CSV saved with updated Enabled states.")

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