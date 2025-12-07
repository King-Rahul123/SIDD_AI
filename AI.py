import pyttsx3
import speech_recognition as sr
import datetime
import wikipedia
import webbrowser
import os
import pywhatkit
import random
import getpass
import requests
import pyautogui
import psutil
import time
import screen_brightness_control as sbc
import subprocess
import ctypes
import win32gui
import win32con
import win32process
import pygetwindow as gw
import queue
from win10toast_click import ToastNotifier
from pywinauto import Application
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
import threading
import json
from pathlib import Path


def log_command(speaker, text):
    """
    speaker: 'YOU' or 'SIDD'
    """
    print(f"[COMMAND][{speaker.upper()}] {text}", flush=True)

toaster = ToastNotifier()

command_queue = queue.Queue()

# ========== PERSISTENT MEMORY ==========
MEMORY_FILE = Path("sidd_memory.json")

# Default memory structure
# Default memory structure
memory = {
    "user_profile": {
        "name": None,       # e.g. "Rahul"
        "nickname": None,   # e.g. "Boss"
    },
    "notes": [],             # free-form facts user teaches
    "learned_responses": [],  # list of {"query": "...", "response": "..."}
    "conversation_context": {
        "last_topic": None,
        "last_action": None,
        "mood": "neutral"
    }
}

def load_memory():
    """Load memory from disk if it exists."""
    global memory
    try:
        if MEMORY_FILE.exists():
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults so older versions still work
            if "user_profile" not in data:
                data["user_profile"] = memory["user_profile"]
            if "notes" not in data:
                data["notes"] = memory["notes"]
            if "learned_responses" not in data:
                data["learned_responses"] = memory["learned_responses"]
            if "conversation_context" not in data:
                data["conversation_context"] = memory["conversation_context"]
            memory = data
        else:
            save_memory()  # create file with default structure
    except Exception as e:
        print("[MEMORY] Error loading memory:", e)

def save_memory():
    """Save memory to disk."""
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[MEMORY] Error saving memory:", e)

def find_learned_response(query):
    """Return saved response for this exact query if it exists."""
    try:
        for item in memory.get("learned_responses", []):
            if item.get("query") == query:
                return item.get("response")
    except Exception as e:
        print("[MEMORY] Error finding learned response:", e)
    return None

def add_learned_response(query, response):
    """Store a new query → response pair in memory."""
    try:
        memory.setdefault("learned_responses", [])
        memory["learned_responses"].append({
            "query": query,
            "response": response
        })
        save_memory()
        print("[MEMORY] Learned:", query, "->", response)
    except Exception as e:
        print("[MEMORY] Error adding learned response:", e)


# Your OpenWeatherMap API key here
WEATHER_API_KEY = "YOUR_OPENWEATHERMAP_API_KEY"

# Initialize voice engine
engine = pyttsx3.init('sapi5')
voices = engine.getProperty('voices')
tts_lock = threading.Lock()             # Make TTS thread-safe
if voices:
    engine.setProperty('voice', voices[1].id)
    engine.setProperty('rate', 190)
else:
    engine.setProperty('voice', voices[0].id)
    engine.setProperty('rate', 190)

# Simple memory of last interaction
last_query = ""

# Notification system
notifications = []

def add_notification(title, msg):
    notifications.append(f"{title}: {msg}")
    toaster.show_toast(title, msg, duration=5)

def fetch_system_notifications():
    try:
        output = subprocess.check_output(
            'powershell -Command "Get-StartApps | Select-Object Name"', 
            shell=True, text=True
        )
        return output.strip()
    except subprocess.CalledProcessError as e:
        print("Error fetching system notifications:", e)
        return ""

def update_notifications():
    system_notes = fetch_system_notifications()
    if system_notes:
        # Split by line and add each as a notification
        for line in system_notes.splitlines():
            line = line.strip()
            if line and line not in notifications:
                add_notification("System Notification", line)

def handle_notifications_query(query):
    """Handle any user query related to notifications/messages"""
    query_lower = query.lower()
    # First, update internal notifications from system
    update_notifications()
    # If user wants to read the latest notification/message
    if any(phrase in query_lower for phrase in ["read recent notification", "read recent message"]):
        if notifications:
            last_note = notifications[-1]
            speak(f"Here is your latest notification: {last_note}")
            print(last_note)
        else:
            speak("No recent notifications found.")
    else:
        # Just asking if there are notifications/messages
        if notifications:
            speak("Yes, you have notifications.")
        else:
            speak("No, you don't have any notifications.")

# Speak function
def speak(text):
    log_command("SIDD", text)
    with tts_lock:
        try:
            engine.say(text)
            engine.runAndWait()
        except RuntimeError as e:
            # Prevent crash if pyttsx3 is in a weird state
            print("TTS RuntimeError:", e)

# Wishing user based on time
def wish_user():
    hour = datetime.datetime.now().hour
    if hour < 12:
        greet = "Good Morning!"
    elif hour < 18:
        greet = "Good Afternoon!"
    else:
        greet = "Good Evening!"

    # Use saved name / nickname if available
    display_name = None
    try:
        profile = memory.get("user_profile", {})
        display_name = profile.get("nickname") or profile.get("name")
    except Exception:
        display_name = None

    if display_name:
        speak(f"Hello {display_name}, {greet} How can I assist you today?")
    else:
        speak(f"Hello Sir, {greet} How can I assist you today?")

def start_background_listener():
    recognizer = sr.Recognizer()
    mic = sr.Microphone(device_index=1)

    def callback(recognizer, audio):
        try:
            query = recognizer.recognize_google(audio, language='en-in')
            log_command("YOU", query)
            command_queue.put(query.lower())
        except sr.UnknownValueError:
            pass  # ignore noise
        except sr.RequestError:
            speak("I think there is a network issue. Please check your connection.")
        except Exception as e:
            print("Recognition error:", e)

    # Start non-blocking listener
    stop_listening = recognizer.listen_in_background(mic, callback, phrase_time_limit=6)
    return stop_listening

current_ui_elements = []
current_active_window = None
scanner_interval = 1.5  # seconds between scans (lower -> more responsive, higher -> lighter CPU)
CONFIRM_BEFORE_DESTRUCTIVE_ACTIONS = True  # toggle safety confirmations

# ================ SIDD Personality ================
AI_NAME = "SIDD"
PERSONALITY_PRESETS = {
    "friendly": {
        "unknown": "That's outside my current knowledge, Sir. Shall I learn it from you?",
        "confirm": "Just to be safe, should I proceed, Sir?",
        "listening": "I'm listening, Sir."
    },
    "professional": {
        "unknown": "I don't have that information yet. Please clarify.",
        "confirm": "Confirmation required to proceed.",
        "listening": "Awaiting your command."
    },
    "witty": {
        "unknown": "My circuits didn't learn that one yet. Want to teach me, Sir?",
        "confirm": "This might be dramatic. Shall I continue?",
        "listening": "Go ahead, I'm all ears."
    }
}

SIDD_MODE = "friendly"
# ---------- Background scanner ----------
def continuous_window_scanner(interval=scanner_interval):
    """Continuously update current_active_window and current_ui_elements."""
    global current_ui_elements, current_active_window
    while True:
        try:
            active_title = get_active_window()
            if not active_title:
                # no active window detected; clear elements and keep looping
                current_ui_elements = []
                current_active_window = None
                time.sleep(interval)
                continue

            # Update only on change or always refresh elements for reliability
            if active_title != current_active_window:
                current_active_window = active_title
                # print(f"[Scanner] Active window changed: {current_active_window}")

            # Attempt to scan UI elements (scan_app_elements from your code)
            try:
                elements = scan_app_elements()
                if elements is None:
                    elements = []
                # Normalize to lowercase for matching convenience
                current_ui_elements = [e for e in elements if e]
            except Exception as e:
                # print("[Scanner] scan_app_elements error:", e)
                current_ui_elements = []

        except Exception as e:
            print("[Scanner] Unexpected error:", e)
        time.sleep(interval)
    
def proactive_checks():
    last_morning_greeted_day = None
    while True:
        try:
            # Battery check
            battery = psutil.sensors_battery()
            if battery and battery.percent is not None:
                if battery.percent < 20 and not battery.power_plugged:
                    speak("Sir, battery is below twenty percent. I recommend connecting the charger.")

            # Simple daily morning greeting around 9 AM
            now = datetime.datetime.now()
            if now.hour == 9:
                today = now.date()
                if last_morning_greeted_day != today:
                    speak("Good morning, Sir. All systems are operational.")
                    last_morning_greeted_day = today

        except Exception as e:
            print("Proactive error:", e)

        time.sleep(300)  # check every 5 minutes

# Taking command from microphone
def take_command():
    recognizer = sr.Recognizer()
    with sr.Microphone(device_index=1) as source:
        print("Listening...")
        recognizer.adjust_for_ambient_noise(source)
        recognizer.dynamic_energy_threshold = False

        audio = recognizer.listen(source, phrase_time_limit=7)

    try:
        print("Recognizing...")
        query = recognizer.recognize_google(audio, language='en-in')
        print(f"You said: {query}")
        return query.lower()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        speak("I think there is a network issue. Please check your connection.")
        return ""
    except Exception as e:
        speak(f"Oops, something went wrong: {e}")
        return ""

# Notification function
def show_notification(title, msg):
    toaster.show_toast(title, msg, duration=5)
    speak(msg)
    add_notification(title, msg)  # Also add to internal notification list

# Location Functions
def get_current_location():
    """ Get approximate location via IP """
    try:
        # Use a free geolocation API
        resp = requests.get("http://ip-api.com/json/")
        data = resp.json()
        lat = data.get("lat")
        lon = data.get("lon")
        city = data.get("city")
        return lat, lon, city
    except Exception as e:
        print(f"Location error: {e}")
        return None, None, None

# Weather Functions
def get_weather(lat, lon):
    """ fetch weather by lat/lon """
    if lat is None or lon is None:
        return None
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        resp = requests.get(url)
        weather_data = resp.json()
        if weather_data.get("cod") != 200:
            return None
        # Extract what you want
        desc = weather_data["weather"][0]["description"]
        temp = weather_data["main"]["temp"]
        feels = weather_data["main"]["feels_like"]
        humidity = weather_data["main"]["humidity"]
        return {
            "description": desc,
            "temperature": temp,
            "feels_like": feels,
            "humidity": humidity
        }
    except Exception as e:
        print(f"Weather error: {e}")
        return None

# Handle weather command
def handle_weather():
    lat, lon, city = get_current_location()
    if lat is None:
        speak("Sorry, I couldn’t get your location.")
        return
    weather = get_weather(lat, lon)
    if weather is None:
        speak("Sorry, I couldn’t fetch the weather right now.")
        return
    # Speak out results
    speak(f"The current weather in {city} is {weather['description']}. "
          f"The temperature is {weather['temperature']}°C, "
          f"feels like {weather['feels_like']}°C. Humidity is {weather['humidity']} percent.")

# Website Control
def open_website(url, name):
    webbrowser.open(url)
    speak(f"Alright, opening {name} for you.")

# Volume Control Functions
def set_system_volume(level):
    """Set system volume 0-100 without showing overlay"""
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
    except Exception as e:
        print("Volume error:", e)

def set_volume(level):
    try:
        level = int(level)
        if 0 <= level <= 100:
            set_system_volume(level)
            speak(f"Volume set to {level} percent.")
        else:
            speak("Please say a number between 0 and 100.")
    except ValueError:
        speak("I couldn't understand the volume level. Please say a number between 0 and 100.")
    except Exception as e:
        speak("Sorry, I couldn't change the volume.")
        print(e)

# Brightness Control Functions
def set_brightness(level):
    try:
        sbc.set_brightness(level)
        speak(f"Brightness set to {level} percent.")
    except Exception as e:
        speak("Sorry, I couldn't change the brightness.")
        print(e)
def increase_brightness(step=10):                               #increase_brightness
    try:
        current = sbc.get_brightness(display=0)[0]
        new_level = min(100, current + step)
        sbc.set_brightness(new_level)
        speak(f"Increased brightness to {new_level} percent.")
    except Exception as e:
        speak("Sorry, I couldn't increase brightness.")
        print(e)
def decrease_brightness(step=10):                               #decrease_brightness
    try:
        current = sbc.get_brightness(display=0)[0]
        new_level = max(0, current - step)
        sbc.set_brightness(new_level)
        speak(f"Decreased brightness to {new_level} percent.")
    except Exception as e:
        speak("Sorry, I couldn't decrease brightness.")
        print(e)

# Music Control Functions
def play_music_from_folder(path, description):
    if os.path.exists(path):
        songs = os.listdir(path)
        if songs:
            song_choice = random.choice(songs)
            os.startfile(os.path.join(path, song_choice))
            speak(f"Playing some music from your {description}")
        else:
            speak(f"I couldn’t find any songs in your {description}")
    else:
        speak(f"Hmm… that folder doesn’t seem to exist.")

# Wikipedia Search Function
def handle_wikipedia(query):
    try:
        speak('Let me check Wikipedia for that...')
        query = query.replace('wikipedia', '')
        summary = wikipedia.summary(query, sentences=2)
        speak("Here’s what I found:")
        print(summary)
        speak(summary)
    except Exception:
        speak("I couldn’t retrieve information from Wikipedia right now.")

def bring_window_to_front(title_keyword):
    """Bring window with matching title to front."""
    windows = gw.getWindowsWithTitle(title_keyword)
    if windows:
        win = windows[0]
        try:
            win.activate()
            return True
        except:
            try:
                # Fallback with win32
                hwnd = win._hWnd
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                return True
            except:
                return False
    return False

def shift_chrome_tab(keyword):
    try:
        app = Application(backend="uia").connect(title_re=".*Chrome.*")
        dlg = app.top_window()

        # Get tab elements
        tabs = dlg.child_window(control_type="Tab").children()
        for tab in tabs:
            if keyword.lower() in tab.window_text().lower():
                tab.select()
                return True
    except Exception as e:
        print("Chrome tab switch error:", e)
    return False

user = getpass.getuser()
STANDARD_FOLDERS = {
    "desktop": fr"C:\Users\{user}\Desktop",
    "downloads": fr"C:\Users\{user}\Downloads",
    "documents": fr"C:\Users\{user}\Documents",
    "music": fr"C:\Users\{user}\Music",
    "pictures": fr"C:\Users\{user}\Pictures",
    "videos": fr"C:\Users\{user}\Videos",
    "this pc": "explorer.exe"
}

# Start Menu locations (user + all users)
START_MENU_PATHS = [
    fr"C:\Users\{user}\AppData\Roaming\Microsoft\Windows\Start Menu\Programs",
    r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs"
]

def find_in_start_menu(app_name):
    app_name = app_name.lower()
    for path in START_MENU_PATHS:
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.lower().endswith(".lnk") and app_name in file.lower():
                    return os.path.join(root, file)
    return None

# Universal Open Function (supports apps and files)
def open_app_or_file(name):
    global last_opened_app
    try:
        key = name.lower().replace("open ", "").replace("from ", "").strip()

        # 1. Check standard folders
        if key in STANDARD_FOLDERS:
            target = STANDARD_FOLDERS[key]
            if os.path.exists(target) or target.endswith(".exe"):
                os.startfile(target)
                speak(f"Opening {key} for you.")
                last_opened_app = key
                return

        # 2. Scan Desktop for folder or shortcut
        desktop_path = STANDARD_FOLDERS["desktop"]
        for item in os.listdir(desktop_path):
            if key in item.lower():
                os.startfile(os.path.join(desktop_path, item))
                speak(f"Opening {item} from Desktop.")
                return

        # 3. Search Start Menu for shortcuts
        shortcut_path = find_in_start_menu(key)
        if shortcut_path:
            os.startfile(shortcut_path)
            speak(f"Opening {name} from Start Menu.")
            return

        # 4. Try Windows Search (Win+S) first
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
                pyautogui.hotkey("win", "s")
                pyautogui.write(name, interval=0)
                pyautogui.press("enter")
                time.sleep(0.6)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 5)
            # Immediately check if Edge launched
            if close_edge():
                speak(f"Opening {name}.")
                search_url = f"https://www.{name.replace(' ', '')}.com"
                webbrowser.open(search_url)
                return
            else:
                speak(f"Opening {name}.")
                return  # Successfully opened via Windows Search
        except Exception:
            speak(f"Windows search failed for {name}.")

    except Exception as e:
        speak(f"Error opening {name}: {e}")

def close_edge(timeout=1.2):
    end_time = time.time() + timeout
    closed = False

    while time.time() < end_time:
        for proc in psutil.process_iter(['name']):
            try:
                if proc.info['name'] and "msedge.exe" in proc.info['name'].lower():
                    proc.kill()
                    closed = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if closed:
            break
        time.sleep(0.05)  # check every 200ms
    return closed
# Universal Close Function (supports apps and files)

def close_app_or_file(name):
    key = name.lower().replace("close ", "").strip()
    closed = False
    for proc in psutil.process_iter(['pid', 'name']):
        proc_name = proc.info['name']
        if proc_name and key in proc_name.lower():
            proc.terminate()
            closed = True
    try:
        def enumHandler(hwnd, lParam):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).lower()
                if key in title:
                    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                    try:
                        p = psutil.Process(pid)
                        p.terminate()
                        nonlocal closed
                        closed = True
                    except Exception:
                        pass
        win32gui.EnumWindows(enumHandler, None)
    except ImportError:
        # fallback: simple taskkill (will close all explorer windows)
        if key in ["desktop", "computer", "this pc"]:
            os.system("taskkill /f /im explorer.exe")
            # restart explorer
            subprocess.Popen("explorer.exe")
            closed = True

    if closed:
        speak(f"Closed {name} successfully.")
    else:
        speak(f"No running process or folder found matching {name}.")

def get_active_window():
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        return title.lower()
    except:
        return None
def scan_app_elements():
    try:
        app = Application(backend="uia").connect(active_only=True)
        dlg = app.top_window()
        controls = [ctrl.window_text() for ctrl in dlg.descendants() if ctrl.window_text()]
        return controls
    except Exception as e:
        print("UI Scan error:", e)
        return []

# Working on any where inside an app
def handle_in_app_action(command, app):
    global current_ui_elements
    print("Active Window:", get_active_window())
    print("Live Elements (top 10):", current_ui_elements[:10])

    active_title = get_active_window()
    print("Active Window:", active_title)

    # Scan available elements (buttons/links/text fields)
    elements = scan_app_elements()
    print("Found Elements:", elements[:10])  # show first 10

    try:
        # --- Browser actions ---
        if "scroll down" in command:
            pyautogui.scroll(-500)
            speak("Scrolled down.")

        elif "scroll up" in command:
            pyautogui.scroll(500)
            speak("Scrolled up.")

        elif "click" in command:
            # find the element mentioned
            for el in elements:
                if el and el.lower() in command:
                    dlg = Application(backend="uia").connect(active_only=True).top_window()
                    dlg[el].click()
                    speak(f"Clicked on {el}")
                    return
            # fallback: mouse click center
            pyautogui.click()
            speak("Clicked at the center.")

        elif "type" in command:
            text = command.replace("type", "").strip()
            pyautogui.typewrite(text)
            speak(f"Typed: {text}")

        elif "search" in command:
            query = command.replace("search", "").strip()
            pyautogui.typewrite(query)
            pyautogui.press("enter")
            speak(f"Searched for {query}")

        else:
            speak("I scanned the window but couldn't match your command yet.")

    except Exception as e:
        speak(f"Error performing action: {e}")

def detect_mood(query: str):
    """Very simple mood detector from keywords."""
    mood_map = {
        "sad": ["sad", "tired", "depressed", "upset", "lonely"],
        "happy": ["happy", "great", "awesome", "excited"],
        "angry": ["angry", "annoyed", "frustrated", "irritated"]
    }

    q = query.lower()
    for mood, words in mood_map.items():
        if any(word in q for word in words):
            memory["conversation_context"]["mood"] = mood
            save_memory()
            return mood

    # if nothing matched
    memory["conversation_context"]["mood"] = "neutral"
    save_memory()
    return "neutral"

def is_negative_reply(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    negatives = ["no", "nope", "don't", "do not", "cancel", "leave", "skip", "stop"]
    return any(word in t for word in negatives)


def is_positive_reply(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    positives = ["yes", "yeah", "yup", "sure", "ok", "okay", "of course", "teach", "learn"]
    return any(word in t for word in positives)

# Main Function
def main():
    global last_query
    load_memory()
    wish_user()

    # Start background listener & scanners
    threading.Thread(target=continuous_window_scanner, daemon=True).start()
    threading.Thread(target=proactive_checks, daemon=True).start()

    # music_karva_path = f"C:\\Users\\{getpass.getuser()}\\Music\\Carva mini"
    # music_desktop_path = f"C:\\Users\\{getpass.getuser()}\\Music\\desktop"

    last_actionable_query = None  # Store last actionable query
    last_opened_app = None  # Store last opened app for potential closing

    try:
        while True:
            query = take_command()
            if not query:
                continue
            
            log_command("YOU", query)

            # --- Mood detection for Jarvis personality ---
            current_mood = detect_mood(query)
            if current_mood == "sad":
                speak("I sense something is bothering you, Sir. I'm here with you.")
            elif current_mood == "angry":
                speak("I understand your frustration, Sir. I'll try to make things smoother.")
                
            if query == "try again" and last_actionable_query:
                query = last_actionable_query
                speak("Trying again.")

            # Only store actionable queries (not greetings, not "try again", etc.)
            def is_actionable(q):
                ignore = [
                    "hi", "hello", "hey", "good morning", "good evening",
                    "how are you", "can you hear me", "what is your name",
                    "try again", "quit", "exit", "goodbye", "stop", "get out"
                ]
                return not any(word in q for word in ignore)

            if is_actionable(query):
                    print(f"[COMMAND][YOU] {query}", flush=True)
                    
            # ==================== Basic Commands ======================
            greetings = ['hi', 'hello', 'hey', 'good morning', 'good evening']
            if any(query.lower().split()[0] == greet.split()[0] for greet in greetings):
                responses = ["Hey there! 😊 How can I help?", "Hello Sir! What can I do for you today?"]
                speak(random.choice(responses))
                last_query = ""

            # elif 'how are you' in query:
            #     responses = [
            #         "I'm fine, How about you?",
            #         "All systems running smoothly. How are you today?"
            #     ]
            #     speak(random.choice(responses))
            #     last_query = ""

            elif 'can you hear me' in query:
                speak("Yes Sir, I hear you clearly!")
                last_query = ""

            # elif 'what is your name' in query:
            #     speak("I am Sidd, your personal assistent!")
            #     last_query = ""

                        # ========== PERSONAL MEMORY / TRAINING ==========
            elif "my name is" in query:
                # Example: "my name is rahul"
                name = query.split("my name is", 1)[1].strip()
                if name:
                    # Capitalize nicely
                    name = " ".join(part.capitalize() for part in name.split())
                    memory["user_profile"]["name"] = name
                    save_memory()
                    speak(f"Nice to meet you, {name}. I will remember your name.")
                else:
                    speak("I didn't catch your name. Please say it again.")
                last_query = ""

            elif "call me" in query:
                # Example: "call me boss"
                nickname = query.split("call me", 1)[1].strip()
                if nickname:
                    nickname = " ".join(part.capitalize() for part in nickname.split())
                    memory["user_profile"]["nickname"] = nickname
                    save_memory()
                    speak(f"Okay, I will call you {nickname} from now on.")
                else:
                    speak("I didn't catch what you want me to call you.")
                last_query = ""

            elif query.startswith("remember that"):
                # Example: "remember that my favorite color is blue"
                fact = query.replace("remember that", "").strip()
                if fact:
                    memory["notes"].append(fact)
                    save_memory()
                    speak("Okay, I will remember that.")
                    print("[MEMORY] New fact:", fact)
                else:
                    speak("Tell me clearly what you want me to remember.")
                last_query = ""

            # elif "what do you remember" in query or "what things do you remember" in query:
            #     notes = memory.get("notes", [])
            #     profile = memory.get("user_profile", {})
            #     pieces = []

            #     if profile.get("name"):
            #         pieces.append(f"Your name is {profile['name']}.")
            #     if profile.get("nickname"):
            #         pieces.append(f"I call you {profile['nickname']}.")
            #     for fact in notes:
            #         pieces.append(f"I remember that {fact}.")

            #     if pieces:
            #         speak("Here are some things I remember about you.")
            #         for p in pieces[:6]:   # don't talk forever if long
            #             speak(p)
            #     else:
            #         speak("Right now, I don't remember anything special. You can teach me by saying 'remember that' followed by your sentence.")
            #     last_query = ""

            elif 'wikipedia' in query:
                handle_wikipedia(query)
                last_query = ""
                if is_actionable(query): last_actionable_query = query

            elif 'weather' in query:
                handle_weather()
                last_query = ""
                if is_actionable(query): last_actionable_query = query

            elif 'open youtube' in query:
                open_website('https://www.youtube.com', 'YouTube')
                last_query = ""
                if is_actionable(query): last_actionable_query = query

            elif 'open google' in query:
                open_website('https://www.google.com', 'Google')
                last_query = ""
                if is_actionable(query): last_actionable_query = query

            elif 'open gmail' in query:
                open_website('https://mail.google.com', 'Gmail')
                last_query = ""
                if is_actionable(query): last_actionable_query = query

            elif 'open stackoverflow' in query:
                open_website('https://stackoverflow.com', 'Stack Overflow')
                last_query = ""
                if is_actionable(query): last_actionable_query = query

            # elif 'play music from karva mini' in query:
            #     play_music_from_folder(music_karva_path, "Karva Mini folder")
            #     last_query = ""
            #     if is_actionable(query): last_actionable_query = query

            # elif 'play music from desktop' in query:
            #     play_music_from_folder(music_desktop_path, "Desktop folder")
            #     last_query = ""
            #     if is_actionable(query): last_actionable_query = query

            elif query.startswith("play "):
                song = query[5:].strip()  # Extract after 'play '

                # Retry until we get a song name
                while not song:
                    speak("I couldn't understand the song name. Please say the song name again in Bengali or English.")
                    recognizer = sr.Recognizer()
                    with sr.Microphone() as source:
                        recognizer.adjust_for_ambient_noise(source, duration=1)
                        audio = recognizer.listen(source, phrase_time_limit=7)
                    try:
                        # Try Bengali first
                        song_retry = recognizer.recognize_google(audio, language='bn-IN')
                        if not song_retry.strip():
                            # fallback to English
                            song_retry = recognizer.recognize_google(audio, language='en-IN')
                        song = song_retry.strip()
                        print(f"You said (song): {song}")
                    except sr.UnknownValueError:
                        speak("Sorry, I still couldn't understand. Please repeat the song name.")
                    except Exception as e:
                        print(e)
                        speak("Something went wrong, let's try again.")

                # Play the song on YouTube
                speak(f"Great! Playing '{song}' on YouTube now.")
                try:
                    pywhatkit.playonyt(song)
                except Exception as e:
                    print(e)
                    speak("Sorry, I couldn't play the song right now.")
                if is_actionable(query): last_actionable_query = query
            
            # Pause/Resume Music (local + YouTube)
            elif "pause song" in query or "pause music" in query:
                try:
                    pyautogui.press("playpause")  # Works for most players
                    # Also try YouTube-specific pause
                    time.sleep(0.5)
                    pyautogui.press("k")  # YouTube pause/play shortcut
                    speak("Paused the song.")
                except Exception as e:
                    speak("Sorry, I couldn't pause the song.")
                    print(e)
                if is_actionable(query): last_actionable_query = query

            elif "resume" in query or "resume song" in query:
                try:
                    pyautogui.press("playpause")  # Resume for local players
                    time.sleep(0.5)
                    pyautogui.press("k")  # Resume YouTube
                    speak("Resumed the song.")
                except Exception as e:
                    speak("Sorry, I couldn't resume the song.")
                    print(e)
                if is_actionable(query): last_actionable_query = query

            elif 'the time' in query:
                str_time = datetime.datetime.now().strftime("%H:%M")
                speak(f"It's currently {str_time}.")
                last_query = ""
                if is_actionable(query): last_actionable_query = query

            # ==================== Open/shift/Close Applications ======================
            elif query.startswith("open "):
                source = query.replace("open ", "").strip()
                if source:
                    open_app_or_file(source)
                else:
                    speak("Please specify what you want to open.")
                if is_actionable(query): last_actionable_query = query
            
            elif query.startswith("shift to "):
                target = query.replace("shift to ", "").strip()
                if not target:
                    speak("Please specify what you want me to shift to.")
                else:
                    # Try Chrome tab first
                    if "chrome" in target and "tab" in target:
                        site = target.replace("chrome", "").replace("tab", "").strip()
                        if site and shift_chrome_tab(site):
                            speak(f"Shifted to {site} tab in Chrome.")
                        elif bring_window_to_front("Chrome"):
                            speak("Shifted to Chrome.")
                        else:
                            speak("Chrome is not open.")
                    else:
                        # Try to bring general window forward
                        if bring_window_to_front(target):
                            speak(f"Shifted to {target}.")
                        else:
                            speak(f"I couldn’t find any window for {target}.")

            elif query == "close it":
                if last_opened_app:
                    close_app_or_file(last_opened_app)
                else:
                    speak("I don't know which application to close. Please specify.")
                if is_actionable(query): last_actionable_query = query

            elif query.startswith("close "):
                source = query.replace("close ", "").strip()
                if source:
                    close_app_or_file(source)
                else:
                    speak("Please specify what you want to close.")
                if is_actionable(query): last_actionable_query = query

            elif "follow the steps" in query or "follow my steps" in query or "follow my commands" in query or "follow my instructions" in query or "enter to the screen" in query or "check screen" in query:
                speak("Okay, sir!")
                while True:
                    step = take_command()
                    if not step:
                        continue
                    if "leave" in step or "stop" in step or "end steps" in step:
                        speak("Step following stopped.")
                        break
                    # Scan UI elements every time before executing
                    elements = scan_app_elements()
                    print("Scanned Elements:", elements[:15])  # just show first 15 for debug
                    # Try to match your step with a UI element
                    matched = False
                    for el in elements:
                        if el and el.lower() in step:
                            try:
                                dlg = Application(backend="uia").connect(active_only=True).top_window()
                                dlg[el].click_input()
                                speak(f"Clicked on {el}")
                                matched = True
                                break
                            except Exception as e:
                                print("Error clicking element:", e)
                    if not matched:
                        # If no element match, fallback to generic actions
                        handle_in_app_action(step, last_opened_app)

            # ==================== In-App Actions ======================
            elif any(phrase in query for phrase in ["scroll down", "scroll up", "click", "type", "search", "click"]):
                active_app = get_active_window()
                if active_app:
                    handle_in_app_action(query, active_app)
                else:
                    speak("I couldn't detect any active application.")
                if is_actionable(query): last_actionable_query = query

            # ================ Search and Explain =================
            elif query.startswith("tell me about"):
                topic = query.replace("tell me about", "").strip()
                if topic:
                    try:
                        speak(f"Let me tell you about {topic}")
                        summary = wikipedia.summary(topic, sentences=2)
                        print(summary)
                        speak(summary)
                    except Exception:
                        speak("Sorry, I couldn’t find details about that right now.")
                else:
                    speak("Please tell me clearly what you want me to explain.")
                if is_actionable(query): last_actionable_query = query

                        # ==================== System Configuration ======================
            elif "shutdown" in query:
                if CONFIRM_BEFORE_DESTRUCTIVE_ACTIONS:
                    speak(PERSONALITY_PRESETS[SIDD_MODE]["confirm"])
                    confirm = take_command()
                    if "yes" in confirm or "do it" in confirm:
                        speak("Shutting down your system, goodbye, Sir.")
                        os.system("shutdown /s /t 1")
                    else:
                        speak("Shutdown cancelled, Sir.")
                else:
                    speak("Shutting down your system, goodbye!")
                    os.system("shutdown /s /t 1")
                if is_actionable(query): last_actionable_query = query

            elif "restart" in query:
                if CONFIRM_BEFORE_DESTRUCTIVE_ACTIONS:
                    speak(PERSONALITY_PRESETS[SIDD_MODE]["confirm"])
                    confirm = take_command()
                    if "yes" in confirm or "do it" in confirm:
                        speak("Restarting your system now, Sir.")
                        os.system("shutdown /r /t 1")
                    else:
                        speak("Restart cancelled, Sir.")
                else:
                    speak("Restarting your system.")
                    os.system("shutdown /r /t 1")
                if is_actionable(query): last_actionable_query = query

            elif "log off" in query or "sign out" in query:
                if CONFIRM_BEFORE_DESTRUCTIVE_ACTIONS:
                    speak(PERSONALITY_PRESETS[SIDD_MODE]["confirm"])
                    confirm = take_command()
                    if "yes" in confirm or "do it" in confirm:
                        speak("Signing out now, Sir.")
                        os.system("shutdown /l")
                    else:
                        speak("Log off cancelled, Sir.")
                else:
                    speak("Signing out now.")
                    os.system("shutdown /l")
                if is_actionable(query): last_actionable_query = query

            elif "lock system" in query or "lock computer" in query:
                if CONFIRM_BEFORE_DESTRUCTIVE_ACTIONS:
                    speak(PERSONALITY_PRESETS[SIDD_MODE]["confirm"])
                    confirm = take_command()
                    if "yes" in confirm or "do it" in confirm:
                        speak("Locking your computer, Sir.")
                        os.system("rundll32.exe user32.dll,LockWorkStation")
                    else:
                        speak("Lock cancelled, Sir.")
                else:
                    speak("Locking your computer.")
                    os.system("rundll32.exe user32.dll,LockWorkStation")
                if is_actionable(query): last_actionable_query = query

            elif "off wi-fi" in query:
                os.system("netsh interface set interface Wi-Fi admin=disable")
                speak("Wi-Fi disabled.")
                if is_actionable(query): last_actionable_query = query

            elif "on wi-fi" in query:
                os.system("netsh interface set interface Wi-Fi admin=enable")
                speak("Wi-Fi enabled.")
                if is_actionable(query): last_actionable_query = query

            elif "screenshot" in query:
                filename = f"screenshot_{int(time.time())}.png"
                pyautogui.screenshot(filename)
                speak(f"Screenshot saved as {filename}")
                if is_actionable(query): last_actionable_query = query

            elif "battery" in query or "power" in query:
                battery = psutil.sensors_battery()
                percent = battery.percent
                plugged = "charging" if battery.power_plugged else "not charging"
                speak(f"Battery is at {percent} percent and is {plugged}.")
                if percent < 20 and not battery.power_plugged:
                    speak("Warning! Battery is below 20 percent. Please connect to a power source.")
                if is_actionable(query): last_actionable_query = query

            # ------------ Volume and Brightness Controls ------------
            elif "set volume" in query:
                level = query.replace("set volume to", "").strip().replace("%", "")
                set_volume(level)
                if is_actionable(query): last_actionable_query = query
            elif "increase volume" in query:
                pyautogui.press("volumeup", presses=5)
                speak("Volume increased.")
                if is_actionable(query): last_actionable_query = query
            elif "decrease volume" in query:
                pyautogui.press("volumedown", presses=5)
                speak("Volume decreased.")
                if is_actionable(query): last_actionable_query = query

            elif "mute" in query:
                pyautogui.press("volumemute")
                speak("Volume muted.")
                if is_actionable(query): last_actionable_query = query
            elif "unmute" in query:
                pyautogui.press("volumemute")
                speak("Volume unmuted.")
                if is_actionable(query): last_actionable_query = query

            elif "set brightness into" in query:
                try:
                    level = int(query.replace("set brightness into", "").strip().replace("%", ""))
                    set_brightness(level)
                except:
                    speak("Please say a number between 0 and 100.")
                if is_actionable(query): last_actionable_query = query
            elif "increase brightness" in query:
                increase_brightness()
                if is_actionable(query): last_actionable_query = query
            elif "decrease brightness" in query:
                decrease_brightness()
                if is_actionable(query): last_actionable_query = query

            elif "notify me" in query:
                show_notification("AI Assistant", "This is your notification test.")
                if is_actionable(query): last_actionable_query = query

            # ================ Notification Commands =================
            elif any(word in query for word in ["notification", "notifications", "message", "messages"]):
                query_lower = query.lower()
                # If user wants to read the latest
                if any(phrase in query_lower for phrase in ["read recent notification", "read recent message"]):
                    if notifications:
                        last_note = notifications[-1]
                        speak(f"Here is your latest notification: {last_note}")
                        print(last_note)
                    else:
                        speak("No recent notifications found.")
                else:
                    # Just asking if there are notifications/messages
                    if notifications:
                        speak("Yes, you have notifications.")
                    else:
                        speak("No, you don't have any notifications.")
                if is_actionable(query): last_actionable_query = query

            # ================ Quit/Exit =================
            elif 'quit' in query or 'exit' in query or 'goodbye' in query or 'stop' in query or 'get out' in query or 'leave' in query:
                farewell_responses = [
                    "Goodbye Sir! Take care.",
                    "See you later! Have a wonderful day."
                ]
                speak(random.choice(farewell_responses))
                break

            else:
                # 1) First, check if we already learned a response for this query
                learned = find_learned_response(query)
                if learned:
                    speak(learned)
                else:
                    # 2) New unknown query → ask user what to reply and save it
                    if last_query != query:
                        speak("That's outside my current knowledge Sir. Shall I learn it from you?")
                        command = take_command()

                        if is_positive_reply(command):
                            speak("Please tell me what I should reply.")
                            answer = take_command()
                            if answer:
                                add_learned_response(query, answer)
                                speak("Got it, I will remember that.")
                                last_query = query
                            else:
                                speak("I couldn't hear any reply to learn.")
                                last_query = ""

                        elif is_negative_reply(command):
                            speak("Alright, Sir.")
                            last_query = ""

                        else:
                            speak("I couldn't hear any reply to learn.")
                        last_query = query

                if is_actionable(query):
                    last_actionable_query = query
    except KeyboardInterrupt:
        speak("Session ended. Goodbye!")

if __name__ == "__main__":
    main()