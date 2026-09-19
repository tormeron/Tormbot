import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import os
import json
import datetime
import socket
import requests
import time
import queue
import random
import sqlite3
import pygame  # For Audio Alerts
import re      # Added for smarter Auto-Mod word boundaries and link protection
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- YOUTUBE LIBRARY (Graceful Import) ---
try:
    import pytchat
    PYTCHAT_AVAILABLE = True
except ImportError:
    PYTCHAT_AVAILABLE = False

# --- FILES & CONFIG DEFAULTS ---
LOG_FOLDER = "chat_logs"
CONFIG_FILE = "bot_config.json" # Kept for migration detection
POINTS_FILE = "user_points.json" # Kept for migration detection
DB_FILE = "bot_database.db"

DEFAULT_CONFIG = {
    "token": "",
    "client_id": "",     
    "client_secret": "",
    "channel": "",
    "events_enabled": True,
    "automod_enabled": True,
    "link_protection_enabled": False, # Added for Link Protection
    "commands_enabled": True,
    "points_enabled": True,
    "charity_enabled": True,
    "timers_enabled": False,          # Added for Timers
    "timers_interval": 15,            # Added for Timers
    "timers_messages": [],            # Added for Timers
    "uptime_command": "!uptime",      # Added for Engagement
    "ai_enabled": False,
    "ai_type_response": False,   
    "ai_allow_all": False,       
    "ai_cooldown": 10.0,         
    "ai_port_listen": 9998,      
    "ai_port_send": 9999,        
    "ai_command": "!ai",
    "ai_allow_mods": True,       
    "ai_allowed_users": [],      
    "debug_enabled": True,
    "points_per_msg": 5,
    "points_passive_rate": 10,
    "passive_interval": 60,
    "charity_total": 0.0,
    "charity_msg_amt": 0.0,
    "charity_passive_amt": 0.0,
    "charity_passive_interval": 60,
    "charity_log_interval_min": 30,
    "charity_ignored_users": [],
    "auto_shoutouts": {},
    "user_notes": {},
    "auto_mod_rules": [
        {"word": "buy followers", "action": "ban", "sound": "", "match_type": "spread"}
    ],
    "custom_commands": {
        "!discord": {
            "response": "Join our discord here: https://discord.gg/example", 
            "sound": "",
            "permission_everyone": True,
            "permission_mods": True,
            "allowed_users": [],
            "cooldown": 2.0,
            "aliases": ["!dc"]
        }
    },
    "events": {
        "bits": {"response": "Thanks for the {amount} bits, {user}!", "sound": ""},
        "sub": {"response": "Welcome to the crew, {user}!", "sound": ""},
        "points": {"response": "{user} redeemed {reward}!", "sound": ""},
        "follow": {"response": "Thanks for the follow, {user}!", "sound": ""}
    }
}

# --- SQLITE UTILS & MIGRATION ---
def get_db_conn():
    """Creates a database connection configured for concurrent threading and maximum crash durability."""
    conn = sqlite3.connect(DB_FILE, timeout=15.0) # Wait up to 15 seconds if locked by another thread
    conn.execute("PRAGMA journal_mode=WAL;")      # Write-Ahead Logging allows concurrent readers/writers
    conn.execute("PRAGMA synchronous=FULL;")      # Forces full disk sync to prevent corruption on crash
    return conn

def init_db(debug_callback=None):
    conn = get_db_conn()
    try:
        # Flush any leftover WAL cache into the main DB from a previous ungraceful shutdown
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        
        c = conn.cursor()
        # Create tables
        c.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS points (username TEXT PRIMARY KEY, points INTEGER)''')
        conn.commit()
        
        # Migration from JSON to SQLite
        c.execute("SELECT 1 FROM config LIMIT 1")
        if not c.fetchone():
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, 'r') as f:
                        data = json.load(f)
                    for k, v in data.items():
                        c.execute("INSERT INTO config (key, value) VALUES (?, ?)", (k, json.dumps(v)))
                    conn.commit()
                    os.rename(CONFIG_FILE, CONFIG_FILE + ".bak")
                    if debug_callback: debug_callback("Migrated bot_config.json to SQLite database.")
                except Exception as e:
                    if debug_callback: debug_callback(f"Failed to migrate config: {e}")
                    
        c.execute("SELECT 1 FROM points LIMIT 1")
        if not c.fetchone():
            if os.path.exists(POINTS_FILE):
                try:
                    with open(POINTS_FILE, 'r') as f:
                        data = json.load(f)
                    for k, v in data.items():
                        c.execute("INSERT INTO points (username, points) VALUES (?, ?)", (k, v))
                    conn.commit()
                    os.rename(POINTS_FILE, POINTS_FILE + ".bak")
                    if debug_callback: debug_callback("Migrated user_points.json to SQLite database.")
                except Exception as e:
                    if debug_callback: debug_callback(f"Failed to migrate points: {e}")
    finally:
        conn.close()

def db_load_config(default_cfg, debug_callback=None):
    init_db(debug_callback)
    conn = get_db_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT key, value FROM config")
        rows = c.fetchall()
    finally:
        conn.close()
    
    if not rows:
        db_save_config(default_cfg, debug_callback)
        return default_cfg.copy()
        
    data = {}
    for row in rows:
        try:
            data[row[0]] = json.loads(row[1])
        except Exception as e:
            if debug_callback: debug_callback(f"Garbage data discarded during DB config load for key {row[0]}: {e}")
            
    modified = False
    for key in default_cfg:
        if key not in data:
            data[key] = default_cfg[key]
            modified = True
            
    if "events" in data:
        for ev in default_cfg["events"]:
            if ev not in data["events"]:
                data["events"][ev] = default_cfg["events"][ev]
                modified = True

    if modified:
        db_save_config(data, debug_callback)
    return data

def db_save_config(data, debug_callback=None):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        for k, v in data.items():
            # Identify Garbage Data: Serialization test
            try:
                json_str = json.dumps(v)
                json.loads(json_str) # Verification pass to ensure it is parseable
                c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, json_str))
            except (TypeError, ValueError) as e:
                if debug_callback: debug_callback(f"Garbage data blocked for config key '{k}'. Write aborted. Error: {e}")
        conn.commit()
    except Exception as e:
        if debug_callback: debug_callback(f"DB Save Config Error: {e}")
    finally:
        if 'conn' in locals(): conn.close()

def db_load_points(debug_callback=None):
    init_db(debug_callback)
    conn = get_db_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT username, points FROM points")
        rows = c.fetchall()
    finally:
        conn.close()
    
    data = {}
    for row in rows:
        data[row[0]] = row[1]
    return data

def db_save_points(data, debug_callback=None):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        for k, v in data.items():
            # Identify Garbage Data: Type checking
            if not isinstance(v, int) or v < 0:
                if debug_callback: debug_callback(f"Garbage data blocked for points user '{k}': Invalid value {v}. Must be a positive integer.")
                continue
            c.execute("INSERT OR REPLACE INTO points (username, points) VALUES (?, ?)", (k, v))
        conn.commit()
    except Exception as e:
        if debug_callback: debug_callback(f"DB Save Points Error: {e}")
    finally:
        if 'conn' in locals(): conn.close()

def play_alert(sound_path, debug_callback=None):
    if sound_path and os.path.exists(sound_path):
        def _play():
            try:
                pygame.mixer.init()
                pygame.mixer.music.load(sound_path)
                pygame.mixer.music.play()
            except Exception as e: 
                if debug_callback:
                    debug_callback(f"Audio Alert Failed: Could not load or play sound file '{sound_path}'. Verify it is a valid format. Error: {e}")
        threading.Thread(target=_play, daemon=True).start()
    elif sound_path:
        if debug_callback:
            debug_callback(f"Audio Alert Failed: The file '{sound_path}' was not found. Has it been moved or deleted?")

# --- AI LISTENER SERVER ---
class AIResponseHandler(BaseHTTPRequestHandler):
    bot_instance = None

    def log_message(self, format, *args):
        pass # Suppress standard HTTP server logging spam

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8'))
            reply = data.get('response', data.get('text', data.get('message', '')))
            
            if reply and AIResponseHandler.bot_instance:
                if AIResponseHandler.bot_instance.cfg.get('ai_type_response', False):
                    formatted_reply = f"AI: {str(reply).strip()}"
                    AIResponseHandler.bot_instance.send_message(formatted_reply)
                    
                AIResponseHandler.bot_instance.gui_callback("SYSTEM", f"✓ Received Async from AI: {str(reply).strip()}", "system")
                
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            if AIResponseHandler.bot_instance and AIResponseHandler.bot_instance.debug_callback:
                AIResponseHandler.bot_instance.debug_callback(f"AI Listener Error handling POST request: {e}")

# --- HYBRID BACKEND ---
class HybridBot:
    def __init__(self, config, gui_callback, points_callback, event_callback, charity_callback, note_alert_callback, debug_callback):
        self.debug_callback = debug_callback
        self.cfg = config
        self.gui_callback = gui_callback
        self.points_callback = points_callback
        self.event_callback = event_callback
        self.charity_callback = charity_callback
        self.note_alert_callback = note_alert_callback
        self.points = db_load_points(self.debug_callback)
        self.running = False
        self.socket = None
        self.username = None
        self.user_id = None
        self.channel_id = None
        self.msg_queue = queue.Queue()
        self.ai_server = None
        
        self.live_viewers = set() 
        self.last_ai_time = 0.0 
        self.last_command_time = 0.0
        self.last_send_time = time.time()  
        self.cmd_cooldowns = {} # Stores individual command timestamps
        
        self.poll_active = False
        self.poll_question = ""
        self.poll_options = {}
        self.poll_descs = {}
        self.poll_voted = set()
        self.poll_callback = None

        self.raffle_active = False
        self.raffle_cmd = "!join"
        self.raffle_desc = ""
        self.raffle_entries = set()
        self.raffle_update_cb = None

        self.shouted_out_users = set()
        self.notified_users = set()

        self.charity_total = float(self.cfg.get('charity_total', 0.0))
        self.session_msg_charity = 0.0
        self.session_passive_charity = 0.0
        self.session_manual_charity = 0.0
        self.msg_delay = 1.6 # Default sending delay
        self.perm_confirmed = False # Tracks if permissions have been reported once
        self._export_charity_file()

    def _export_charity_file(self):
        try:
            with open("charity_total.txt", "w") as f:
                f.write(f"{self.charity_total:.2f}")
        except Exception as e:
            if self.debug_callback:
                self.debug_callback(f"File Write Error: Failed to save 'charity_total.txt'. Error: {e}")
                
        self.cfg['charity_total'] = self.charity_total
        db_save_config(self.cfg, self.debug_callback)
        self.charity_callback(self.charity_total)

    def stop(self):
        self.running = False
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
                self.socket.close()
            except: pass
        if self.ai_server:
            threading.Thread(target=self.ai_server.shutdown, daemon=True).start()

    def start(self):
        self.running = True
        token = self.cfg['token'].replace("oauth:", "")
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            val = requests.get("https://id.twitch.tv/oauth2/validate", headers=headers).json()
            if 'login' not in val:
                raise ValueError("Invalid OAuth Token. Check your settings.")
            
            self.username = val.get('login')
            self.user_id = val.get('user_id')
            actual_client_id = val.get('client_id', self.cfg.get('client_id', ''))
            self.cfg['client_id'] = actual_client_id
            
            chan_name = self.cfg['channel'].lower().replace("#", "")
            if not chan_name:
                raise ValueError("Channel name is empty in your settings!")
            
            u_resp = requests.get(f"https://api.twitch.tv/helix/users?login={chan_name}", 
                                 headers={**headers, "Client-Id": actual_client_id}).json()
            
            if 'data' not in u_resp or not u_resp['data']:
                raise ValueError("Twitch API Error: Could not find that channel.")
                
            self.channel_id = u_resp['data'][0]['id']
            
            # If the bot is the broadcaster, it automatically has mod privileges
            if self.user_id == self.channel_id:
                self.msg_delay = 0.35
                self.debug_callback("Bot is broadcaster. Setting message delay to 0.35s.", is_verbose=True)
            else:
                self.msg_delay = 1.6
            
        except Exception as e:
            self.gui_callback("SYSTEM", f"Login Error: {e}", "system")
            self.debug_callback(f"Bot Login Failed: {e}. Check if your OAuth token is valid.")
            self.running = False
            return

        if self.cfg.get('ai_enabled', False):
            threading.Thread(target=self.start_ai_listener, daemon=True).start()
            
        threading.Thread(target=self.passive_points_task, daemon=True).start()
        threading.Thread(target=self.passive_charity_task, daemon=True).start()
        threading.Thread(target=self.check_followers_task, daemon=True).start()
        threading.Thread(target=self.message_sender_task, daemon=True).start()
        threading.Thread(target=self.sync_chatters_task, daemon=True).start()  
        threading.Thread(target=self.keep_alive_task, daemon=True).start()
        threading.Thread(target=self.timers_task, daemon=True).start()

        retry_count = 0
        
        while self.running:
            self.socket = socket.socket()
            try:
                self.socket.connect(('irc.chat.twitch.tv', 6667))
                self.socket.send(f"PASS oauth:{token}\r\n".encode('utf-8'))
                self.socket.send(f"NICK {self.username}\r\n".encode('utf-8'))
                self.socket.send(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
                self.socket.send(f"JOIN #{chan_name}\r\n".encode('utf-8'))
                self.last_send_time = time.time()  
                self.debug_callback(f"Sent JOIN command for #{chan_name}", is_verbose=True)
                
                self.gui_callback("SYSTEM", f"Bot Online: {self.username} in #{chan_name}", "system")
                self.perm_confirmed = False # Reset confirmation for new session
                
                # If we definitely know status (streamer), report it now.
                # Otherwise, check_mod_status will handle it accurately.
                if self.user_id == self.channel_id:
                    self.gui_callback("SYSTEM", "Permissions: MODERATOR (Broadcaster)", "system")
                    self.perm_confirmed = True

                retry_count = 0 
                
                self.listen()
            except Exception as e:
                if self.running:
                    self.gui_callback("SYSTEM", f"Connection Error: {e}", "system")
                    self.debug_callback(f"Connection Error during IRC connect/listen: {e}")

            if self.running:
                wait_time = 60 + (retry_count * 5)
                self.gui_callback("SYSTEM", f"Disconnected. Retrying in {wait_time} seconds...", "suspicious")
                
                sleep_elapsed = 0
                while sleep_elapsed < wait_time and self.running:
                    time.sleep(1)
                    sleep_elapsed += 1
                    
                retry_count += 1

    def timers_task(self):
        timer_idx = 0
        while self.running:
            interval_min = float(self.cfg.get('timers_interval', 15))
            time.sleep(interval_min * 60)
            if not self.cfg.get('timers_enabled', False):
                continue
            msgs = self.cfg.get('timers_messages', [])
            if msgs and self.socket:
                self.send_message(msgs[timer_idx % len(msgs)])
                timer_idx += 1

    def _fetch_uptime(self):
        try:
            token = self.cfg['token'].replace("oauth:", "")
            headers = {"Authorization": f"Bearer {token}", "Client-Id": self.cfg.get('client_id', '')}
            resp = requests.get(f"https://api.twitch.tv/helix/streams?user_id={self.channel_id}", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                if data:
                    start_time_str = data[0]['started_at']
                    start_time = datetime.datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                    now = datetime.datetime.now(datetime.timezone.utc)
                    diff = now - start_time
                    hours, remainder = divmod(diff.seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m {seconds}s"
                    self.send_message(f"The stream has been live for {time_str}.")
                else:
                    self.send_message("The stream is currently offline.")
            else:
                self.debug_callback(f"Failed to fetch uptime. HTTP {resp.status_code}")
        except Exception as e:
            self.debug_callback(f"Error fetching uptime: {e}")

    def keep_alive_task(self):
        while self.running:
            time.sleep(5)
            if self.socket and (time.time() - self.last_send_time > 120):
                try:
                    self.socket.send(b"PING :tmi.twitch.tv\r\n")
                    self.last_send_time = time.time()
                    self.debug_callback("SEND: PING :tmi.twitch.tv", is_verbose=True)
                except Exception as e:
                    self.debug_callback(f"Keep-Alive ping failed: {e}", is_verbose=True)

    def start_ai_listener(self):
        listen_port = int(self.cfg.get('ai_port_listen', 9998))
        AIResponseHandler.bot_instance = self
        try:
            self.ai_server = HTTPServer(('127.0.0.1', listen_port), AIResponseHandler)
            self.gui_callback("SYSTEM", f"AI Listener active on port {listen_port}", "system")
            self.ai_server.serve_forever()
        except Exception as e:
            self.gui_callback("SYSTEM", f"Failed to start AI Listener: {e}", "suspicious")
            self.debug_callback(f"Failed to bind AI Listener port {listen_port}. Is the port in use? Details: {e}")

    def listen(self):
        while self.running:
            try:
                data = self.socket.recv(32768).decode('utf-8', errors='ignore')
                if not data: 
                    self.debug_callback("IRC Connection closed by server.", is_verbose=True)
                    break 
                    
                for line in data.split("\r\n"):
                    if not line: continue
                    self.debug_callback(f"RECV: {line}", is_verbose=True)
                    
                    if "PING" in line: 
                        self.socket.send(b"PONG :tmi.twitch.tv\r\n")
                        self.last_send_time = time.time()
                    elif "PRIVMSG" in line: 
                        self.handle_raw_msg(line)
                    elif "USERSTATE" in line or "GLOBALUSERSTATE" in line:
                        self.check_mod_status(line)
            except Exception as e: 
                self.debug_callback(f"IRC socket error while listening: {e}")
                break

    def check_mod_status(self, line):
        if line.startswith("@"):
            parts = line[1:].split(" ", 1)
            if len(parts) > 1:
                tag_part = parts[0]
                tags = dict(item.split("=") for item in tag_part.split(";") if "=" in item)
                
                badges = tags.get('badges', '')
                is_mod = tags.get('mod') == '1' or 'broadcaster' in badges or 'moderator' in badges
                
                new_delay = 0.35 if is_mod else 1.6
                
                # Report and update status if confirmed for the first time or if permissions changed
                if not getattr(self, 'perm_confirmed', False) or self.msg_delay != new_delay:
                    self.msg_delay = new_delay
                    self.perm_confirmed = True
                    status_text = "MODERATOR" if is_mod else "REGULAR USER"
                    self.debug_callback(f"Bot recognized as {status_text}. Message delay adjusted to {new_delay}s.", is_verbose=True)
                    self.gui_callback("SYSTEM", f"Permissions: {status_text}", "system")

    def handle_raw_msg(self, line):
        tags = {}
        if line.startswith("@"):
            parts = line[1:].split(" ", 1)
            if len(parts) > 1:
                tag_part, main_msg = parts
                tags = dict(item.split("=") for item in tag_part.split(";") if "=" in item)
                line = main_msg

        parts = line.split(":", 2)
        if len(parts) < 3: return
        
        user = line.split("!")[0].replace(":", "").lower()
        content = parts[2]
        
        self.live_viewers.add(user)

        if "bits" in tags:
            self.trigger_event("bits", user, amount=tags["bits"])
        elif "msg-id" in tags and tags["msg-id"] in ["sub", "resub"]:
            self.trigger_event("sub", user)
        elif "custom-reward-id" in tags:
            self.trigger_event("points", user, reward="Reward")

        self.process_features(user, content, tags)

    def trigger_event(self, etype, user, **kwargs):
        if not self.cfg.get('events_enabled', True): return
        ev_cfg = self.cfg['events'].get(etype, {})
        resp = ev_cfg.get("response", "").format(user=user, **kwargs)
        if resp: self.send_message(resp)
        play_alert(ev_cfg.get("sound"), self.debug_callback)
        self.event_callback(f"[{etype.upper()}] {user}: {resp}")

    def process_features(self, author, content, tags=None):
        if tags is None: tags = {}
        content_lower = content.lower().strip()
        status = "normal"
        
        if self.username and author.lower() == self.username.lower():
            self.gui_callback(author, content, status)
            return

        author_lower = author.lower()
        badges = tags.get('badges', '')
        is_mod = (tags.get('mod') == '1' or 'broadcaster' in badges or 'moderator' in badges)
        is_broadcaster = 'broadcaster' in badges

        # Streamer Note Alert Logic
        notes_data = self.cfg.get('user_notes', {}).get(author_lower, {})
        if notes_data.get('remind', False) and author_lower not in self.notified_users:
            self.notified_users.add(author_lower)
            if self.note_alert_callback:
                self.note_alert_callback(author, notes_data.get('notes', []))

        # Auto-Shoutouts
        auto_so_data = self.cfg.get('auto_shoutouts', {})
        if isinstance(auto_so_data, list):
            new_so_data = {u.lower(): "Go check out {user} at https://twitch.tv/{user} and give them a follow!" for u in auto_so_data}
            self.cfg['auto_shoutouts'] = new_so_data
            auto_so_data = new_so_data
            db_save_config(self.cfg, self.debug_callback)
            
        if author_lower in auto_so_data and author_lower not in self.shouted_out_users:
            self.shouted_out_users.add(author_lower)
            so_msg = auto_so_data[author_lower].replace("{user}", author)
            self.send_message(so_msg)
            self.gui_callback("SYSTEM", f"Auto-Shoutout triggered for {author}", "system")

        # Poll Logic
        if getattr(self, 'poll_active', False):
            if author not in self.poll_voted:
                if content_lower in self.poll_options:
                    self.poll_options[content_lower] += 1
                    self.poll_voted.add(author)
                    if getattr(self, 'poll_callback', None):
                        self.poll_callback(self._get_poll_results())

        # Raffle Logic (Checking Entries)
        if getattr(self, 'raffle_active', False):
            if content_lower == self.raffle_cmd.lower():
                if author_lower not in self.raffle_entries:
                    self.raffle_entries.add(author_lower)
                    if self.raffle_update_cb:
                        self.raffle_update_cb(author_lower, len(self.raffle_entries))

        # Charity Messages
        if self.cfg.get('charity_enabled', True):
            ignored_users = [u.lower() for u in self.cfg.get('charity_ignored_users', [])]
            if author.lower() not in ignored_users:
                msg_amt = float(self.cfg.get('charity_msg_amt', 0.0))
                if msg_amt > 0:
                    self.charity_total += msg_amt
                    self.session_msg_charity += msg_amt
                    self._export_charity_file()

        # Link Protection
        if self.cfg.get('link_protection_enabled', False) and not is_mod:
            url_pattern = r"(?i)\b(?:https?://|www\.)\S+\b"
            if re.search(url_pattern, content):
                self.run_manual_action("timeout", author, duration=10)
                self.send_message(f"@{author} links are not allowed here.")
                self.gui_callback(author, content, "suspicious")
                return # Block command processing and standard automod for this link msg

        # Auto-Mod
        if self.cfg.get('automod_enabled', True):
            for rule in self.cfg['auto_mod_rules']:
                m_type = rule.get('match_type', 'standard')
                raw_word = rule['word']
                
                try:
                    if m_type == 'regex':
                        pattern = raw_word
                    elif m_type == 'spread':
                        chars = [re.escape(c) for c in raw_word.lower() if c.isalnum()]
                        if chars:
                            inner = r'[\W_]*'.join(chars)
                            pattern = rf"\b{inner}\b"
                        else:
                            pattern = rf"\b{re.escape(raw_word.lower())}\b"
                    else: # standard
                        pattern = rf"\b{re.escape(raw_word.lower())}\b"
                        
                    if re.search(pattern, content_lower):
                        self.run_manual_action(rule['action'], author)
                        play_alert(rule.get("sound"), self.debug_callback)
                        status = "suspicious"
                        break
                except re.error as e:
                    self.debug_callback(f"Invalid Regex in Auto-Mod rule '{raw_word}': {e}")
                    continue

        # Commands
        if self.cfg.get('commands_enabled', True):
            cmd_parts = content.split()
            if cmd_parts:
                cmd = cmd_parts[0].lower()
                target = cmd_parts[1] if len(cmd_parts) > 1 else author

                # Engagement: Uptime Built-in
                if cmd == self.cfg.get('uptime_command', '!uptime').lower():
                    current_time = time.time()
                    if current_time - getattr(self, 'last_uptime_time', 0.0) >= 10.0:
                        self.last_uptime_time = current_time
                        threading.Thread(target=self._fetch_uptime, daemon=True).start()

                # Custom Commands
                else:
                    c_name, c_data = None, None
                    for k, v in self.cfg['custom_commands'].items():
                        aliases = [a.strip().lower() for a in v.get('aliases', [])] if isinstance(v, dict) else []
                        if cmd == k or cmd in aliases:
                            c_name = k
                            c_data = v
                            break
                            
                    if c_name and c_data is not None:
                        # Backwards compatibility and structured permissions check
                        perm_everyone = c_data.get('permission_everyone', not c_data.get('mods_only', False)) if isinstance(c_data, dict) else True
                        perm_mods = c_data.get('permission_mods', True) if isinstance(c_data, dict) else True
                        allowed_users = [u.lower() for u in c_data.get('allowed_users', [])] if isinstance(c_data, dict) else []

                        has_permission = False
                        if perm_everyone:
                            has_permission = True
                        elif perm_mods and is_mod:
                            has_permission = True
                        elif author_lower in allowed_users:
                            has_permission = True
                        elif is_broadcaster:
                            has_permission = True # Failsafe
                            
                        if has_permission:
                            current_time = time.time()
                            cooldown = float(c_data.get('cooldown', 2.0)) if isinstance(c_data, dict) else 2.0
                            
                            if current_time - self.cmd_cooldowns.get(c_name, 0.0) >= cooldown:
                                self.cmd_cooldowns[c_name] = current_time
                                
                                resp_data = c_data['response'] if isinstance(c_data, dict) else c_data
                                if isinstance(resp_data, list) and resp_data:
                                    resp_text = random.choice(resp_data)
                                else:
                                    resp_text = resp_data
                                    
                                user_points = str(self.points.get(author, 0))
                                rnd_val = str(random.randint(1, 100))
                                
                                # Process Variables
                                resp_text = resp_text.replace("{user}", author).replace("{points}", user_points)
                                resp_text = resp_text.replace("{target}", target).replace("{random}", rnd_val)
                                
                                self.send_message(resp_text)
                                if isinstance(c_data, dict): play_alert(c_data.get('sound'), self.debug_callback)

        # AI Forwarding logic
        if self.cfg.get('ai_enabled', False):
            ai_cmd = self.cfg.get('ai_command', '!ai').lower()
            if content_lower.startswith(ai_cmd + " ") or content_lower == ai_cmd:
                has_permission = False
                
                if self.cfg.get('ai_allow_all', False):
                    has_permission = True
                else:
                    if self.cfg.get('ai_allow_mods', True):
                        if is_mod:
                            has_permission = True
                    
                    if not has_permission:
                        allowed_users = [u.lower() for u in self.cfg.get('ai_allowed_users', [])]
                        if author.lower() in allowed_users:
                            has_permission = True
                            
                if has_permission:
                    current_time = time.time()
                    cooldown = float(self.cfg.get('ai_cooldown', 10.0))
                    
                    if current_time - self.last_ai_time >= cooldown:
                        prompt = content[len(ai_cmd):].strip()
                        if prompt:
                            self.last_ai_time = current_time
                            threading.Thread(target=self.send_to_ai, args=(author, prompt), daemon=True).start()
                    else:
                        remaining = int(cooldown - (current_time - self.last_ai_time))
                        self.gui_callback("SYSTEM", f"Blocked AI Request from {author} (Cooldown: {remaining}s left)", "suspicious")
                else:
                    self.gui_callback("SYSTEM", f"Blocked AI Request from {author} (Missing Permissions)", "suspicious")

        # Points
        if self.cfg.get('points_enabled', True):
            if author not in self.points: self.points[author] = 0
            self.points[author] += int(self.cfg.get('points_per_msg', 5))
            db_save_points(self.points, self.debug_callback)
            self.points_callback(self.points)
            
        self.gui_callback(author, content, status)

    def start_poll(self, question, options_dict):
        self.poll_active = True
        self.poll_question = question
        self.poll_options = {k.lower(): 0 for k in options_dict.keys()}
        self.poll_descs = {k.lower(): (options_dict[k], k) for k in options_dict.keys()}
        self.poll_voted = set()
        
        opts_list = []
        for t, d in options_dict.items():
            if d:
                opts_list.append(f"{t} ({d})")
            else:
                opts_list.append(f"{t}")
        opts_str = " | ".join(opts_list)
        
        self.send_message(f"📊 POLL STARTED: {question} (Type exactly one of: {opts_str})")
        if self.poll_callback:
            self.poll_callback(self._get_poll_results())

    def _get_poll_results(self):
        res = {}
        for k_lower, count in self.poll_options.items():
            desc, orig_t = self.poll_descs[k_lower]
            res[orig_t] = {"desc": desc, "count": count}
        return res

    def end_poll(self):
        if not getattr(self, 'poll_active', False): return None
        self.poll_active = False
        
        results = self._get_poll_results()
        res_list = []
        for t, data in results.items():
            name = data['desc'] if data['desc'] else t
            res_list.append(f"[{name}]:[{data['count']}]")
            
        res_str = " ".join(res_list)
        self.send_message(f"the results of [{self.poll_question}] are: {res_str}")
        return self.poll_question, results

    # --- RAFFLE CONTROL ---
    def start_raffle(self, command, duration_sec, description=""):
        self.raffle_cmd = command.lower()
        self.raffle_desc = description
        self.raffle_entries = set()
        self.raffle_active = True
        
        desc_str = f" for {description}" if description else ""
        self.send_message(f"🎉 A raffle has started{desc_str}! Type {self.raffle_cmd} to join. 🎉")

    def end_raffle(self):
        self.raffle_active = False
        self.send_message("🛑 The raffle has ended! No more entries accepted.")

    def draw_raffle_winner(self):
        if not self.raffle_entries:
            self.send_message("No one entered the raffle :(")
            return None
        winner = random.choice(list(self.raffle_entries))
        self.send_message(f"🏆 The winner is @{winner}! Congratulations! 🏆")
        return winner

    def send_to_ai(self, author, text):
        send_port = self.cfg.get('ai_port_send', 9999)
        url = f"http://127.0.0.1:{send_port}"
        formatted_message = f"{author} said to you: {text}"
        
        try:
            response = requests.post(url, json={"text": formatted_message}, timeout=60)
            if response.status_code == 200:
                self.gui_callback("SYSTEM", f"✓ Passed to AI ({author}): {text}", "system")
                
                if self.cfg.get('ai_type_response', False):
                    try:
                        res_data = response.json()
                        ai_reply = res_data.get('response', res_data.get('text', res_data.get('message', '')))
                        if ai_reply and str(ai_reply).strip():
                            formatted_reply = f"AI: {str(ai_reply).strip()}"
                            self.send_message(formatted_reply)
                            self.gui_callback("SYSTEM", f"✓ Received Sync from AI: {str(ai_reply).strip()}", "system")
                    except:
                        pass
            else:
                self.debug_callback(f"AI API Error: Forwarding text to AI received HTTP {response.status_code}")
                self.gui_callback("SYSTEM", f"⚠ AI Server Error Code: {response.status_code}", "suspicious")
        except requests.exceptions.RequestException as e:
            self.debug_callback(f"AI Forwarding Failed: Connection refused or timed out. Is the main AI app running? Details: {e}")
            self.gui_callback("SYSTEM", f"❌ AI Connection Refused/Timeout. Is the Main App running?", "suspicious")

    def run_manual_action(self, action, target_user, duration=600):
        token = self.cfg['token'].replace("oauth:", "")
        headers = {"Authorization": f"Bearer {token}", "Client-Id": self.cfg.get('client_id', ''), "Content-Type": "application/json"}
        try:
            u_req = requests.get(f"https://api.twitch.tv/helix/users?login={target_user}", headers=headers)
            if u_req.status_code != 200:
                self.debug_callback(f"API Error fetching user '{target_user}': HTTP {u_req.status_code}. Is your OAuth token expired?")
                return
            
            target_id = u_req.json()['data'][0]['id']
            endpoint = f"https://api.twitch.tv/helix/moderation/bans?broadcaster_id={self.channel_id}&moderator_id={self.user_id}"
            data = {"data": {"user_id": target_id, "reason": "Bot Action"}}
            if action == "timeout": data["data"]["duration"] = duration
            
            resp = requests.post(endpoint, headers=headers, json=data)
            if resp.status_code not in [200, 204]:
                self.debug_callback(f"API Error performing '{action}' on '{target_user}': HTTP {resp.status_code} - {resp.text}")
                self.gui_callback("SYSTEM", f"Failed API {action} on {target_user}", "system")
            else:
                self.gui_callback("SYSTEM", f"API {action.upper()} on {target_user}", "system")
        except Exception as e:
            self.debug_callback(f"Failed to execute manual API action '{action}' on '{target_user}'. Exception: {e}")
            self.gui_callback("SYSTEM", f"Failed API {action} on {target_user}", "system")

    def send_message(self, text):
        if self.socket: self.msg_queue.put(text)

    def message_sender_task(self):
        while self.running:
            try:
                text = self.msg_queue.get(timeout=0.5)
                chan = self.cfg['channel'].lower().replace("#", "")
                if self.socket:
                    self.socket.send(f"PRIVMSG #{chan} :{text}\r\n".encode('utf-8'))
                    self.last_send_time = time.time() 
                    self.debug_callback(f"SEND: PRIVMSG #{chan} :{text}", is_verbose=True)
                time.sleep(self.msg_delay)
            except queue.Empty: continue
            except Exception as e: 
                self.debug_callback(f"Error in message sender task: {e}")

    def modify_user_points(self, user, amount):
        if user not in self.points: self.points[user] = 0
        self.points[user] += amount
        if self.points[user] < 0: self.points[user] = 0
        db_save_points(self.points, self.debug_callback)
        self.points_callback(self.points)
        
    def modify_charity_manual(self, amount):
        if not self.cfg.get('charity_enabled', True): return
        self.charity_total += amount
        self.session_manual_charity += amount
        self._export_charity_file()
        
    def save_charity_log(self, offline_amount=0.0):
        total_gained = self.session_msg_charity + self.session_passive_charity + self.session_manual_charity + offline_amount
        if total_gained > 0:
            try:
                with open("charity_stream_log.txt", "a") as f:
                    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"[{now}] Total Gained: {total_gained:.2f} | Messages: {self.session_msg_charity:.2f} | Passive View: {self.session_passive_charity:.2f} | Manual: {(self.session_manual_charity + offline_amount):.2f}\n")
            except Exception as e:
                self.debug_callback(f"File Write Error: Failed to save to 'charity_stream_log.txt'. Error: {e}")
                
            self.session_msg_charity = 0.0
            self.session_passive_charity = 0.0
            self.session_manual_charity = 0.0

    def sync_chatters_task(self):
        token = self.cfg['token'].replace("oauth:", "")
        headers = {
            "Authorization": f"Bearer {token}", 
            "Client-Id": self.cfg.get('client_id', '')
        }
        base_url = f"https://api.twitch.tv/helix/chat/chatters?broadcaster_id={self.channel_id}&moderator_id={self.user_id}&first=1000"
        
        while self.running:
            try:
                new_viewers = set()
                cursor = ""
                missing_scope = False
                
                while True:
                    url = base_url
                    if cursor:
                        url += f"&after={cursor}"
                        
                    resp = requests.get(url, headers=headers)
                    
                    if resp.status_code in [401, 403]:
                        self.gui_callback("SYSTEM", "API WARNING: Missing scope 'moderator:read:chatters'. Viewer limits bypassed by API will not work. Please generate a new token with this scope enabled.", "suspicious")
                        self.debug_callback(f"Chatters API Auth Error: HTTP {resp.status_code}. Missing scope 'moderator:read:chatters'.", is_verbose=True)
                        missing_scope = True
                        break
                    elif resp.status_code != 200:
                        self.debug_callback(f"Chatters API Error: HTTP {resp.status_code} - {resp.text}")
                        break
                        
                    data = resp.json()
                    if 'data' in data:
                        for u in data['data']:
                            new_viewers.add(u['user_login'].lower())
                            
                    pagination = data.get('pagination', {})
                    cursor = pagination.get('cursor')
                    
                    if not cursor:
                        break
                        
                    time.sleep(0.2)  
                    
                if missing_scope:
                    break  
                    
                if new_viewers:
                    self.live_viewers = new_viewers
                    
            except Exception as e:
                self.debug_callback(f"Exception in sync_chatters task: {e}")
                
            time.sleep(60)

    def passive_points_task(self):
        while self.running:
            interval = int(self.cfg.get('passive_interval', 60))
            if interval <= 0: interval = 60
            time.sleep(interval)
            
            if not self.cfg.get('points_enabled', True): continue
            
            rate = int(self.cfg.get('points_passive_rate', 10))
            if rate > 0 and self.live_viewers:
                for u in list(self.live_viewers): 
                    if u not in self.points: self.points[u] = 0
                    self.points[u] += rate
                db_save_points(self.points, self.debug_callback)
                self.points_callback(self.points)
            
    def passive_charity_task(self):
        while self.running:
            interval = int(self.cfg.get('charity_passive_interval', 60))
            if interval <= 0: interval = 60
            time.sleep(interval)
            
            if not self.cfg.get('charity_enabled', True): continue
            
            c_amt = float(self.cfg.get('charity_passive_amt', 0.0))
            if c_amt > 0 and self.live_viewers:
                ignored = [u.lower() for u in self.cfg.get('charity_ignored_users', [])]
                valid_count = sum(1 for u in list(self.live_viewers) if u.lower() not in ignored)
                if valid_count > 0:
                    added = c_amt * valid_count
                    self.charity_total += added
                    self.session_passive_charity += added
                    self._export_charity_file()

    def check_followers_task(self):
        token = self.cfg['token'].replace("oauth:", "")
        headers = {"Authorization": f"Bearer {token}", "Client-Id": self.cfg.get('client_id', '')}
        url = f"https://api.twitch.tv/helix/channels/followers?broadcaster_id={self.channel_id}&moderator_id={self.user_id}"
        known_followers = set()
        initialized = False
        while self.running:
            try:
                if not self.cfg.get('events_enabled', True):
                    time.sleep(60)
                    continue
                    
                resp = requests.get(url, headers=headers)
                if resp.status_code != 200:
                    self.debug_callback(f"Followers API Error: HTTP {resp.status_code} - {resp.text}. Note: This usually happens if the token lacks permissions or is expired.")
                    time.sleep(120)
                    continue
                
                resp_json = resp.json()
                if 'data' in resp_json:
                    if not initialized:
                        known_followers = {u['user_name'] for u in resp_json['data']}
                        initialized = True
                    else:
                        for u in resp_json['data']:
                            uname = u['user_name']
                            if uname not in known_followers:
                                known_followers.add(uname)
                                self.trigger_event("follow", uname)
            except Exception as e: 
                self.debug_callback(f"Exception checking followers: {e}")
            time.sleep(120)

# --- GUI ---
class ModernBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Tormbot 3.1")
        self.root.geometry("1100x750")
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        style = ttk.Style()
        style.theme_use('clam')

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)

        self.tab_dash = ttk.Frame(self.tabs); self.tabs.add(self.tab_dash, text="Dashboard")
        self.tab_events = ttk.Frame(self.tabs); self.tabs.add(self.tab_events, text="Events")
        self.tab_automod = ttk.Frame(self.tabs); self.tabs.add(self.tab_automod, text="Auto-Mod")
        self.tab_commands = ttk.Frame(self.tabs); self.tabs.add(self.tab_commands, text="Commands")
        self.tab_eng = ttk.Frame(self.tabs); self.tabs.add(self.tab_eng, text="Engagement") 
        self.tab_raffle = ttk.Frame(self.tabs); self.tabs.add(self.tab_raffle, text="Raffle")
        self.tab_points = ttk.Frame(self.tabs); self.tabs.add(self.tab_points, text="Points")
        self.tab_charity = ttk.Frame(self.tabs); self.tabs.add(self.tab_charity, text="Charity")
        self.tab_poll = ttk.Frame(self.tabs); self.tabs.add(self.tab_poll, text="Poll")
        self.tab_shoutout = ttk.Frame(self.tabs); self.tabs.add(self.tab_shoutout, text="Auto-SO")
        self.tab_notes = ttk.Frame(self.tabs); self.tabs.add(self.tab_notes, text="Notes")
        self.tab_ai = ttk.Frame(self.tabs); self.tabs.add(self.tab_ai, text="AI")
        self.tab_settings = ttk.Frame(self.tabs); self.tabs.add(self.tab_settings, text="Settings")
        self.tab_debug = ttk.Frame(self.tabs); self.tabs.add(self.tab_debug, text="Debug")

        # Initialize Debug var and tab first to catch any early errors during config load
        self.var_debug = tk.BooleanVar(value=True) 
        self._init_debug_tab()

        self.config = db_load_config(DEFAULT_CONFIG, self.log_debug)
        self.var_debug.set(self.config.get('debug_enabled', True))
        
        if isinstance(self.config.get('auto_shoutouts', {}), list):
            legacy_list = self.config['auto_shoutouts']
            self.config['auto_shoutouts'] = {u.lower(): "Go check out {user} at https://twitch.tv/{user} and give them a follow!" for u in legacy_list}
            db_save_config(self.config, self.log_debug)

        self.bot = None
        self.yt_running = False # YT thread tracking
        self.offline_manual_charity = 0.0
        self.log_timer = None

        self.var_events = tk.BooleanVar(value=self.config.get('events_enabled', True))
        self.var_automod = tk.BooleanVar(value=self.config.get('automod_enabled', True))
        self.var_link_prot = tk.BooleanVar(value=self.config.get('link_protection_enabled', False))
        self.var_commands = tk.BooleanVar(value=self.config.get('commands_enabled', True))
        self.var_points = tk.BooleanVar(value=self.config.get('points_enabled', True))
        self.var_charity = tk.BooleanVar(value=self.config.get('charity_enabled', True))
        
        self.var_ai = tk.BooleanVar(value=self.config.get('ai_enabled', False)) 
        self.var_ai_type_response = tk.BooleanVar(value=self.config.get('ai_type_response', False)) 
        self.var_ai_allow_all = tk.BooleanVar(value=self.config.get('ai_allow_all', False))         
        self.var_ai_mods = tk.BooleanVar(value=self.config.get('ai_allow_mods', True)) 

        self._init_dashboard()
        self._init_events_tab()
        self._init_automod()
        self._init_commands()
        self._init_engagement() 
        self._init_raffle()     
        self._init_points()
        self._init_charity()
        self._init_poll()
        self._init_shoutout()
        self._init_notes()
        self._init_ai() 
        self._init_settings()
        
        self._schedule_auto_log_loop()

    def on_closing(self):
        self.perform_charity_log_save()
        self.yt_running = False # Cleanly exit youtube listener
        if self.bot:
            self.bot.stop()
        self.root.destroy()

    def _toggle_module(self, key, var):
        self.config[key] = var.get()
        db_save_config(self.config, self.log_debug)

    def _init_debug_tab(self):
        f = ttk.Frame(self.tab_debug)
        f.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Checkbutton(f, text="Enable Debug System", variable=self.var_debug, 
                        command=lambda: self._toggle_module('debug_enabled', self.var_debug)).pack(anchor="w", pady=(0, 10))

        left_f = ttk.Frame(f)
        left_f.pack(side="left", fill="both", expand=True)

        self.txt_debug = scrolledtext.ScrolledText(left_f, state='disabled', font=("Courier", 9))
        self.txt_debug.pack(fill="both", expand=True)

        right_f = ttk.LabelFrame(f, text="Debug Controls", width=220)
        right_f.pack(side="right", fill="y", padx=(10, 0))

        self.var_verbose = tk.BooleanVar(value=False)
        ttk.Checkbutton(right_f, text="Enable Verbose Logging", variable=self.var_verbose).pack(anchor="w", padx=10, pady=10)
        
        ttk.Label(right_f, text="Shows raw IRC traffic\nlike PING/PONG messages\nand deep diagnostics.", justify="left", foreground="gray").pack(anchor="w", padx=10, pady=(0, 15))

        ttk.Button(right_f, text="Clear Log", command=self.clear_debug_log).pack(fill="x", padx=10, pady=5)
        ttk.Button(right_f, text="Save Log to File", command=self.save_debug_log).pack(fill="x", padx=10, pady=5)

    def clear_debug_log(self):
        self.txt_debug.config(state="normal")
        self.txt_debug.delete("1.0", tk.END)
        self.txt_debug.config(state="disabled")

    def save_debug_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", 
            filetypes=[("Text Files", "*.txt")],
            initialfile=f"debug_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(self.txt_debug.get("1.0", tk.END))
                messagebox.showinfo("Saved", "Debug log successfully saved!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save debug log: {e}")
                self.log_debug(f"Save Log Error: {e}")

    def log_debug(self, msg, is_verbose=False):
        if hasattr(self, 'var_debug') and not self.var_debug.get():
            return
        if is_verbose and not getattr(self, 'var_verbose', None):
            return
        if getattr(self, 'var_verbose', None) and not self.var_verbose.get() and is_verbose:
            return

        def _append():
            if not hasattr(self, 'txt_debug'): return
            self.txt_debug.config(state="normal")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            prefix = "[VERBOSE] " if is_verbose else ""
            self.txt_debug.insert(tk.END, f"[{ts}] {prefix}{msg}\n")

            num_lines = int(float(self.txt_debug.index('end')))
            if num_lines > 200:
                self.txt_debug.delete('1.0', f"{num_lines - 200}.0")

            self.txt_debug.see(tk.END)
            self.txt_debug.config(state="disabled")

        self.root.after(0, _append)

    def _init_dashboard(self):
        top = ttk.Frame(self.tab_dash)
        top.pack(fill="x", padx=10, pady=5)
        
        self.btn_connect = ttk.Button(top, text="START BOT", command=self.start_bot)
        self.btn_connect.pack(side="left")
        
        self.btn_disconnect = ttk.Button(top, text="DISCONNECT BOT", command=self.stop_bot, state="disabled")
        self.btn_disconnect.pack(side="left", padx=5)
        
        ttk.Label(top, text="Right-click names to Mod (Twitch only)").pack(side="right")

        # --- CHAT SPLIT CONTAINER ---
        chat_split = ttk.Frame(self.tab_dash)
        chat_split.pack(fill="both", expand=True, padx=10, pady=5)

        # --- TWITCH SIDE ---
        tw_frame = ttk.LabelFrame(chat_split, text="Twitch Chat")
        tw_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        self.chat_area = scrolledtext.ScrolledText(tw_frame, state='disabled', height=25)
        self.chat_area.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.chat_area.tag_config("suspicious", foreground="red", background="#ffffcc")
        self.chat_area.tag_config("system", foreground="blue", font=("Segoe UI", 10, "bold"))
        self.chat_area.tag_config("clickable", foreground="darkblue", underline=True)

        send_frame = ttk.Frame(tw_frame)
        send_frame.pack(fill="x", padx=5, pady=(0, 5))
        
        self.ent_chat_msg = ttk.Entry(send_frame, state='disabled')
        self.ent_chat_msg.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.ent_chat_msg.bind("<Return>", lambda event: self.send_manual_msg())
        
        self.btn_send_msg = ttk.Button(send_frame, text="Send Chat", command=self.send_manual_msg, state="disabled")
        self.btn_send_msg.pack(side="right")

        # --- YOUTUBE SIDE ---
        yt_frame = ttk.LabelFrame(chat_split, text="YouTube Chat (Viewer Only)")
        yt_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))

        yt_top = ttk.Frame(yt_frame)
        yt_top.pack(fill="x", padx=5, pady=5)
        ttk.Label(yt_top, text="Channel / URL / ID:").pack(side="left")
        self.ent_yt_url = ttk.Entry(yt_top)
        self.ent_yt_url.pack(side="left", fill="x", expand=True, padx=5)
        self.btn_yt_connect = ttk.Button(yt_top, text="Connect", command=self.connect_youtube)
        self.btn_yt_connect.pack(side="left")
        self.btn_yt_disconnect = ttk.Button(yt_top, text="Disconnect", command=self.disconnect_youtube, state="disabled")
        self.btn_yt_disconnect.pack(side="left", padx=(5,0))

        self.yt_chat_area = scrolledtext.ScrolledText(yt_frame, state='disabled', height=25)
        self.yt_chat_area.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        self.yt_chat_area.tag_config("system", foreground="red", font=("Segoe UI", 10, "bold"))

        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Timeout (10m)", command=self.ctx_timeout)
        self.context_menu.add_command(label="Ban", command=self.ctx_ban)
        self.selected_user = None

    # --- YOUTUBE CONNECTION LOGIC ---
    def connect_youtube(self):
        if not PYTCHAT_AVAILABLE:
            messagebox.showerror("Missing Library", "The 'pytchat' library is required for YouTube integration.\n\nPlease open your terminal/command prompt and run:\npip install pytchat")
            return

        url_or_id = self.ent_yt_url.get().strip()
        if not url_or_id:
            messagebox.showwarning("Warning", "Please paste a YouTube Stream URL, Channel Handle (e.g. @Name), or Video ID.")
            return
        
        self.btn_yt_connect.config(state="disabled")
        self.ent_yt_url.config(state="disabled")
        self.btn_yt_disconnect.config(state="normal")
        
        self.yt_running = True
        self.yt_thread = threading.Thread(target=self.youtube_worker, args=(url_or_id,), daemon=True)
        self.yt_thread.start()

    def disconnect_youtube(self):
        self.yt_running = False
        self.btn_yt_connect.config(state="normal")
        self.ent_yt_url.config(state="normal")
        self.btn_yt_disconnect.config(state="disabled")
        self.log_youtube("SYSTEM", "Disconnected from YouTube.", "system")

    def resolve_yt_id(self, input_str):
        # 1. Exact 11 char match (Video ID)
        if re.match(r"^[0-9A-Za-z_-]{11}$", input_str):
            return input_str
            
        # 2. Standard Video URLs
        video_match = re.search(r"(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})(?:[^\w-]|$)", input_str)
        if video_match and "channel/" not in input_str and "@" not in input_str:
            return video_match.group(1)
            
        # 3. Channel URL or Handle Setup
        if "youtube.com" in input_str:
            url = input_str.rstrip("/")
            if not url.endswith("/live"):
                url += "/live"
        else:
            handle = input_str if input_str.startswith("@") else f"@{input_str}"
            url = f"https://www.youtube.com/{handle}/live"
            
        # 4. Scrape the live URL to find the active Stream Video ID
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(url, headers=headers, timeout=10)
            
            # The canonical tag is usually the most reliable way to find the video ID of the live stream
            match = re.search(r'<link rel="canonical" href="https://www.youtube.com/watch\?v=([0-9A-Za-z_-]{11})">', resp.text)
            if match:
                return match.group(1)
                
            # Fallback search
            match2 = re.search(r'"videoId":"([0-9A-Za-z_-]{11})"', resp.text)
            if match2:
                return match2.group(1)
                
            return None
        except Exception:
            return None

    def youtube_worker(self, url_or_id):
        self.log_youtube("SYSTEM", "Resolving YouTube Link/Channel...", "system")
        
        # 1. Resolve exactly ONCE at the start.
        video_id = self.resolve_yt_id(url_or_id)
        if not video_id:
            self.log_youtube("SYSTEM", "Could not find an active live stream for that input. Make sure the channel is currently live.", "suspicious")
            self.root.after(0, self.disconnect_youtube)
            return

        failed_checks = 0

        while self.yt_running:
            try:
                # 2. Always connect to the exact same video_id
                chat = pytchat.create(video_id=video_id, interruptable=False)
                self.log_youtube("SYSTEM", f"Attempting to connect to YouTube (Video ID: {video_id})...", "system")
                
                if not chat.is_alive():
                    failed_checks += 1
                    if failed_checks >= 2:
                        self.log_youtube("SYSTEM", "Stream is offline or has ended. Closing connection after 2 checks.", "suspicious")
                        self.root.after(0, self.disconnect_youtube)
                        break
                        
                    self.log_youtube("SYSTEM", f"Failed to connect. Stream may be offline. Retrying in 5 seconds (Attempt {failed_checks}/2)...", "system")
                    for _ in range(5):
                        if not self.yt_running: return
                        time.sleep(1)
                    continue

                # Connected successfully, reset failed checks
                failed_checks = 0
                self.log_youtube("SYSTEM", "Connected! Reading YouTube chat...", "system")
                
                while self.yt_running and chat.is_alive():
                    try:
                        for c in chat.get().sync_items():
                            if not self.yt_running: break
                            self.log_youtube(c.author.name, c.message)
                    except Exception as e:
                        self.log_youtube("SYSTEM", f"Error reading chat: {e}", "system")
                    time.sleep(1)
                    
                if self.yt_running:
                    self.log_youtube("SYSTEM", "YouTube connection dropped. Checking stream status in 5 seconds...", "system")
                    for _ in range(5):
                        if not self.yt_running: break
                        time.sleep(1)
                        
            except Exception as e:
                self.log_youtube("SYSTEM", f"YouTube Connection Error: {e}", "system")
                failed_checks += 1
                if failed_checks >= 2:
                    self.log_youtube("SYSTEM", "Stream connection failed twice. Closing connection.", "suspicious")
                    self.root.after(0, self.disconnect_youtube)
                    break
                    
                if self.yt_running:
                    self.log_youtube("SYSTEM", f"Attempting to reconnect in 5 seconds (Attempt {failed_checks}/2)...", "system")
                    for _ in range(5):
                        if not self.yt_running: break
                        time.sleep(1)
                else:
                    self.root.after(0, self.disconnect_youtube)

    def log_youtube(self, user, msg, status="normal"):
        def _log():
            self.yt_chat_area.configure(state='normal')
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.yt_chat_area.insert(tk.END, f"[{ts}] ")
            
            if user == "SYSTEM":
                self.yt_chat_area.insert(tk.END, f"{msg}\n", status)
            else:
                self.yt_chat_area.insert(tk.END, f"{user}: {msg}\n")
            
            num_lines = int(float(self.yt_chat_area.index('end')))
            if num_lines > 200:
                self.yt_chat_area.delete('1.0', f"{num_lines - 200}.0")
                
            self.yt_chat_area.see(tk.END)
            self.yt_chat_area.configure(state='disabled')
        self.root.after(0, _log)

    def send_manual_msg(self):
        if self.bot and self.bot.running:
            msg = self.ent_chat_msg.get().strip()
            if msg:
                self.bot.send_message(msg)
                self._log(self.bot.username if self.bot.username else "BOT", msg)
                self.ent_chat_msg.delete(0, tk.END)

    def _init_events_tab(self):
        f = ttk.Frame(self.tab_events)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        ttk.Checkbutton(f, text="Enable Events System", variable=self.var_events, 
                        command=lambda: self._toggle_module('events_enabled', self.var_events)).pack(anchor="w", pady=(0, 10))
        
        self.event_log = scrolledtext.ScrolledText(f, height=10, state='disabled')
        self.event_log.pack(fill="both", expand=True, pady=5)

        ctrl = ttk.LabelFrame(f, text="Event Responses & Sounds")
        ctrl.pack(fill="x", pady=10)

        self.event_vars = {}
        for i, ev in enumerate(["bits", "sub", "points", "follow"]):
            ttk.Label(ctrl, text=f"{ev.upper()}:").grid(row=i, column=0, padx=10, pady=5, sticky="e")
            ent = ttk.Entry(ctrl, width=60)
            ent.insert(0, self.config['events'][ev]['response'])
            ent.grid(row=i, column=1, pady=5)
            ttk.Button(ctrl, text="🔊 Sound", command=lambda e=ev: self.pick_sound("event", e)).grid(row=i, column=2, padx=10)
            self.event_vars[ev] = ent

        ttk.Button(f, text="Save Event Settings", command=self.save_events).pack(pady=5)

    def _init_points(self):
        f = ttk.Frame(self.tab_points)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        ttk.Checkbutton(f, text="Enable Points System", variable=self.var_points, 
                        command=lambda: self._toggle_module('points_enabled', self.var_points)).pack(anchor="w", pady=(0, 5))
        
        cfg_f = ttk.LabelFrame(f, text="Point Configuration Settings")
        cfg_f.pack(fill="x", pady=5)
        
        ttk.Label(cfg_f, text="Points per message:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.ent_pts_msg = ttk.Entry(cfg_f, width=10)
        self.ent_pts_msg.insert(0, str(self.config.get('points_per_msg', 5)))
        self.ent_pts_msg.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(cfg_f, text="Passive points amount:").grid(row=0, column=2, padx=5, pady=5, sticky="e")
        self.ent_pts_pass = ttk.Entry(cfg_f, width=10)
        self.ent_pts_pass.insert(0, str(self.config.get('points_passive_rate', 10)))
        self.ent_pts_pass.grid(row=0, column=3, padx=5, pady=5)
        
        ttk.Label(cfg_f, text="Passive interval (seconds):").grid(row=0, column=4, padx=5, pady=5, sticky="e")
        self.ent_pts_int = ttk.Entry(cfg_f, width=10)
        self.ent_pts_int.insert(0, str(self.config.get('passive_interval', 60)))
        self.ent_pts_int.grid(row=0, column=5, padx=5, pady=5)
        
        ttk.Button(cfg_f, text="Save Point Settings", command=self.save_points_config).grid(row=1, column=0, columnspan=6, pady=5)

        bot_f = ttk.Frame(f)
        bot_f.pack(fill="both", expand=True, pady=5)
        
        left_frame = ttk.Frame(bot_f)
        left_frame.pack(side="left", fill="both", expand=True)
        
        self.tree_pts = ttk.Treeview(left_frame, columns=("User", "Pts"), show="headings", height=10)
        self.tree_pts.heading("User", text="Username")
        self.tree_pts.heading("Pts", text="Points")
        self.tree_pts.pack(fill="both", expand=True)
        self.tree_pts.bind('<<TreeviewSelect>>', self.on_user_select)

        ctrl = ttk.LabelFrame(bot_f, text="Manage User Points")
        ctrl.pack(side="right", fill="y", padx=10)
        
        ttk.Label(ctrl, text="Username:").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_manage_user = ttk.Entry(ctrl, width=25)
        self.ent_manage_user.pack(padx=10, pady=2)
        
        ttk.Label(ctrl, text="Amount:").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_manage_amt = ttk.Entry(ctrl, width=25)
        self.ent_manage_amt.pack(padx=10, pady=2)
        
        btn_f = ttk.Frame(ctrl)
        btn_f.pack(pady=15)
        ttk.Button(btn_f, text="Add (+)", width=10, command=lambda: self.adjust_points(1)).pack(side="left", padx=2)
        ttk.Button(btn_f, text="Sub (-)", width=10, command=lambda: self.adjust_points(-1)).pack(side="left", padx=2)
        
        # Load and render points immediately when UI initializes
        initial_points = db_load_points(self.log_debug)
        self.render_points(initial_points)

    def save_points_config(self):
        try:
            self.config['points_per_msg'] = int(self.ent_pts_msg.get())
            self.config['points_passive_rate'] = int(self.ent_pts_pass.get())
            self.config['passive_interval'] = int(self.ent_pts_int.get())
            db_save_config(self.config, self.log_debug)
            messagebox.showinfo("Saved", "Point settings saved!")
        except ValueError:
            messagebox.showerror("Error", "Please enter valid integers for point settings.")

    def _init_charity(self):
        f = ttk.Frame(self.tab_charity)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        ttk.Checkbutton(f, text="Enable Charity System", variable=self.var_charity, 
                        command=lambda: self._toggle_module('charity_enabled', self.var_charity)).pack(anchor="w", pady=(0, 5))
        
        top_f = ttk.Frame(f)
        top_f.pack(fill="x", pady=5)
        self.lbl_charity_total = ttk.Label(top_f, text=f"Current Total: {self.config.get('charity_total', 0.0):.2f}", font=("Segoe UI", 14, "bold"))
        self.lbl_charity_total.pack(side="left", padx=10)
        
        ttk.Label(top_f, text="Amount:").pack(side="left", padx=5)
        self.ent_charity_manual = ttk.Entry(top_f, width=10)
        self.ent_charity_manual.pack(side="left", padx=5)
        ttk.Button(top_f, text="Add", command=lambda: self.manual_charity_adj(1)).pack(side="left", padx=2)
        ttk.Button(top_f, text="Remove", command=lambda: self.manual_charity_adj(-1)).pack(side="left", padx=2)
        
        mid_f = ttk.LabelFrame(f, text="Charity Automation Settings")
        mid_f.pack(fill="x", pady=10)
        
        ttk.Label(mid_f, text="Amount per message:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.ent_charity_msg = ttk.Entry(mid_f, width=10)
        self.ent_charity_msg.insert(0, str(self.config.get('charity_msg_amt', 0.0)))
        self.ent_charity_msg.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(mid_f, text="Amount per user viewed:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.ent_charity_pass = ttk.Entry(mid_f, width=10)
        self.ent_charity_pass.insert(0, str(self.config.get('charity_passive_amt', 0.0)))
        self.ent_charity_pass.grid(row=1, column=1, padx=5, pady=5)
        
        ttk.Label(mid_f, text="Viewed Interval (sec):").grid(row=1, column=2, padx=5, pady=5, sticky="e")
        self.ent_charity_pass_int = ttk.Entry(mid_f, width=10)
        self.ent_charity_pass_int.insert(0, str(self.config.get('charity_passive_interval', 60)))
        self.ent_charity_pass_int.grid(row=1, column=3, padx=5, pady=5)
        
        ttk.Button(mid_f, text="Save Settings", command=self.save_charity_settings).grid(row=2, column=0, columnspan=4, pady=10)
        
        bot_f = ttk.Frame(f)
        bot_f.pack(fill="both", expand=True, pady=5)
        
        ign_f = ttk.LabelFrame(bot_f, text="Ignored Users")
        ign_f.pack(side="left", fill="both", expand=True, padx=5)
        
        self.lst_ignored = tk.Listbox(ign_f, height=8)
        self.lst_ignored.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        ign_ctrl = ttk.Frame(ign_f)
        ign_ctrl.pack(side="right", fill="y", padx=5, pady=5)
        self.ent_ignore = ttk.Entry(ign_ctrl, width=15)
        self.ent_ignore.pack(pady=2)
        ttk.Button(ign_ctrl, text="Add Ignore", command=self.add_ignored_user).pack(fill="x", pady=2)
        ttk.Button(ign_ctrl, text="Remove Selected", command=self.remove_ignored_user).pack(fill="x", pady=2)
        self.ref_ignored()
        
        log_f = ttk.LabelFrame(bot_f, text="Session Logging (Auto-Saves)")
        log_f.pack(side="right", fill="both", expand=True, padx=5)
        
        ttk.Label(log_f, text="Logs are saved automatically every X minutes\nand when you close the application.", justify="center").pack(pady=10)
        
        log_ctrl_f = ttk.Frame(log_f)
        log_ctrl_f.pack(pady=5)
        ttk.Label(log_ctrl_f, text="Interval (minutes):").pack(side="left", padx=5)
        self.ent_charity_log_int = ttk.Entry(log_ctrl_f, width=10)
        self.ent_charity_log_int.insert(0, str(self.config.get('charity_log_interval_min', 30)))
        self.ent_charity_log_int.pack(side="left", padx=5)
        
        ttk.Button(log_f, text="Save Log Setting", command=self.save_charity_log_setting).pack(pady=10)

    # --- ENGAGEMENT UI (Timers, Uptime) ---
    def _init_engagement(self):
        f = ttk.Frame(self.tab_eng)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        t_f = ttk.LabelFrame(f, text="Scheduled Timers")
        t_f.pack(fill="x", pady=5)
        self.var_timers = tk.BooleanVar(value=self.config.get('timers_enabled', False))
        ttk.Checkbutton(t_f, text="Enable Timers", variable=self.var_timers, 
                        command=lambda: self._toggle_module('timers_enabled', self.var_timers)).pack(anchor="w", padx=10, pady=5)
        
        int_f = ttk.Frame(t_f)
        int_f.pack(fill="x", padx=10, pady=5)
        ttk.Label(int_f, text="Interval (Minutes):").pack(side="left")
        self.ent_timer_int = ttk.Entry(int_f, width=5)
        self.ent_timer_int.insert(0, str(self.config.get('timers_interval', 15)))
        self.ent_timer_int.pack(side="left", padx=5)
        ttk.Button(int_f, text="Save Interval", command=self.save_timer_int).pack(side="left", padx=5)
        
        tm_f = ttk.Frame(t_f)
        tm_f.pack(fill="both", expand=True, padx=10, pady=10)
        self.lst_timers = tk.Listbox(tm_f, height=6)
        self.lst_timers.pack(side="left", fill="both", expand=True)
        
        ctrl_tm = ttk.Frame(tm_f)
        ctrl_tm.pack(side="right", fill="y", padx=5)
        self.ent_timer_msg = ttk.Entry(ctrl_tm, width=30)
        self.ent_timer_msg.pack(pady=2)
        ttk.Button(ctrl_tm, text="Add Message", command=self.add_timer_msg).pack(fill="x", pady=2)
        ttk.Button(ctrl_tm, text="Remove Selected", command=self.rem_timer_msg).pack(fill="x", pady=2)
        self.ref_timers()

        c_f = ttk.LabelFrame(f, text="Built-in Engagement Commands")
        c_f.pack(fill="x", pady=10)
        
        ttk.Label(c_f, text="Uptime Command:").grid(row=0, column=0, padx=10, pady=5, sticky="e")
        self.ent_uptime_cmd = ttk.Entry(c_f)
        self.ent_uptime_cmd.insert(0, self.config.get('uptime_command', '!uptime'))
        self.ent_uptime_cmd.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        
        ttk.Button(c_f, text="Save Built-in Commands", command=self.save_builtin_cmds).grid(row=1, column=0, columnspan=2, pady=10)

    def save_timer_int(self):
        try:
            val = float(self.ent_timer_int.get())
            if val <= 0: raise ValueError
            self.config['timers_interval'] = val
            db_save_config(self.config, self.log_debug)
            messagebox.showinfo("Saved", "Timer interval updated!")
        except ValueError:
            messagebox.showerror("Error", "Interval must be a valid number greater than 0.")

    def add_timer_msg(self):
        msg = self.ent_timer_msg.get().strip()
        if msg:
            if 'timers_messages' not in self.config: self.config['timers_messages'] = []
            self.config['timers_messages'].append(msg)
            db_save_config(self.config, self.log_debug)
            self.ent_timer_msg.delete(0, tk.END)
            self.ref_timers()

    def rem_timer_msg(self):
        idx = self.lst_timers.curselection()
        if idx:
            del self.config['timers_messages'][idx[0]]
            db_save_config(self.config, self.log_debug)
            self.ref_timers()

    def ref_timers(self):
        self.lst_timers.delete(0, tk.END)
        for t in self.config.get('timers_messages', []):
            self.lst_timers.insert(tk.END, t)

    def save_builtin_cmds(self):
        self.config['uptime_command'] = self.ent_uptime_cmd.get().strip().lower()
        db_save_config(self.config, self.log_debug)
        messagebox.showinfo("Saved", "Engagement commands updated!")

    # --- RAFFLE UI ---
    def _init_raffle(self):
        f = ttk.Frame(self.tab_raffle)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        top_f = ttk.LabelFrame(f, text="Raffle Settings")
        top_f.pack(fill="x", pady=5)
        
        ttk.Label(top_f, text="Prize/Topic:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.ent_raffle_desc = ttk.Entry(top_f, width=40)
        self.ent_raffle_desc.grid(row=0, column=1, columnspan=3, padx=5, pady=5, sticky="w")
        
        ttk.Label(top_f, text="Join Command:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.ent_raffle_join = ttk.Entry(top_f, width=15)
        self.ent_raffle_join.insert(0, "!join")
        self.ent_raffle_join.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        
        ttk.Label(top_f, text="Duration (Optional):").grid(row=1, column=2, padx=5, pady=5, sticky="e")
        dur_f = ttk.Frame(top_f)
        dur_f.grid(row=1, column=3, padx=5, pady=5, sticky="w")
        self.ent_raffle_min = ttk.Entry(dur_f, width=5)
        self.ent_raffle_min.pack(side="left")
        ttk.Label(dur_f, text="m ").pack(side="left")
        self.ent_raffle_sec = ttk.Entry(dur_f, width=5)
        self.ent_raffle_sec.pack(side="left")
        ttk.Label(dur_f, text="s").pack(side="left")
        
        mid_f = ttk.Frame(f)
        mid_f.pack(fill="x", pady=10)
        self.btn_start_raffle = ttk.Button(mid_f, text="▶ Start Raffle", command=self.start_raffle_gui)
        self.btn_start_raffle.pack(side="left", fill="x", expand=True, padx=5)
        self.btn_end_raffle = ttk.Button(mid_f, text="🛑 End Manual/Early", command=self.end_raffle_gui, state="disabled")
        self.btn_end_raffle.pack(side="left", fill="x", expand=True, padx=5)
        self.btn_draw_raffle = ttk.Button(mid_f, text="🏆 Draw Winner", command=self.draw_raffle_winner_gui)
        self.btn_draw_raffle.pack(side="left", fill="x", expand=True, padx=5)
        
        bot_f = ttk.LabelFrame(f, text="Live Entries")
        bot_f.pack(fill="both", expand=True, pady=5)
        
        info_f = ttk.Frame(bot_f)
        info_f.pack(fill="x", padx=10, pady=5)
        self.lbl_raffle_count = ttk.Label(info_f, text="Entries: 0", font=("Segoe UI", 12, "bold"))
        self.lbl_raffle_count.pack(side="left")
        self.lbl_raffle_winner = ttk.Label(info_f, text="Winner: None", font=("Segoe UI", 12, "bold"), foreground="green")
        self.lbl_raffle_winner.pack(side="right")
        
        self.lst_raffle_entries = tk.Listbox(bot_f, font=("Courier", 10))
        self.lst_raffle_entries.pack(fill="both", expand=True, padx=10, pady=5)
        self.raffle_timer_id = None

    def start_raffle_gui(self):
        if not self.bot or not self.bot.running:
            messagebox.showwarning("Warning", "Bot must be connected to start a raffle.")
            return

        cmd = self.ent_raffle_join.get().strip().lower()
        if not cmd:
            messagebox.showwarning("Warning", "Please provide a join command.")
            return
            
        m_str = self.ent_raffle_min.get().strip() or "0"
        s_str = self.ent_raffle_sec.get().strip() or "0"

        try:
            m = int(m_str)
            s = int(s_str)
            total_sec = m * 60 + s
        except ValueError:
            messagebox.showerror("Error", "Duration must be valid numbers.")
            return

        self.btn_start_raffle.config(state="disabled")
        self.btn_end_raffle.config(state="normal")
        self.ent_raffle_join.config(state="disabled")
        self.lst_raffle_entries.delete(0, tk.END)
        self.lbl_raffle_count.config(text="Entries: 0")
        self.lbl_raffle_winner.config(text="Winner: None")
        
        self.bot.raffle_update_cb = self.update_raffle_entries_gui
        self.bot.start_raffle(cmd, total_sec, self.ent_raffle_desc.get().strip())
        
        if total_sec > 0:
            self.raffle_timer_id = self.root.after(total_sec * 1000, self.end_raffle_gui)

    def update_raffle_entries_gui(self, new_user, total_count):
        def _update():
            self.lbl_raffle_count.config(text=f"Entries: {total_count}")
            self.lst_raffle_entries.insert(tk.END, new_user)
            self.lst_raffle_entries.see(tk.END)
        self.root.after(0, _update)

    def end_raffle_gui(self):
        if self.raffle_timer_id:
            self.root.after_cancel(self.raffle_timer_id)
            self.raffle_timer_id = None
            
        if self.bot and getattr(self.bot, 'raffle_active', False):
            self.bot.end_raffle()
            
        self.btn_start_raffle.config(state="normal")
        self.btn_end_raffle.config(state="disabled")
        self.ent_raffle_join.config(state="normal")

    def draw_raffle_winner_gui(self):
        if not self.bot or not self.bot.running:
            return
        
        # Optionally end it if it's still running
        if getattr(self.bot, 'raffle_active', False):
            self.end_raffle_gui()
            
        winner = self.bot.draw_raffle_winner()
        if winner:
            self.lbl_raffle_winner.config(text=f"Winner: {winner}")

    # --- AUTO SHOUTOUT UI ---
    def _init_shoutout(self):
        f = ttk.Frame(self.tab_shoutout)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        ttk.Label(f, text="Users in this list will receive an automated custom shoutout on their FIRST message of the session.\nYou can use '{user}' in the message and it will be replaced by their username.").pack(anchor="w", pady=(0, 10))
        
        left_frame = ttk.Frame(f)
        left_frame.pack(side="left", fill="both", expand=True)
        self.lst_so = tk.Listbox(left_frame, height=15)
        self.lst_so.pack(side="left", fill="both", expand=True)
        self.lst_so.bind('<<ListboxSelect>>', self.on_so_select)
        
        ctrl = ttk.LabelFrame(f, text="Manage Auto-Shoutouts")
        ctrl.pack(side="right", fill="y", padx=10)
        
        ttk.Label(ctrl, text="Twitch Username:").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_so_user = ttk.Entry(ctrl, width=35)
        self.ent_so_user.pack(padx=10, pady=2)
        
        ttk.Label(ctrl, text="Custom Message:").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_so_msg = ttk.Entry(ctrl, width=35)
        self.ent_so_msg.insert(0, "Go check out {user} at https://twitch.tv/{user} !")
        self.ent_so_msg.pack(padx=10, pady=2)
        
        ttk.Button(ctrl, text="Add / Update User", command=self.add_so_user).pack(fill="x", padx=10, pady=(15, 2))
        ttk.Button(ctrl, text="Remove Selected", command=self.remove_so_user).pack(fill="x", padx=10, pady=2)
        
        self.ref_so()
        
    def on_so_select(self, event):
        idx = self.lst_so.curselection()
        if idx:
            val = self.lst_so.get(idx[0])
            u = val.split(" -> ")[0].strip()
            
            if isinstance(self.config.get('auto_shoutouts'), dict) and u in self.config['auto_shoutouts']:
                self.ent_so_user.delete(0, tk.END)
                self.ent_so_user.insert(0, u)
                
                self.ent_so_msg.delete(0, tk.END)
                self.ent_so_msg.insert(0, self.config['auto_shoutouts'][u])

    def add_so_user(self):
        u = self.ent_so_user.get().strip().lower()
        msg = self.ent_so_msg.get().strip()
        
        if u and msg:
            if 'auto_shoutouts' not in self.config or not isinstance(self.config['auto_shoutouts'], dict):
                self.config['auto_shoutouts'] = {}
                
            self.config['auto_shoutouts'][u] = msg
            db_save_config(self.config, self.log_debug)
            
            self.ent_so_user.delete(0, tk.END)
            self.ent_so_msg.delete(0, tk.END)
            self.ent_so_msg.insert(0, "Go check out {user} at https://twitch.tv/{user} !")
            self.ref_so()

    def remove_so_user(self):
        idx = self.lst_so.curselection()
        if idx:
            val = self.lst_so.get(idx[0])
            u = val.split(" -> ")[0].strip()
            if isinstance(self.config.get('auto_shoutouts', {}), dict) and u in self.config['auto_shoutouts']:
                del self.config['auto_shoutouts'][u]
                db_save_config(self.config, self.log_debug)
                self.ref_so()

    def ref_so(self):
        self.lst_so.delete(0, tk.END)
        so_data = self.config.get('auto_shoutouts', {})
        if isinstance(so_data, dict):
            for u, msg in so_data.items():
                self.lst_so.insert(tk.END, f"{u} -> {msg}")

    # --- NOTES UI ---
    def _init_notes(self):
        f = ttk.Frame(self.tab_notes)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        left_f = ttk.LabelFrame(f, text="Users with Notes")
        left_f.pack(side="left", fill="y", padx=5)
        
        self.lst_notes_users = tk.Listbox(left_f, width=20, height=15)
        self.lst_notes_users.pack(side="top", fill="both", expand=True, padx=5, pady=5)
        self.lst_notes_users.bind('<<ListboxSelect>>', self.on_notes_user_select)
        
        ttk.Button(left_f, text="Remove User", command=self.remove_notes_user).pack(fill="x", padx=5, pady=5)
        
        right_f = ttk.LabelFrame(f, text="Manage User Notes")
        right_f.pack(side="right", fill="both", expand=True, padx=5)
        
        top_r = ttk.Frame(right_f)
        top_r.pack(fill="x", pady=5, padx=5)
        ttk.Label(top_r, text="Username:").pack(side="left")
        self.ent_notes_user = ttk.Entry(top_r, width=20)
        self.ent_notes_user.pack(side="left", padx=5)
        ttk.Button(top_r, text="Select / Add User", command=self.select_add_notes_user).pack(side="left", padx=5)
        
        self.lbl_current_notes_user = ttk.Label(right_f, text="No user selected", font=("Segoe UI", 10, "bold"))
        self.lbl_current_notes_user.pack(anchor="w", padx=5, pady=5)
        
        self.var_note_remind = tk.BooleanVar(value=False)
        self.chk_note_remind = ttk.Checkbutton(right_f, text="Remind me when they chat (Once per stream)", variable=self.var_note_remind, command=self.save_note_remind, state="disabled")
        self.chk_note_remind.pack(anchor="w", padx=5, pady=2)
        
        self.lst_notes = tk.Listbox(right_f, height=10)
        self.lst_notes.pack(fill="both", expand=True, padx=5, pady=5)
        
        bot_r = ttk.Frame(right_f)
        bot_r.pack(fill="x", pady=5, padx=5)
        self.ent_new_note = ttk.Entry(bot_r)
        self.ent_new_note.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(bot_r, text="Add Note", command=self.add_note).pack(side="left")
        ttk.Button(bot_r, text="Remove Selected Note", command=self.remove_note).pack(side="left", padx=(5, 0))
        
        self.current_notes_user = None
        self.ref_notes_users()

    def select_add_notes_user(self):
        u = self.ent_notes_user.get().strip().lower()
        if not u: return
        
        if 'user_notes' not in self.config:
            self.config['user_notes'] = {}
            
        if u not in self.config['user_notes']:
            self.config['user_notes'][u] = {"notes": [], "remind": False}
            db_save_config(self.config, self.log_debug)
            self.ref_notes_users()
            
        self._set_current_notes_user(u)
        self.ent_notes_user.delete(0, tk.END)

    def on_notes_user_select(self, event):
        idx = self.lst_notes_users.curselection()
        if idx:
            u = self.lst_notes_users.get(idx[0])
            self._set_current_notes_user(u)

    def _set_current_notes_user(self, u):
        self.current_notes_user = u
        self.lbl_current_notes_user.config(text=f"Notes for: {u}")
        self.chk_note_remind.config(state="normal")
        
        data = self.config['user_notes'].get(u, {"notes": [], "remind": False})
        self.var_note_remind.set(data.get('remind', False))
        
        self.lst_notes.delete(0, tk.END)
        for n in data.get('notes', []):
            self.lst_notes.insert(tk.END, n)

    def save_note_remind(self):
        if self.current_notes_user and self.current_notes_user in self.config.get('user_notes', {}):
            self.config['user_notes'][self.current_notes_user]['remind'] = self.var_note_remind.get()
            db_save_config(self.config, self.log_debug)

    def add_note(self):
        if not self.current_notes_user: return
        note = self.ent_new_note.get().strip()
        if note:
            self.config['user_notes'][self.current_notes_user]['notes'].append(note)
            db_save_config(self.config, self.log_debug)
            self.ent_new_note.delete(0, tk.END)
            self._set_current_notes_user(self.current_notes_user)

    def remove_note(self):
        if not self.current_notes_user: return
        idx = self.lst_notes.curselection()
        if idx:
            del self.config['user_notes'][self.current_notes_user]['notes'][idx[0]]
            db_save_config(self.config, self.log_debug)
            self._set_current_notes_user(self.current_notes_user)

    def remove_notes_user(self):
        idx = self.lst_notes_users.curselection()
        if idx:
            u = self.lst_notes_users.get(idx[0])
            del self.config['user_notes'][u]
            db_save_config(self.config, self.log_debug)
            self.ref_notes_users()
            if self.current_notes_user == u:
                self.current_notes_user = None
                self.lbl_current_notes_user.config(text="No user selected")
                self.chk_note_remind.config(state="disabled")
                self.lst_notes.delete(0, tk.END)

    def ref_notes_users(self):
        self.lst_notes_users.delete(0, tk.END)
        for u in self.config.get('user_notes', {}).keys():
            self.lst_notes_users.insert(tk.END, u)

    def show_note_alert(self, user, notes):
        def _popup():
            win = tk.Toplevel(self.root)
            win.title(f"Streamer Note Alert: {user}")
            win.geometry("400x250")
            win.attributes("-topmost", True)  
            
            f = ttk.Frame(win, padding=10)
            f.pack(fill="both", expand=True)
            
            ttk.Label(f, text=f"{user} just chatted!", font=("Segoe UI", 12, "bold")).pack(pady=5)
            ttk.Label(f, text="Your Notes:").pack(anchor="w")
            
            st = scrolledtext.ScrolledText(f, height=8)
            st.pack(fill="both", expand=True, pady=5)
            for i, n in enumerate(notes, 1):
                st.insert(tk.END, f"{i}. {n}\n")
            st.configure(state="disabled")
            
            ttk.Button(f, text="Dismiss", command=win.destroy).pack(pady=5)
        
        self.root.after(0, _popup)

    # --- POLL UI ---
    def _init_poll(self):
        f = ttk.Frame(self.tab_poll)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        q_frame = ttk.Frame(f)
        q_frame.pack(fill="x", pady=5)
        ttk.Label(q_frame, text="Poll Question:").pack(side="left", padx=5)
        self.ent_poll_q = ttk.Entry(q_frame)
        self.ent_poll_q.pack(side="left", fill="x", expand=True, padx=5)
        
        mid_frame = ttk.Frame(f)
        mid_frame.pack(fill="x", pady=10)
        
        opt_frame = ttk.LabelFrame(mid_frame, text="Options (Up to 6)")
        opt_frame.pack(side="left", fill="both", expand=True, padx=5)
        
        ttk.Label(opt_frame, text="Answer (e.g. 1)").grid(row=0, column=1, padx=5, pady=2, sticky="w")
        ttk.Label(opt_frame, text="Description (e.g. Yes)").grid(row=0, column=2, padx=5, pady=2, sticky="w")

        self.poll_opts = []
        self.poll_entries = []
        for i in range(6):
            ttk.Label(opt_frame, text=f"Option {i+1}:").grid(row=i+1, column=0, padx=5, pady=2, sticky="e")
            
            t_var = tk.StringVar()
            d_var = tk.StringVar()
            self.poll_opts.append({"trigger": t_var, "desc": d_var})
            
            t_ent = ttk.Entry(opt_frame, textvariable=t_var, width=15)
            t_ent.grid(row=i+1, column=1, padx=5, pady=2, sticky="ew")
            
            d_ent = ttk.Entry(opt_frame, textvariable=d_var)
            d_ent.grid(row=i+1, column=2, padx=5, pady=2, sticky="ew")
            
            self.poll_entries.extend([t_ent, d_ent])
            
        opt_frame.columnconfigure(2, weight=1)
        
        ctrl_frame = ttk.LabelFrame(mid_frame, text="Settings & Control")
        ctrl_frame.pack(side="right", fill="both", padx=5)
        
        ttk.Label(ctrl_frame, text="Duration (Optional):").grid(row=0, column=0, columnspan=2, pady=(5,0))
        dur_f = ttk.Frame(ctrl_frame)
        dur_f.grid(row=1, column=0, columnspan=2, pady=2)
        
        self.ent_poll_min = ttk.Entry(dur_f, width=5)
        self.ent_poll_min.pack(side="left", padx=2)
        ttk.Label(dur_f, text="min").pack(side="left")
        
        self.ent_poll_sec = ttk.Entry(dur_f, width=5)
        self.ent_poll_sec.pack(side="left", padx=2)
        ttk.Label(dur_f, text="sec").pack(side="left")
        
        self.btn_start_poll = ttk.Button(ctrl_frame, text="Start Poll", command=self.start_poll_gui)
        self.btn_start_poll.grid(row=2, column=0, columnspan=2, pady=10, sticky="ew", padx=10)
        
        self.btn_end_poll = ttk.Button(ctrl_frame, text="End Poll", command=self.end_poll_gui, state="disabled")
        self.btn_end_poll.grid(row=3, column=0, columnspan=2, pady=5, sticky="ew", padx=10)
        
        res_frame = ttk.LabelFrame(f, text="Live Results")
        res_frame.pack(fill="both", expand=True, pady=5)
        
        self.txt_poll_results = scrolledtext.ScrolledText(res_frame, state='disabled', font=("Courier", 10))
        self.txt_poll_results.pack(fill="both", expand=True, padx=5, pady=5)
        self.poll_timer_id = None

    def start_poll_gui(self):
        if not self.bot or not self.bot.running:
            messagebox.showwarning("Warning", "Bot must be connected and running to start a poll.")
            return

        q = self.ent_poll_q.get().strip()
        opts = {}
        for opt in self.poll_opts:
            t = opt["trigger"].get().strip()
            d = opt["desc"].get().strip()
            if t:
                opts[t] = d

        if not q:
            messagebox.showwarning("Warning", "Please enter a poll question.")
            return
        if len(opts) < 2:
            messagebox.showwarning("Warning", "Please provide at least 2 options with 'Answer' triggers for the poll.")
            return

        m_str = self.ent_poll_min.get().strip() or "0"
        s_str = self.ent_poll_sec.get().strip() or "0"

        try:
            m = int(m_str)
            s = int(s_str)
            total_sec = m * 60 + s
        except ValueError as e:
            messagebox.showerror("Error", "Duration minutes and seconds must be valid numbers.")
            self.log_debug(f"Start Poll Error: Duration configuration failed. {e}")
            return

        self.btn_start_poll.config(state="disabled")
        self.btn_end_poll.config(state="normal")
        self.ent_poll_q.config(state="disabled")
        for ent in self.poll_entries:
            ent.config(state="disabled")
        self.ent_poll_min.config(state="disabled")
        self.ent_poll_sec.config(state="disabled")

        self.bot.poll_callback = lambda results: self.root.after(0, self.update_poll_live, results)
        self.bot.start_poll(q, opts)

        if total_sec > 0:
            self.poll_timer_id = self.root.after(total_sec * 1000, self.end_poll_gui)

    def end_poll_gui(self):
        if self.poll_timer_id:
            self.root.after_cancel(self.poll_timer_id)
            self.poll_timer_id = None

        if self.bot and getattr(self.bot, 'poll_active', False):
            self.bot.end_poll()

        self.btn_start_poll.config(state="normal")
        self.btn_end_poll.config(state="disabled")
        self.ent_poll_q.config(state="normal")
        for ent in self.poll_entries:
            ent.config(state="normal")
        self.ent_poll_min.config(state="normal")
        self.ent_poll_sec.config(state="normal")

    def update_poll_live(self, poll_results):
        self.txt_poll_results.configure(state='normal')
        self.txt_poll_results.delete('1.0', tk.END)

        q = self.ent_poll_q.get()
        total_votes = sum(data['count'] for data in poll_results.values())
        max_votes = max((data['count'] for data in poll_results.values()), default=0)

        res = []
        res.append("=" * 60)
        res.append(f" POLL: {q}")
        res.append("=" * 60)
        res.append("")

        for trigger, data in poll_results.items():
            count = data['count']
            desc = data['desc']
            display_name = f"{trigger} - {desc}" if desc else trigger
            
            bar_len = int((count / max_votes) * 30) if max_votes > 0 else 0
            bar = "█" * bar_len + " " * (30 - bar_len)
            
            res.append(f" {display_name[:20]:<20} | {count:>3} votes | [{bar}]")

        res.append("")
        res.append("=" * 60)
        res.append(f" Total Votes: {total_votes}")
        
        self.txt_poll_results.insert('1.0', "\n".join(res))
        self.txt_poll_results.configure(state='disabled')

    def _init_ai(self):
        f = ttk.Frame(self.tab_ai)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        ttk.Checkbutton(f, text="Enable AI Forwarding via API", variable=self.var_ai, 
                        command=lambda: self._toggle_module('ai_enabled', self.var_ai)).pack(anchor="w", pady=(0, 5))
        
        ttk.Checkbutton(f, text="Type AI response in Twitch chat", variable=self.var_ai_type_response, 
                        command=lambda: self._toggle_module('ai_type_response', self.var_ai_type_response)).pack(anchor="w", pady=(0, 5))

        ttk.Checkbutton(f, text="Allow ANYONE in chat to use the AI command", variable=self.var_ai_allow_all, 
                        command=lambda: self._toggle_module('ai_allow_all', self.var_ai_allow_all)).pack(anchor="w", pady=(0, 5))

        ttk.Checkbutton(f, text="Allow Streamer and Moderators to use AI command", variable=self.var_ai_mods, 
                        command=lambda: self._toggle_module('ai_allow_mods', self.var_ai_mods)).pack(anchor="w", pady=(0, 10))
        
        ctrl = ttk.LabelFrame(f, text="AI Command Trigger Setup")
        ctrl.pack(fill="x", pady=5)
        
        ttk.Label(ctrl, text="Command Prefix (e.g., !ai):").pack(anchor="w", padx=10, pady=(5, 2))
        self.ent_ai_cmd = ttk.Entry(ctrl, width=30)
        self.ent_ai_cmd.insert(0, self.config.get('ai_command', '!ai'))
        self.ent_ai_cmd.pack(padx=10, pady=2, anchor="w")
        
        ttk.Label(ctrl, text="Cooldown (seconds):").pack(anchor="w", padx=10, pady=(5, 2))
        self.ent_ai_cooldown = ttk.Entry(ctrl, width=30)
        self.ent_ai_cooldown.insert(0, str(self.config.get('ai_cooldown', 10.0)))
        self.ent_ai_cooldown.pack(padx=10, pady=2, anchor="w")

        ttk.Label(ctrl, text="Sending Port (to AI App):").pack(anchor="w", padx=10, pady=(5, 2))
        self.ent_ai_port_send = ttk.Entry(ctrl, width=30)
        self.ent_ai_port_send.insert(0, str(self.config.get('ai_port_send', 9999)))
        self.ent_ai_port_send.pack(padx=10, pady=2, anchor="w")

        ttk.Label(ctrl, text="Listening Port (from AI App):").pack(anchor="w", padx=10, pady=(5, 2))
        self.ent_ai_port_listen = ttk.Entry(ctrl, width=30)
        self.ent_ai_port_listen.insert(0, str(self.config.get('ai_port_listen', 9998)))
        self.ent_ai_port_listen.pack(padx=10, pady=2, anchor="w")
        
        ttk.Button(ctrl, text="Save AI Settings", command=self.save_ai_settings).pack(padx=10, pady=(5, 10), anchor="w")

        user_f = ttk.LabelFrame(f, text="Specific Allowed Users")
        user_f.pack(fill="both", expand=True, pady=5)
        
        self.lst_ai_users = tk.Listbox(user_f, height=6)
        self.lst_ai_users.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        user_ctrl = ttk.Frame(user_f)
        user_ctrl.pack(side="right", fill="y", padx=5, pady=5)
        self.ent_ai_user = ttk.Entry(user_ctrl, width=15)
        self.ent_ai_user.pack(pady=2)
        ttk.Button(user_ctrl, text="Add User", command=self.add_ai_user).pack(fill="x", pady=2)
        ttk.Button(user_ctrl, text="Remove Selected", command=self.remove_ai_user).pack(fill="x", pady=2)
        self.ref_ai_users()
        
        info_lbl = ttk.LabelFrame(f, text="How it works")
        info_lbl.pack(fill="x", pady=10)
        info_text = (
            "When enabled, approved users typing the command prefix will have their text forwarded to the AI API.\n"
            "Example: If prefix is '!ai' and user types '!ai Hello', the bot sends 'User said to you: Hello' to Port 9999."
        )
        ttk.Label(info_lbl, text=info_text, justify="left").pack(padx=10, pady=5, anchor="w")

    def save_ai_settings(self):
        cmd = self.ent_ai_cmd.get().strip().lower()
        try:
            cooldown = float(self.ent_ai_cooldown.get())
            port_send = int(self.ent_ai_port_send.get())
            port_listen = int(self.ent_ai_port_listen.get())
        except ValueError as e:
            messagebox.showerror("Error", "Cooldown and Ports must be valid numbers.")
            self.log_debug(f"Save AI Settings Error: Validation failed. {e}")
            return

        if cmd:
            self.config['ai_command'] = cmd
            self.config['ai_cooldown'] = cooldown
            self.config['ai_port_send'] = port_send
            self.config['ai_port_listen'] = port_listen
            db_save_config(self.config, self.log_debug)
            messagebox.showinfo("Saved", "AI configuration successfully saved!")
        else:
            messagebox.showerror("Error", "The command prefix cannot be empty.")

    def add_ai_user(self):
        u = self.ent_ai_user.get().strip().lower()
        if u and u not in self.config.get('ai_allowed_users', []):
            if 'ai_allowed_users' not in self.config: self.config['ai_allowed_users'] = []
            self.config['ai_allowed_users'].append(u)
            db_save_config(self.config, self.log_debug)
            self.ent_ai_user.delete(0, tk.END)
            self.ref_ai_users()

    def remove_ai_user(self):
        idx = self.lst_ai_users.curselection()
        if idx:
            user = self.lst_ai_users.get(idx[0])
            self.config['ai_allowed_users'].remove(user)
            db_save_config(self.config, self.log_debug)
            self.ref_ai_users()

    def ref_ai_users(self):
        self.lst_ai_users.delete(0, tk.END)
        for u in self.config.get('ai_allowed_users', []):
            self.lst_ai_users.insert(tk.END, u)

    def _schedule_auto_log_loop(self):
        interval_min = float(self.config.get('charity_log_interval_min', 30))
        if interval_min <= 0: interval_min = 30
        ms_interval = int(interval_min * 60 * 1000)
        
        if self.log_timer:
            self.root.after_cancel(self.log_timer)
            
        self.log_timer = self.root.after(ms_interval, self._auto_log_tick)

    def _auto_log_tick(self):
        self.perform_charity_log_save()
        self._schedule_auto_log_loop()

    def perform_charity_log_save(self):
        total_offline = getattr(self, 'offline_manual_charity', 0.0)
        
        if self.bot and self.bot.running:
            self.bot.save_charity_log(offline_amount=total_offline)
            self.offline_manual_charity = 0.0
        else:
            if total_offline > 0.0:
                try:
                    with open("charity_stream_log.txt", "a") as f:
                        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        f.write(f"[{now}] Total Gained: {total_offline:.2f} | Messages: 0.00 | Passive View: 0.00 | Manual: {total_offline:.2f} (Offline Edit)\n")
                except Exception as e:
                    self.log_debug(f"File Write Error: Failed to save to 'charity_stream_log.txt' offline. Error: {e}")
                self.offline_manual_charity = 0.0

    def save_charity_log_setting(self):
        try:
            val = float(self.ent_charity_log_int.get())
            if val <= 0: raise ValueError
            self.config['charity_log_interval_min'] = val
            db_save_config(self.config, self.log_debug)
            self._schedule_auto_log_loop()
            messagebox.showinfo("Saved", "Auto-save interval updated!")
        except ValueError as e:
            messagebox.showerror("Error", "Please enter a valid number greater than 0.")
            self.log_debug(f"Save Charity Log Setting Error: {e}")

    def manual_charity_adj(self, mult):
        if not self.config.get('charity_enabled', True):
            messagebox.showwarning("Disabled", "Charity system is currently disabled. Toggle it on above to make changes.")
            return
        try:
            amt = float(self.ent_charity_manual.get()) * mult
            if self.bot and self.bot.running:
                self.bot.modify_charity_manual(amt)
            else:
                cur = float(self.config.get('charity_total', 0.0))
                cur += amt
                self.config['charity_total'] = cur
                db_save_config(self.config, self.log_debug)
                try:
                    with open("charity_total.txt", "w") as f:
                        f.write(f"{cur:.2f}")
                except Exception as e:
                    self.log_debug(f"File Write Error: Failed to save 'charity_total.txt'. Error: {e}")
                self.update_charity_display(cur)
                self.offline_manual_charity += amt
            self.ent_charity_manual.delete(0, tk.END)
        except ValueError as e:
            messagebox.showerror("Error", "Enter a valid number.")
            self.log_debug(f"Manual Charity Adjustment Error: {e}")

    def save_charity_settings(self):
        try:
            self.config['charity_msg_amt'] = float(self.ent_charity_msg.get())
            self.config['charity_passive_amt'] = float(self.ent_charity_pass.get())
            self.config['charity_passive_interval'] = int(self.ent_charity_pass_int.get())
            db_save_config(self.config, self.log_debug)
            messagebox.showinfo("Saved", "Charity settings saved!")
        except ValueError as e:
            messagebox.showerror("Error", "Please enter valid numbers.")
            self.log_debug(f"Save Charity Settings Error: {e}")

    def add_ignored_user(self):
        u = self.ent_ignore.get().strip().lower()
        if u and u not in self.config.get('charity_ignored_users', []):
            if 'charity_ignored_users' not in self.config: self.config['charity_ignored_users'] = []
            self.config['charity_ignored_users'].append(u)
            db_save_config(self.config, self.log_debug)
            self.ent_ignore.delete(0, tk.END)
            self.ref_ignored()

    def remove_ignored_user(self):
        idx = self.lst_ignored.curselection()
        if idx:
            user = self.lst_ignored.get(idx[0])
            self.config['charity_ignored_users'].remove(user)
            db_save_config(self.config, self.log_debug)
            self.ref_ignored()

    def ref_ignored(self):
        self.lst_ignored.delete(0, tk.END)
        for u in self.config.get('charity_ignored_users', []):
            self.lst_ignored.insert(tk.END, u)

    def on_user_select(self, event):
        selected = self.tree_pts.focus()
        if selected:
            values = self.tree_pts.item(selected, 'values')
            self.ent_manage_user.delete(0, tk.END)
            self.ent_manage_user.insert(0, values[0])

    def adjust_points(self, mult):
        u = self.ent_manage_user.get().strip().lower()
        try:
            val = self.ent_manage_amt.get()
            amt = int(val) * mult
            if self.bot: self.bot.modify_user_points(u, amt)
            else:
                pts = db_load_points(self.log_debug)
                pts[u] = max(0, pts.get(u, 0) + amt)
                db_save_points(pts, self.log_debug); self.render_points(pts)
        except Exception as e: 
            messagebox.showerror("Error", "Enter a valid number.")
            self.log_debug(f"Adjust Points Error: Manual modification failed. {e}")

    def _log(self, user, msg, status="normal"):
        self.chat_area.configure(state='normal')
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.chat_area.insert(tk.END, f"[{ts}] ")
        
        if user == "SYSTEM":
            self.chat_area.insert(tk.END, f"{msg}\n", status)
        else:
            u_tag = f"u_{user}"
            self.chat_area.insert(tk.END, user, ("clickable", u_tag))
            self.chat_area.insert(tk.END, f": {msg}\n", status)
            self.chat_area.tag_bind(u_tag, "<Button-3>", lambda e, u=user: self.show_context_menu(e, u))
        
        num_lines = int(float(self.chat_area.index('end')))
        if num_lines > 200:
            self.chat_area.delete('1.0', f"{num_lines - 200}.0")
            
        self.chat_area.see(tk.END)
        self.chat_area.configure(state='disabled')

    def show_context_menu(self, event, user):
        self.selected_user = user
        self.context_menu.post(event.x_root, event.y_root)

    def ctx_timeout(self):
        if self.bot and self.selected_user: self.bot.run_manual_action("timeout", self.selected_user)

    def ctx_ban(self):
        if self.bot and self.selected_user: self.bot.run_manual_action("ban", self.selected_user)

    def start_bot(self):
        self.btn_connect.config(state="disabled", text="Running...")
        self.btn_disconnect.config(state="normal")
        self.ent_chat_msg.config(state="normal")
        self.btn_send_msg.config(state="normal")
        
        self.bot = HybridBot(
            self.config, 
            self.update_gui, 
            self.update_points_display, 
            self.update_event_log, 
            self.update_charity_display,
            self.show_note_alert,
            self.log_debug
        )
        threading.Thread(target=self.bot.start, daemon=True).start()

    def stop_bot(self):
        if self.bot:
            self.bot.stop()
        self.btn_connect.config(state="normal", text="START BOT")
        self.btn_disconnect.config(state="disabled")
        self.ent_chat_msg.delete(0, tk.END)
        self.ent_chat_msg.config(state="disabled")
        self.btn_send_msg.config(state="disabled")
        self._log("SYSTEM", "Bot disconnected manually.", "system")

    def update_gui(self, u, m, s):
        self.root.after(0, self._log, u, m, s)

    def update_event_log(self, msg):
        self.root.after(0, lambda: [self.event_log.configure(state='normal'), 
                                    self.event_log.insert(tk.END, f"{msg}\n"), 
                                    self.event_log.see(tk.END), 
                                    self.event_log.configure(state='disabled')])

    def update_points_display(self, p_dict):
        self.root.after(0, self.render_points, p_dict)

    def update_charity_display(self, total):
        self.root.after(0, lambda: self.lbl_charity_total.config(text=f"Current Total: {total:.2f}"))

    def render_points(self, p_dict):
        for i in self.tree_pts.get_children(): self.tree_pts.delete(i)
        for u, s in sorted(p_dict.items(), key=lambda x: x[1], reverse=True):
            self.tree_pts.insert("", tk.END, values=(u, s))

    def pick_sound(self, category, key):
        path = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav")])
        if path:
            if category == "event": 
                self.config['events'][key]['sound'] = path
            elif category == "cmd": 
                if isinstance(self.config['custom_commands'][key], str):
                    self.config['custom_commands'][key] = {
                        "response": self.config['custom_commands'][key], 
                        "sound": "",
                        "permission_everyone": True,
                        "permission_mods": True,
                        "allowed_users": [],
                        "cooldown": 2.0,
                        "aliases": []
                    }
                self.config['custom_commands'][key]['sound'] = path
            elif category == "mod": 
                self.config['auto_mod_rules'][key]['sound'] = path
                
            db_save_config(self.config, self.log_debug)
            messagebox.showinfo("Sound Added", "Sound successfully attached!")

    def _init_settings(self):
        frame = ttk.Frame(self.tab_settings)
        frame.pack(padx=20, pady=20, fill="x")
        
        fields = [
            ("OAuth Token (Bearer/IRC):", "token", True), 
            ("Client ID:", "client_id", False), 
            ("Client Secret:", "client_secret", True),
            ("Channel:", "channel", False)
        ]
        self.setting_ents = {}
        for i, (label, key, secret) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="e", padx=10)
            ent = ttk.Entry(frame, width=50, show="*" if secret else "")
            ent.insert(0, self.config.get(key, ''))
            ent.grid(row=i, column=1, pady=5)
            self.setting_ents[key] = ent
        
        ttk.Button(frame, text="Save Config", command=self.save_config).grid(row=len(fields), column=1, pady=15, sticky="w")
        
        help_f = ttk.LabelFrame(self.tab_settings, text="How to get your API Credentials")
        help_f.pack(padx=20, pady=10, fill="x")
        help_text = (
            "1. OAuth Token: Go to https://twitchapps.com/tmi/ and copy the token (including 'oauth:').\n\n"
            "2. Client ID & Client Secret:\n"
            "   - Go to https://dev.twitch.tv/console and log in.\n"
            "   - Click 'Register Your Application'.\n"
            "   - Give it a name, set OAuth Redirect URL to 'http://localhost', and set Category to 'Chat Bot'.\n"
            "   - Once created, click 'Manage' on your new app to copy the Client ID and generate a Client Secret.\n\n"
            "NOTE: To track chatters automatically, make sure your token includes the 'moderator:read:chatters' scope."
        )
        ttk.Label(help_f, text=help_text, justify="left", wraplength=900).pack(padx=10, pady=10, anchor="w")

    def save_config(self):
        for k, v in self.setting_ents.items(): self.config[k] = v.get()
        db_save_config(self.config, self.log_debug)
        messagebox.showinfo("Saved", "Settings saved!")

    def save_events(self):
        for ev in ["bits", "sub", "points", "follow"]: self.config['events'][ev]['response'] = self.event_vars[ev].get()
        db_save_config(self.config, self.log_debug)
        messagebox.showinfo("Saved", "Events updated.")

    def _init_automod(self):
        f = ttk.Frame(self.tab_automod)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        ttk.Checkbutton(f, text="Enable Auto-Mod", variable=self.var_automod, 
                        command=lambda: self._toggle_module('automod_enabled', self.var_automod)).pack(anchor="w", pady=(0, 5))
        
        ttk.Checkbutton(f, text="Enable Link Protection (Times out non-mods posting URLs)", variable=self.var_link_prot, 
                        command=lambda: self._toggle_module('link_protection_enabled', self.var_link_prot)).pack(anchor="w", pady=(0, 10))
        
        left_frame = ttk.Frame(f)
        left_frame.pack(side="left", fill="both", expand=True)
        self.lst_rules = tk.Listbox(left_frame, height=13)
        self.lst_rules.pack(side="left", fill="both", expand=True)
        self.lst_rules.bind('<<ListboxSelect>>', self.on_rule_select)
        
        ctrl = ttk.LabelFrame(f, text="Manage Rules")
        ctrl.pack(side="right", fill="y", padx=10)
        
        ttk.Label(ctrl, text="Word / Phrase:").pack(anchor="w", padx=10, pady=(10, 2))
        self.ent_rule = ttk.Entry(ctrl, width=25)
        self.ent_rule.pack(padx=10, pady=2)
        
        ttk.Label(ctrl, text="Match Type:").pack(anchor="w", padx=10, pady=(5, 2))
        self.cb_match = ttk.Combobox(ctrl, values=["standard", "spread", "regex"], width=23)
        self.cb_match.set("standard")
        self.cb_match.pack(padx=10, pady=2)
        
        ttk.Label(ctrl, text="Action:").pack(anchor="w", padx=10, pady=(5, 2))
        self.cb_act = ttk.Combobox(ctrl, values=["timeout", "ban"], width=23)
        self.cb_act.set("timeout")
        self.cb_act.pack(padx=10, pady=2)
        
        ttk.Button(ctrl, text="Add New Rule", command=self.add_rule).pack(fill="x", padx=10, pady=(15, 2))
        ttk.Button(ctrl, text="Update Selected", command=self.update_rule).pack(fill="x", padx=10, pady=2)
        ttk.Button(ctrl, text="Remove Rule", command=self.remove_rule).pack(fill="x", padx=10, pady=(10, 2))
        
        self.ref_rules()

    def on_rule_select(self, event):
        idx = self.lst_rules.curselection()
        if idx:
            rule = self.config['auto_mod_rules'][idx[0]]
            self.ent_rule.delete(0, tk.END)
            self.ent_rule.insert(0, rule['word'])
            self.cb_act.set(rule['action'])
            self.cb_match.set(rule.get('match_type', 'standard'))

    def add_rule(self):
        w = self.ent_rule.get().strip()
        if w:
            self.config['auto_mod_rules'].append({
                "word": w, 
                "action": self.cb_act.get(), 
                "sound": "",
                "match_type": self.cb_match.get()
            })
            db_save_config(self.config, self.log_debug)
            self.ent_rule.delete(0, tk.END) 
            self.ref_rules()

    def update_rule(self):
        idx = self.lst_rules.curselection()
        w = self.ent_rule.get().strip()
        if idx and w:
            self.config['auto_mod_rules'][idx[0]]['word'] = w
            self.config['auto_mod_rules'][idx[0]]['action'] = self.cb_act.get()
            self.config['auto_mod_rules'][idx[0]]['match_type'] = self.cb_match.get()
            db_save_config(self.config, self.log_debug)
            self.ref_rules()

    def remove_rule(self):
        idx = self.lst_rules.curselection()
        if idx:
            del self.config['auto_mod_rules'][idx[0]]
            db_save_config(self.config, self.log_debug)
            self.ent_rule.delete(0, tk.END)
            self.ref_rules()

    def ref_rules(self):
        self.lst_rules.delete(0, tk.END)
        for r in self.config['auto_mod_rules']: 
            m_type = r.get('match_type', 'standard')
            self.lst_rules.insert(tk.END, f"[{m_type.upper()}] {r['word']}  ->  {r['action'].upper()}")

    # --- COMMANDS UI ---
    def _init_commands(self):
        f = ttk.Frame(self.tab_commands)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        
        ttk.Checkbutton(f, text="Enable Commands", variable=self.var_commands, 
                        command=lambda: self._toggle_module('commands_enabled', self.var_commands)).pack(anchor="w", pady=(0, 10))
        
        left_frame = ttk.Frame(f)
        left_frame.pack(side="left", fill="both", expand=True)
        self.lst_cmd = tk.Listbox(left_frame, height=15)
        self.lst_cmd.pack(side="left", fill="both", expand=True)
        
        ctrl = ttk.LabelFrame(f, text="Command Actions")
        ctrl.pack(side="right", fill="y", padx=10)
        
        ttk.Button(ctrl, text="➕ Create New Command", command=lambda: self.open_command_builder()).pack(fill="x", padx=10, pady=(15, 5))
        ttk.Button(ctrl, text="✏️ Edit Selected", command=self.edit_selected_cmd).pack(fill="x", padx=10, pady=5)
        ttk.Button(ctrl, text="❌ Remove Selected", command=self.remove_cmd).pack(fill="x", padx=10, pady=(5, 15))
        
        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', padx=10, pady=10)
        ttk.Button(ctrl, text="ℹ️ Variables Help", command=self.show_variables_help).pack(fill="x", padx=10, pady=5)
        
        self.ref_cmd()

    def edit_selected_cmd(self):
        idx = self.lst_cmd.curselection()
        if idx:
            item_text = self.lst_cmd.get(idx[0])
            # Strip prefixes if any
            if item_text.startswith("[RESTRICTED] "): item_text = item_text[13:]
            k = item_text.split("  ->  ")[0].strip()
            self.open_command_builder(edit_key=k)

    def open_command_builder(self, edit_key=None):
        win = tk.Toplevel(self.root)
        win.title("Command Builder" if not edit_key else f"Edit Command: {edit_key}")
        win.geometry("550x500")
        win.transient(self.root)
        win.grab_set()

        # Retrieve existing data if editing
        c_data = self.config['custom_commands'].get(edit_key, {}) if edit_key else {}
        resp_data = c_data.get('response', "") if isinstance(c_data, dict) else c_data
        is_multi = isinstance(resp_data, list)
        
        aliases = c_data.get('aliases', []) if isinstance(c_data, dict) else []
        cooldown = c_data.get('cooldown', 2.0) if isinstance(c_data, dict) else 2.0
        sound = c_data.get('sound', "") if isinstance(c_data, dict) else ""
        
        # Handle backwards compatibility for permissions
        perm_everyone = c_data.get('permission_everyone', not c_data.get('mods_only', False)) if isinstance(c_data, dict) else True
        perm_mods = c_data.get('permission_mods', True) if isinstance(c_data, dict) else True
        allowed_users = c_data.get('allowed_users', []) if isinstance(c_data, dict) else []

        # Variables
        var_type = tk.StringVar(value="multi" if is_multi else "single")
        var_single_resp = tk.StringVar(value=resp_data if not is_multi else "")
        var_trigger = tk.StringVar(value=edit_key if edit_key else "")
        var_cooldown = tk.StringVar(value=str(cooldown))
        var_aliases = tk.StringVar(value=", ".join(aliases))
        var_sound = tk.StringVar(value=sound)
        
        var_perm_everyone = tk.BooleanVar(value=perm_everyone)
        var_perm_mods = tk.BooleanVar(value=perm_mods)

        # Top Control
        top_f = ttk.Frame(win, padding=10)
        top_f.pack(fill="x")
        ttk.Label(top_f, text="Command Trigger (e.g. !quote):").pack(side="left")
        ttk.Entry(top_f, textvariable=var_trigger, width=20).pack(side="left", padx=10)
        ttk.Radiobutton(top_f, text="Single Response", variable=var_type, value="single").pack(side="right")
        ttk.Radiobutton(top_f, text="Multi Response", variable=var_type, value="multi").pack(side="right", padx=10)

        tabs = ttk.Notebook(win)
        tabs.pack(fill="both", expand=True, padx=10, pady=5)

        tab_opt = ttk.Frame(tabs); tabs.add(tab_opt, text="Options")
        tab_single = ttk.Frame(tabs); tabs.add(tab_single, text="Single Response")
        tab_multi = ttk.Frame(tabs); tabs.add(tab_multi, text="Multi Response")
        tab_perm = ttk.Frame(tabs); tabs.add(tab_perm, text="Permissions")

        # Options Tab
        ttk.Label(tab_opt, text="Cooldown (seconds):").pack(anchor="w", padx=10, pady=(15, 2))
        ttk.Entry(tab_opt, textvariable=var_cooldown, width=20).pack(anchor="w", padx=10, pady=2)
        ttk.Label(tab_opt, text="Aliases (comma-separated):").pack(anchor="w", padx=10, pady=(15, 2))
        ttk.Entry(tab_opt, textvariable=var_aliases, width=35).pack(anchor="w", padx=10, pady=2)
        ttk.Label(tab_opt, text="Assigned Sound Alert:").pack(anchor="w", padx=10, pady=(15, 2))
        lbl_sound = ttk.Label(tab_opt, text=sound if sound else "No sound selected", foreground="gray")
        lbl_sound.pack(anchor="w", padx=10, pady=2)
        
        def pick_snd():
            path = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav")])
            if path:
                var_sound.set(path)
                lbl_sound.config(text=path, foreground="black")
                
        ttk.Button(tab_opt, text="Browse Audio", command=pick_snd).pack(anchor="w", padx=10, pady=2)
        
        # Single Response Tab
        ttk.Label(tab_single, text="Message to send when command is triggered:").pack(anchor="w", padx=10, pady=(15, 5))
        ttk.Entry(tab_single, textvariable=var_single_resp, width=50).pack(anchor="w", padx=10)

        # Multi Response Tab
        lst_multi = tk.Listbox(tab_multi, height=10)
        lst_multi.pack(fill="both", expand=True, padx=10, pady=(10, 5))
        if is_multi:
            for r in resp_data: lst_multi.insert(tk.END, r)
            
        multi_ctrl = ttk.Frame(tab_multi)
        multi_ctrl.pack(fill="x", padx=10, pady=(0, 10))
        ent_new_multi = ttk.Entry(multi_ctrl)
        ent_new_multi.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(multi_ctrl, text="Add", command=lambda: [lst_multi.insert(tk.END, ent_new_multi.get()), ent_new_multi.delete(0, tk.END)] if ent_new_multi.get().strip() else None).pack(side="left")
        ttk.Button(multi_ctrl, text="Remove", command=lambda: lst_multi.delete(lst_multi.curselection()[0]) if lst_multi.curselection() else None).pack(side="left", padx=(5,0))

        # Permissions Tab
        ttk.Checkbutton(tab_perm, text="Allow Everyone (Overrides other settings)", variable=var_perm_everyone).pack(anchor="w", padx=10, pady=(15, 5))
        ttk.Checkbutton(tab_perm, text="Allow Moderators", variable=var_perm_mods).pack(anchor="w", padx=10, pady=5)
        
        ttk.Label(tab_perm, text="Specific Allowed Users:").pack(anchor="w", padx=10, pady=(15, 2))
        lst_spec = tk.Listbox(tab_perm, height=5)
        lst_spec.pack(fill="both", expand=True, padx=10, pady=2)
        for u in allowed_users: lst_spec.insert(tk.END, u)
        
        spec_ctrl = ttk.Frame(tab_perm)
        spec_ctrl.pack(fill="x", padx=10, pady=(0, 10))
        ent_new_spec = ttk.Entry(spec_ctrl)
        ent_new_spec.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(spec_ctrl, text="Add", command=lambda: [lst_spec.insert(tk.END, ent_new_spec.get().strip().lower()), ent_new_spec.delete(0, tk.END)] if ent_new_spec.get().strip() else None).pack(side="left")
        ttk.Button(spec_ctrl, text="Remove", command=lambda: lst_spec.delete(lst_spec.curselection()[0]) if lst_spec.curselection() else None).pack(side="left", padx=(5,0))

        # Save Logic
        bot_f = ttk.Frame(win, padding=10)
        bot_f.pack(fill="x")
        
        def save_command():
            t = var_trigger.get().strip().lower()
            if not t:
                messagebox.showwarning("Warning", "Trigger cannot be empty.", parent=win)
                return
                
            if var_type.get() == "single":
                final_resp = var_single_resp.get()
            else:
                final_resp = list(lst_multi.get(0, tk.END))
                if not final_resp:
                    messagebox.showwarning("Warning", "Multi-response needs at least one item.", parent=win)
                    return
                    
            try:
                final_cool = float(var_cooldown.get() or 2.0)
            except:
                final_cool = 2.0
                
            final_aliases = [a.strip().lower() for a in var_aliases.get().split(',') if a.strip()]
            final_spec = list(lst_spec.get(0, tk.END))
            
            if edit_key and edit_key != t:
                del self.config['custom_commands'][edit_key]
                
            self.config['custom_commands'][t] = {
                "response": final_resp,
                "sound": var_sound.get(),
                "permission_everyone": var_perm_everyone.get(),
                "permission_mods": var_perm_mods.get(),
                "allowed_users": final_spec,
                "cooldown": final_cool,
                "aliases": final_aliases
            }
            db_save_config(self.config, self.log_debug)
            self.ref_cmd()
            win.destroy()
            
        ttk.Button(bot_f, text="💾 Save Command", command=save_command).pack(side="right")

    def show_variables_help(self):
        help_win = tk.Toplevel(self.root)
        help_win.title("Command Variables")
        help_win.geometry("500x220")
        help_win.resizable(False, False)
        
        f = ttk.Frame(help_win, padding=15)
        f.pack(fill="both", expand=True)
        
        ttk.Label(f, text="You can use these variables in your command responses:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 10))
        
        ttk.Label(f, text="{user} -> Replaced with the username of the person who typed the command.").pack(anchor="w", pady=4)
        ttk.Label(f, text="{points} -> Replaced with the user's current point balance.").pack(anchor="w", pady=4)
        ttk.Label(f, text="{target} -> The first word typed after the command (e.g. '!hug @bob' outputs 'bob').").pack(anchor="w", pady=4)
        ttk.Label(f, text="{random} -> A random number between 1 and 100.").pack(anchor="w", pady=4)
        
        ttk.Button(f, text="Close", command=help_win.destroy).pack(pady=(15, 0))

    def remove_cmd(self):
        idx = self.lst_cmd.curselection()
        if idx:
            item_text = self.lst_cmd.get(idx[0])
            if item_text.startswith("[RESTRICTED] "): item_text = item_text[13:]
            k = item_text.split("  ->  ")[0].strip()
            
            if k in self.config['custom_commands']:
                del self.config['custom_commands'][k]
                db_save_config(self.config, self.log_debug)
                self.ref_cmd()

    def ref_cmd(self):
        self.lst_cmd.delete(0, tk.END)
        for k, v in self.config['custom_commands'].items():
            resp = v['response'] if isinstance(v, dict) else v
            
            # Backwards compat format check
            perm_everyone = v.get('permission_everyone', not v.get('mods_only', False)) if isinstance(v, dict) else True
            mod_prefix = "[RESTRICTED] " if not perm_everyone else ""
            
            if isinstance(resp, list):
                self.lst_cmd.insert(tk.END, f"{mod_prefix}{k}  ->  [Multi-Response: {len(resp)} items]")
            else:
                self.lst_cmd.insert(tk.END, f"{mod_prefix}{k}  ->  {resp}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ModernBotGUI(root)
    root.mainloop()