import asyncio
import json
import logging
import os
import re
import sqlite3
import unicodedata
from collections import deque
from datetime import UTC, datetime
from urllib.parse import quote

import aiohttp
import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks

# =================================
# KOSMICZNY ZEGAR PUBLIC - BOT
# WERSJA: czysta baza + wszystkie kanały + zegar w statusie bota
# =================================

LOG_FILE = os.getenv("LOG_FILE", "bot.log")
TOKEN = os.getenv("DISCORD_TOKEN")
DB_FILE = os.getenv("DB_FILE", "bot_data_public.db")

DEFAULT_CITY_NAME = "Rzeszów"
DEFAULT_LATITUDE = 50.0413
DEFAULT_LONGITUDE = 21.9990
DEFAULT_COUNTRY = "Polska"
DEFAULT_TIMEZONE = "Europe/Warsaw"
DEFAULT_LANGUAGE = "pl"

WEATHER_REFRESH_MINUTES = 2
CLOCK_REFRESH_SECONDS = 30
STATS_FALLBACK_REFRESH_SECONDS = 90
PRESENCE_REFRESH_SECONDS = 90
STATUS_CLOCK_REFRESH_SECONDS = PRESENCE_REFRESH_SECONDS
ONLINE_CHANNEL_MIN_UPDATE_SECONDS = 180
CHANNEL_CREATE_DELAY = 0.02
CHANNEL_DELETE_DELAY = 0.06
CHANNEL_EDIT_DELAY = 0.12
WEATHER_API_MIN_INTERVAL_SECONDS = 120
MAX_CHANNEL_NAME_LENGTH = 100



PRIORITY_SETUP = 0
PRIORITY_ADMIN = 1
PRIORITY_STATS = 2
PRIORITY_CLOCK = 3
PRIORITY_WEATHER = 4
PRIORITY_DEFAULT = 5


# =================================
# SYSTEM STATUSÓW / PANEL RÓL
# =================================

STATUS_ROLE_IDS = {
    "dostepny": 1475627194582831184,
    "zaraz_wracam": 1475595615055511747,
    "afk": 1475592478286676160,
    "nocny_tryb": 1475626089597374680,
    "nie_przeszkadzac": 1475627340494278727,
    "poza_kompem": 1475627428217884764,
    "poza_domem": 1475627463865270404,
    "w_pracy": 1475627537022582804,
    "w_szkole": 1475627641582391457,
    "ide_spac": 1475627705188880547,
    "nowy_tutaj": 1475592165227761704,
    "chce_poznac_nowych_ludzi": 1475595483899494492,
}

MOOD_ROLE_IDS = {
    "na_luzie": 1475616916348604618,
    "full_energia": 1475625987914858677,
    "w_dobrym_humorze": 1475625302641086504,
    "wkurzony": 1475625886886662324,
    "chory": 1475645832702328884,
    "zmeczony": 1475625667075768395,
}

ACTIVITY_ROLE_IDS = {
    "slucham_muzyki": 1475586115569324043,
    "czatuje": 1475591441085366273,
    "gram": 1475591583314477278,
    "ucze_sie": 1475594865860542554,
    "na_vc": 1475595019770396932,
    "streamuje": 1475595081200304259,
    "ogladam_streama": 1475596164026859745,
}

ROLE_GROUPS = {
    "status": STATUS_ROLE_IDS,
    "mood": MOOD_ROLE_IDS,
    "activity": ACTIVITY_ROLE_IDS,
}

ROLE_GROUP_DISPLAY = {
    "status": "🟢 Status",
    "mood": "😎 Nastrój",
    "activity": "🎮 Aktywność",
}

ROLE_DISPLAY_NAMES = {
    "dostepny": "Dostępny",
    "zaraz_wracam": "Zaraz wracam",
    "afk": "AFK",
    "nocny_tryb": "Nocny tryb",
    "nie_przeszkadzac": "Nie przeszkadzać",
    "poza_kompem": "Poza kompem",
    "poza_domem": "Poza domem",
    "w_pracy": "W pracy",
    "w_szkole": "W szkole",
    "ide_spac": "Idę spać",
    "nowy_tutaj": "Nowy tutaj",
    "chce_poznac_nowych_ludzi": "Chcę poznać nowych ludzi",
    "na_luzie": "Na luzie",
    "full_energia": "Full energia",
    "w_dobrym_humorze": "W dobrym humorze",
    "wkurzony": "Wkurzony",
    "chory": "Chory",
    "zmeczony": "Zmęczony",
    "slucham_muzyki": "Słucham muzyki",
    "czatuje": "Czatuję",
    "gram": "Gram",
    "ucze_sie": "Uczę się",
    "na_vc": "Na VC",
    "streamuje": "Streamuję",
    "ogladam_streama": "Oglądam streama",
}

ROLE_EMOJIS = {
    "dostepny": "🟢",
    "zaraz_wracam": "⏳",
    "afk": "😴",
    "nocny_tryb": "🌙",
    "nie_przeszkadzac": "⛔",
    "poza_kompem": "🖥️",
    "poza_domem": "🚪",
    "w_pracy": "💼",
    "w_szkole": "📚",
    "ide_spac": "🛌",
    "nowy_tutaj": "👋",
    "chce_poznac_nowych_ludzi": "🤝",
    "na_luzie": "😎",
    "full_energia": "⚡",
    "w_dobrym_humorze": "😊",
    "wkurzony": "😡",
    "chory": "🤒",
    "zmeczony": "🥱",
    "slucham_muzyki": "🎧",
    "czatuje": "💬",
    "gram": "🎮",
    "ucze_sie": "📖",
    "na_vc": "🗣️",
    "streamuje": "📡",
    "ogladam_streama": "👀",
}

STATUS_PANEL_STORAGE_FILE = os.getenv("STATUS_PANEL_STORAGE_FILE", "status_panel.json")
STATUS_ROLE_DEBOUNCE_SECONDS = 12

# =================================
# LOGOWANIE
# =================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers.clear()

formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# =================================
# GLOBALNE
# =================================

bot_start_time = datetime.now(UTC)

channel_edit_priority_queue: asyncio.PriorityQueue[tuple[int, int]] = asyncio.PriorityQueue()
channel_last_desired_name: dict[int, str] = {}
dead_channel_ids: set[int] = set()
channel_edit_locks: dict[int, asyncio.Lock] = {}
_channel_edit_worker_task: asyncio.Task | None = None

recent_channel_edit_times: deque[datetime] = deque(maxlen=240)

weather_cache: dict[int, dict] = {}
weather_cache_fetched_at: dict[int, datetime] = {}

last_presence_text: str | None = None
last_online_channel_update_at: dict[int, datetime] = {}

status_panel_update_tasks: dict[int, asyncio.Task] = {}
status_panel_refresh_locks: dict[int, asyncio.Lock] = {}
status_role_refresh_tasks: dict[int, asyncio.Task] = {}
stats_update_tasks: dict[int, asyncio.Task] = {}
background_refresh_tasks: dict[int, asyncio.Task] = {}

last_clock_snapshot: dict[int, dict[str, str]] = {}
last_weather_snapshot: dict[int, dict[str, str]] = {}

# =================================
# BOT
# =================================


class KosmicznyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http_session: aiohttp.ClientSession | None = None
        self.synced_once = False

    async def setup_hook(self):
        timeout = aiohttp.ClientTimeout(total=20)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        await ensure_channel_edit_worker_running()

    async def close(self):
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.presences = True
intents.message_content = True

bot = KosmicznyBot(command_prefix="!", intents=intents)

# =================================
# JĘZYKI I NAZWY
# =================================

CHANNEL_TEMPLATE_KEYS = {
    "temperature": ("weather", "ch_temperature"),
    "feels": ("weather", "ch_feels"),
    "clouds": ("weather", "ch_clouds"),
    "air": ("weather", "ch_air"),
    "pollen": ("weather", "ch_pollen"),
    "rain": ("weather", "ch_rain"),
    "wind": ("weather", "ch_wind"),
    "pressure": ("weather", "ch_pressure"),
    "alerts": ("weather", "ch_alerts"),
    "allergy_live": ("allergy", "ch_allergy_live"),
    "allergy_alert": ("allergy", "ch_allergy_alert"),
    "allergy_advice": ("allergy", "ch_allergy_advice"),
    "date": ("clock", "ch_date"),
    "part_of_day": ("clock", "ch_part_of_day"),
    "sunrise": ("clock", "ch_sunrise"),
    "sunset": ("clock", "ch_sunset"),
    "day_length": ("clock", "ch_day_length"),
    "moon": ("clock", "ch_moon"),
    "members": ("stats", "ch_members"),
    "humans": ("stats", "ch_humans"),
    "online": ("stats", "ch_online"),
    "bots": ("stats", "ch_bots"),
    "vc": ("stats", "ch_vc"),
    "joined_today": ("stats", "ch_joined_today"),
    "bans": ("stats", "ch_bans"),
}

LANGUAGES = {
    "pl": {
        "lang_name": "Polski",
        "cat_weather": "🌤️ Pogoda",
        "cat_clock": "🛰️ Kosmiczny Zegar",
        "cat_stats": "📊 Statystyki",
        "ch_temperature": "🌡 Temperatura",
        "ch_feels": "🥵 Odczuwalna",
        "ch_clouds": "☁ Zachmurzenie",
        "ch_air": "🌫 Powietrze",
        "ch_pollen": "🌿 Pylenie",
        "ch_rain": "🌧 Opady",
        "ch_wind": "💨 Wiatr",
        "ch_pressure": "⏱ Ciśnienie",
        "ch_alerts": "🟢 ALERT brak",
        "ch_allergy_live": "🌿 Pylenie live",
        "ch_allergy_alert": "🚨 Alert alergiczny",
        "ch_allergy_advice": "💊 Na co uważać",
        "ch_date": "📅 Data",
        "ch_part_of_day": "🌓 Pora dnia",
        "ch_sunrise": "🌅 Wschód",
        "ch_sunset": "🌇 Zachód",
        "ch_day_length": "☀️ Dzień",
        "ch_moon": "🌙 Faza księżyca",
        "ch_members": "👥 Wszyscy",
        "ch_humans": "👤 Ludzie",
        "ch_online": "🟢 Online",
        "ch_bots": "🤖 Boty",
        "ch_vc": "🔊 Na VC",
        "ch_joined_today": "📥 Dzisiaj weszło 0",
        "ch_bans": "🔨 Bany 0",
        "only_server": "❌ Tej komendy można użyć tylko na serwerze.",
        "setup_ok": "✅ Utworzono i odświeżono wszystkie kategorie oraz kanały.",
        "setup_error": "❌ Błąd setupu: {error}",
        "refresh_no_config": "ℹ️ Brak konfiguracji. Najpierw użyj /setup.",
        "refresh_ok": "✅ Wszystkie kanały zostały odświeżone.",
        "refresh_error": "❌ Błąd refreshu: {error}",
        "no_config": "ℹ️ Brak konfiguracji. Użyj /setup.",
        "city_setup_first": "ℹ️ Najpierw użyj /setup, aby utworzyć kategorie i kanały.",
        "city_not_found": "❌ Nie znaleziono miasta: {city}",
        "city_updated": "✅ Ustawiono miasto: {city} i rozpoczęto aktualizację.",
        "city_error": "❌ Błąd ustawiania miasta: {error}",
        "language_set": "✅ Ustawiono język bota na: Polski",
        "language_invalid": "❌ Nieobsługiwany język. Dostępne: pl, en",
        "status_title": "📊 Status Kosmicznego Zegara",
        "status_weather_cat": "Kategoria Pogoda",
        "status_clock_cat": "Kategoria Kosmiczny Zegar",
        "status_stats_cat": "Kategoria Statystyki",
        "status_allergy_cat": "Kategoria Ostrzeżenia dla alergików",
        "status_saved_channels": "Zapisane kanały",
        "status_city": "Miasto",
        "status_lat": "Szerokość",
        "status_lon": "Długość",
        "status_timezone": "Strefa czasowa",
        "status_language": "Język",
        "weather_title": "🌤️ Pogoda - {city}, {country}",
        "field_temperature": "Temperatura",
        "field_feels": "Odczuwalna",
        "field_clouds": "Zachmurzenie",
        "field_air": "Powietrze",
        "field_pollen": "Pylenie",
        "field_rain": "Opady",
        "field_wind": "Wiatr",
        "field_pressure": "Ciśnienie",
        "field_alerts": "Alerty",
        "field_allergy_alert": "Alert alergiczny",
        "field_allergy_advice": "Na co uważać",
        "field_sunrise": "Wschód",
        "field_sunset": "Zachód",
        "field_day_length": "Długość dnia",
        "none": "brak",
        "time_title": "🕐 Aktualny czas",
        "time_city": "Miasto",
        "time_clock": "Godzina",
        "time_date": "Data",
        "time_part_of_day": "Pora dnia",
        "time_timezone": "Strefa czasowa",
        "weekday_short": ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."],
        "stats_members": "👥 Wszyscy {count}",
        "stats_humans": "👤 Ludzie {count}",
        "stats_online": "🟢 Online {count}",
        "stats_bots": "🤖 Boty {count}",
        "stats_vc": "🔊 Na VC {count}",
        "stats_joined_today": "📥 Dzisiaj weszło {count}",
        "stats_bans": "🔨 Bany {count}",
    },
    "en": {
        "lang_name": "English",
        "cat_weather": "🌤️ Weather",
        "cat_clock": "🛰️ Cosmic Clock",
        "cat_stats": "📊 Statistics",
        "ch_temperature": "🌡 Temperature",
        "ch_feels": "🥵 Feels like",
        "ch_clouds": "☁ Clouds",
        "ch_air": "🌫 Air quality",
        "ch_pollen": "🌿 Pollen",
        "ch_rain": "🌧 Precipitation",
        "ch_wind": "💨 Wind",
        "ch_pressure": "⏱ Pressure",
        "ch_alerts": "🟢 ALERT none",
        "ch_allergy_live": "🌿 Pollen live",
        "ch_allergy_alert": "🚨 Allergy alert",
        "ch_allergy_advice": "💊 Watch out",
        "ch_date": "📅 Date",
        "ch_part_of_day": "🌓 Part of day",
        "ch_sunrise": "🌅 Sunrise",
        "ch_sunset": "🌇 Sunset",
        "ch_day_length": "☀️ Day length",
        "ch_moon": "🌙 Moon phase",
        "ch_members": "👥 Members",
        "ch_humans": "👤 Humans",
        "ch_online": "🟢 Online",
        "ch_bots": "🤖 Bots",
        "ch_vc": "🔊 In VC",
        "ch_joined_today": "📥 Joined today 0",
        "ch_bans": "🔨 Bans 0",
        "only_server": "❌ This command can only be used in a server.",
        "setup_ok": "✅ All categories and channels have been created and refreshed.",
        "setup_error": "❌ Setup error: {error}",
        "refresh_no_config": "ℹ️ No configuration found. Use /setup first.",
        "refresh_ok": "✅ All channels have been refreshed.",
        "refresh_error": "❌ Refresh error: {error}",
        "no_config": "ℹ️ No configuration found. Use /setup.",
        "city_setup_first": "ℹ️ Use /setup first to create categories and channels.",
        "city_not_found": "❌ City not found: {city}",
        "city_updated": "✅ City set to: {city} and refresh started.",
        "city_error": "❌ Error while setting city: {error}",
        "language_set": "✅ Bot language set to: English",
        "language_invalid": "❌ Unsupported language. Available: pl, en",
        "status_title": "📊 Cosmic Clock Status",
        "status_weather_cat": "Weather category",
        "status_clock_cat": "Cosmic Clock category",
        "status_stats_cat": "Statistics category",
        "status_allergy_cat": "Allergy warnings category",
        "status_saved_channels": "Saved channels",
        "status_city": "City",
        "status_lat": "Latitude",
        "status_lon": "Longitude",
        "status_timezone": "Timezone",
        "status_language": "Language",
        "weather_title": "🌤️ Weather - {city}, {country}",
        "field_temperature": "Temperature",
        "field_feels": "Feels like",
        "field_clouds": "Cloud cover",
        "field_air": "Air quality",
        "field_pollen": "Pollen",
        "field_rain": "Precipitation",
        "field_wind": "Wind",
        "field_pressure": "Pressure",
        "field_alerts": "Alerts",
        "field_allergy_alert": "Allergy alert",
        "field_allergy_advice": "Watch out",
        "field_sunrise": "Sunrise",
        "field_sunset": "Sunset",
        "field_day_length": "Day length",
        "none": "none",
        "time_title": "🕐 Current time",
        "time_city": "City",
        "time_clock": "Time",
        "time_date": "Date",
        "time_part_of_day": "Part of day",
        "time_timezone": "Timezone",
        "weekday_short": ["Mon.", "Tue.", "Wed.", "Thu.", "Fri.", "Sat.", "Sun."],
        "stats_members": "👥 Members {count}",
        "stats_humans": "👤 Humans {count}",
        "stats_online": "🟢 Online {count}",
        "stats_bots": "🤖 Bots {count}",
        "stats_vc": "🔊 In VC {count}",
        "stats_joined_today": "📥 Joined today {count}",
        "stats_bans": "🔨 Bans {count}",
    },
}

# =================================
# BAZA
# =================================


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            weather_category_id INTEGER,
            clock_category_id INTEGER,
            stats_category_id INTEGER,
            allergy_category_id INTEGER,
            channels_json TEXT,
            city_name TEXT,
            latitude REAL,
            longitude REAL,
            country TEXT,
            timezone TEXT,
            language TEXT,
            status_panel_channel_id INTEGER,
            status_panel_message_id INTEGER
        )
        """
    )
    conn.commit()

    c.execute("PRAGMA table_info(guild_config)")
    columns = [row[1] for row in c.fetchall()]

    if "country" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN country TEXT")
    if "timezone" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN timezone TEXT")
    if "language" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN language TEXT")
    if "allergy_category_id" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN allergy_category_id INTEGER")
    if "status_panel_channel_id" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN status_panel_channel_id INTEGER")
    if "status_panel_message_id" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN status_panel_message_id INTEGER")

    conn.commit()
    conn.close()


def get_guild_config(guild_id: int) -> dict | None:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute(
        """
        SELECT guild_id, weather_category_id, clock_category_id, stats_category_id, allergy_category_id,
               channels_json, city_name, latitude, longitude, country, timezone, language,
               status_panel_channel_id, status_panel_message_id
        FROM guild_config
        WHERE guild_id=?
        """,
        (guild_id,),
    )

    row = c.fetchone()
    conn.close()

    if not row:
        return None

    try:
        channels = json.loads(row[5]) if row[5] else {}
    except Exception:
        channels = {}

    return {
        "guild_id": row[0],
        "weather_category_id": row[1],
        "clock_category_id": row[2],
        "stats_category_id": row[3],
        "allergy_category_id": row[4],
        "channels": channels,
        "city_name": row[6] or DEFAULT_CITY_NAME,
        "latitude": row[7] if row[7] is not None else DEFAULT_LATITUDE,
        "longitude": row[8] if row[8] is not None else DEFAULT_LONGITUDE,
        "country": row[9] or DEFAULT_COUNTRY,
        "timezone": row[10] or DEFAULT_TIMEZONE,
        "language": row[11] or DEFAULT_LANGUAGE,
        "status_panel_channel_id": row[12],
        "status_panel_message_id": row[13],
    }


def save_guild_config(guild_id: int, cfg: dict):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute(
        """
        INSERT OR REPLACE INTO guild_config (
            guild_id,
            weather_category_id,
            clock_category_id,
            stats_category_id,
            allergy_category_id,
            channels_json,
            city_name,
            latitude,
            longitude,
            country,
            timezone,
            language,
            status_panel_channel_id,
            status_panel_message_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            cfg.get("weather_category_id"),
            cfg.get("clock_category_id"),
            cfg.get("stats_category_id"),
            cfg.get("allergy_category_id"),
            json.dumps(cfg.get("channels", {}), ensure_ascii=False),
            cfg.get("city_name", DEFAULT_CITY_NAME),
            cfg.get("latitude", DEFAULT_LATITUDE),
            cfg.get("longitude", DEFAULT_LONGITUDE),
            cfg.get("country", DEFAULT_COUNTRY),
            cfg.get("timezone", DEFAULT_TIMEZONE),
            cfg.get("language", DEFAULT_LANGUAGE),
            cfg.get("status_panel_channel_id"),
            cfg.get("status_panel_message_id"),
        ),
    )

    conn.commit()
    conn.close()


def build_default_guild_config(guild_id: int) -> dict:
    return {
        "guild_id": guild_id,
        "weather_category_id": None,
        "clock_category_id": None,
        "stats_category_id": None,
        "allergy_category_id": None,
        "channels": {},
        "city_name": DEFAULT_CITY_NAME,
        "latitude": DEFAULT_LATITUDE,
        "longitude": DEFAULT_LONGITUDE,
        "country": DEFAULT_COUNTRY,
        "timezone": DEFAULT_TIMEZONE,
        "language": DEFAULT_LANGUAGE,
        "status_panel_channel_id": None,
        "status_panel_message_id": None,
    }


# =================================
# POMOCNICZE
# =================================


def get_lang_code(cfg: dict | None) -> str:
    if not cfg:
        return DEFAULT_LANGUAGE
    lang = cfg.get("language", DEFAULT_LANGUAGE)
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def tr(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE
    text = LANGUAGES[lang].get(key, LANGUAGES[DEFAULT_LANGUAGE].get(key, key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def get_timezone_object(timezone_name: str):
    try:
        return pytz.timezone(timezone_name)
    except Exception:
        return pytz.timezone(DEFAULT_TIMEZONE)


def trim_channel_name(text: str) -> str:
    text = " ".join(str(text).split())
    return text[:MAX_CHANNEL_NAME_LENGTH].strip()


def normalize_channel_name(name: str) -> str:
    if not name:
        return ""
    normalized = unicodedata.normalize("NFKD", str(name))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = (
        normalized.lower()
        .replace("–", "-")
        .replace("—", "-")
        .replace("→", "-")
        .replace("•", " ")
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def get_channel_fallback_name(lang: str, key: str) -> str:
    _, translation_key = CHANNEL_TEMPLATE_KEYS[key]
    return tr(lang, translation_key)


def get_category_name(lang: str, group_name: str) -> str:
    mapping = {
        "weather": tr(lang, "cat_weather"),
        "clock": tr(lang, "cat_clock"),
        "stats": tr(lang, "cat_stats"),
        "allergy": tr(lang, "cat_allergy"),
    }
    return mapping[group_name]


def get_channel_lock(channel_id: int) -> asyncio.Lock:
    if channel_id not in channel_edit_locks:
        channel_edit_locks[channel_id] = asyncio.Lock()
    return channel_edit_locks[channel_id]


def build_channel_snapshot(mapping: dict[str, str]) -> dict[str, str]:
    return {key: trim_channel_name(value) for key, value in mapping.items()}


def get_recent_channel_edit_count(window_seconds: int = 60) -> int:
    now = datetime.now(UTC)
    return sum(1 for ts in recent_channel_edit_times if (now - ts).total_seconds() < window_seconds)


def find_voice_channel_in_category_by_name(
    category: discord.CategoryChannel | None, name: str
) -> discord.VoiceChannel | None:
    if category is None:
        return None
    for channel in category.voice_channels:
        if channel.name == name:
            return channel
    return None


def get_channel_base_names(key: str) -> list[str]:
    _, translation_key = CHANNEL_TEMPLATE_KEYS[key]
    names: list[str] = []

    for lang_code in LANGUAGES:
        translated = tr(lang_code, translation_key)
        if translated not in names:
            names.append(translated)

        stripped = re.sub(r"^[^\w\d]+\s*", "", translated).strip()
        if stripped and stripped not in names:
            names.append(stripped)

    return names


def channel_name_matches_base(channel_name: str, base_names: list[str]) -> bool:
    current = normalize_channel_name(channel_name)

    for base in base_names:
        base_norm = normalize_channel_name(base)
        if not base_norm:
            continue
        if current == base_norm or current.startswith(base_norm + " ") or current.startswith(base_norm + "-"):
            return True

    return False


def find_matching_channel_for_key(
    guild: discord.Guild, cfg: dict, key: str
) -> discord.VoiceChannel | None:
    if key not in CHANNEL_TEMPLATE_KEYS:
        return None

    group_name, _ = CHANNEL_TEMPLATE_KEYS[key]
    category_id = cfg.get(f"{group_name}_category_id")
    category = guild.get_channel(category_id) if category_id else None

    fallback_names = get_channel_base_names(key)
    search_space = category.voice_channels if isinstance(category, discord.CategoryChannel) else guild.voice_channels

    matches = [
        ch
        for ch in search_space
        if isinstance(ch, discord.VoiceChannel) and channel_name_matches_base(ch.name, fallback_names)
    ]

    if len(matches) == 1:
        return matches[0]

    return None


def get_channel_from_config(guild: discord.Guild, cfg: dict, key: str):
    channels = cfg.get("channels", {})
    channel_id = channels.get(key)

    if channel_id:
        ch = guild.get_channel(channel_id)
        if isinstance(ch, discord.VoiceChannel):
            return ch

    repaired = find_matching_channel_for_key(guild, cfg, key)
    if repaired is not None:
        channels = dict(cfg.get("channels", {}))
        channels[key] = repaired.id
        cfg["channels"] = channels
        save_guild_config(guild.id, cfg)
        logging.warning(
            "[AUTO-ID] Naprawiono ID kanału %s na serwerze %s -> %s (%s)",
            key,
            guild.name,
            repaired.id,
            repaired.name,
        )
        return repaired

    return None


def channel_snapshot_is_applied(guild: discord.Guild, cfg: dict, snapshot: dict[str, str]) -> bool:
    for key, expected_name in snapshot.items():
        channel = get_channel_from_config(guild, cfg, key)
        if channel is None or trim_channel_name(channel.name) != trim_channel_name(expected_name):
            return False
    return True



def get_status_panel_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in status_panel_refresh_locks:
        status_panel_refresh_locks[guild_id] = asyncio.Lock()
    return status_panel_refresh_locks[guild_id]


def get_all_status_role_ids() -> set[int]:
    ids: set[int] = set()
    for group in ROLE_GROUPS.values():
        ids.update(group.values())
    return ids


def get_role_group_for_key(role_key: str) -> str | None:
    for group_name, roles in ROLE_GROUPS.items():
        if role_key in roles:
            return group_name
    return None


def get_role_by_key(guild: discord.Guild, role_key: str) -> discord.Role | None:
    role_id = None
    for roles in ROLE_GROUPS.values():
        if role_key in roles:
            role_id = roles[role_key]
            break
    if role_id is None:
        return None
    role = guild.get_role(role_id)
    return role


def build_role_label(role_key: str) -> str:
    emoji = ROLE_EMOJIS.get(role_key, "")
    name = ROLE_DISPLAY_NAMES.get(role_key, role_key)
    return f"{emoji} {name}".strip()


def count_members_with_role(guild: discord.Guild, role_id: int) -> int:
    role = guild.get_role(role_id)
    if role is None:
        return 0
    return sum(1 for member in role.members if not member.bot)


def build_status_panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="✨ Panel statusów",
        description="Każdy może ustawić sobie prywatnie status komendą `/statusy`.",
        color=discord.Color.blurple(),
    )

    for group_name, roles in ROLE_GROUPS.items():
        lines = []
        for role_key, role_id in roles.items():
            count = count_members_with_role(guild, role_id)
            role = guild.get_role(role_id)
            mention = role.mention if role else f"`{ROLE_DISPLAY_NAMES.get(role_key, role_key)}`"
            lines.append(f"{build_role_label(role_key)} — {mention} **{count}**")
        embed.add_field(
            name=ROLE_GROUP_DISPLAY.get(group_name, group_name.title()),
            value="\n".join(lines) if lines else "Brak ról.",
            inline=False,
        )

    embed.set_footer(text="Panel odświeża się automatycznie po zmianie statusu.")
    return embed


def load_status_panel_storage() -> dict[str, dict[str, int]]:
    if not os.path.exists(STATUS_PANEL_STORAGE_FILE):
        return {}
    try:
        with open(STATUS_PANEL_STORAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_status_panel_storage(data: dict[str, dict[str, int]]):
    try:
        with open(STATUS_PANEL_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("Nie udało się zapisać %s: %s", STATUS_PANEL_STORAGE_FILE, e)


def save_status_panel_reference(guild_id: int, channel_id: int | None, message_id: int | None):
    data = load_status_panel_storage()
    key = str(guild_id)
    if channel_id is None or message_id is None:
        data.pop(key, None)
    else:
        data[key] = {"channel_id": int(channel_id), "message_id": int(message_id)}
    save_status_panel_storage(data)


def load_status_panel_reference(guild_id: int) -> tuple[int | None, int | None]:
    data = load_status_panel_storage()
    item = data.get(str(guild_id), {})
    try:
        return item.get("channel_id"), item.get("message_id")
    except Exception:
        return None, None


async def refresh_status_panel(guild: discord.Guild, *, force: bool = False) -> bool:
    cfg = get_guild_config(guild.id)
    if not cfg:
        return False

    channel_id = cfg.get("status_panel_channel_id")
    message_id = cfg.get("status_panel_message_id")

    if not channel_id or not message_id:
        file_channel_id, file_message_id = load_status_panel_reference(guild.id)
        if file_channel_id and file_message_id:
            channel_id = file_channel_id
            message_id = file_message_id
            cfg["status_panel_channel_id"] = channel_id
            cfg["status_panel_message_id"] = message_id
            save_guild_config(guild.id, cfg)

    if not channel_id or not message_id:
        return False

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        cfg["status_panel_channel_id"] = None
        cfg["status_panel_message_id"] = None
        save_guild_config(guild.id, cfg)
        save_status_panel_reference(guild.id, None, None)
        return False

    lock = get_status_panel_lock(guild.id)
    async with lock:
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=build_status_panel_embed(guild), view=PublicStatusPanelLauncherView())
            return True
        except discord.NotFound:
            cfg["status_panel_channel_id"] = None
            cfg["status_panel_message_id"] = None
            save_guild_config(guild.id, cfg)
            save_status_panel_reference(guild.id, None, None)
            return False
        except discord.Forbidden:
            logging.warning("Brak uprawnień do odświeżenia panelu statusów na %s", guild.id)
            return False
        except discord.HTTPException as e:
            logging.warning("Błąd odświeżania panelu statusów na %s: %s", guild.id, e)
            return False


def schedule_status_panel_refresh(guild: discord.Guild, *, delay: float = 1.0):
    existing = status_panel_update_tasks.get(guild.id)
    if existing and not existing.done():
        return

    async def runner():
        try:
            await asyncio.sleep(delay)
            await refresh_status_panel(guild)
        finally:
            status_panel_update_tasks.pop(guild.id, None)

    status_panel_update_tasks[guild.id] = asyncio.create_task(runner())


async def clear_member_role_group(member: discord.Member, group_name: str):
    role_ids = set(ROLE_GROUPS.get(group_name, {}).values())
    removable = [role for role in member.roles if role.id in role_ids]
    if removable:
        await member.remove_roles(*removable, reason="Zmiana statusu użytkownika")


async def set_member_status_role(member: discord.Member, role_key: str) -> str:
    guild = member.guild
    role = get_role_by_key(guild, role_key)
    if role is None:
        raise RuntimeError(f"Nie znaleziono roli dla klucza: {role_key}")

    bot_member = guild.me or guild.get_member(bot.user.id) if bot.user else None
    if bot_member is None:
        raise RuntimeError("Bot nie widzi swojej roli na serwerze.")

    if not guild.me.guild_permissions.manage_roles:
        raise RuntimeError("Bot nie ma uprawnienia Manage Roles.")

    if role >= bot_member.top_role:
        raise RuntimeError("Rola bota jest za nisko w hierarchii, żeby nadać tę rolę.")

    group_name = get_role_group_for_key(role_key)
    if group_name is None:
        raise RuntimeError("Nie udało się ustalić kategorii roli.")

    await clear_member_role_group(member, group_name)
    if role not in member.roles:
        await member.add_roles(role, reason="Ustawienie statusu użytkownika")
    schedule_status_panel_refresh(guild, delay=1.0)
    return ROLE_DISPLAY_NAMES.get(role_key, role_key)


async def clear_member_all_status_roles(member: discord.Member):
    all_ids = get_all_status_role_ids()
    removable = [role for role in member.roles if role.id in all_ids]
    if removable:
        await member.remove_roles(*removable, reason="Wyczyszczenie statusów użytkownika")
    schedule_status_panel_refresh(member.guild, delay=1.0)


class StatusRoleSelect(discord.ui.Select):
    def __init__(self, group_name: str):
        options = []
        for role_key in ROLE_GROUPS[group_name]:
            options.append(
                discord.SelectOption(
                    label=ROLE_DISPLAY_NAMES.get(role_key, role_key)[:100],
                    value=role_key,
                    emoji=ROLE_EMOJIS.get(role_key) or None,
                )
            )

        placeholder_map = {
            "status": "Wybierz status",
            "mood": "Wybierz nastrój",
            "activity": "Wybierz aktywność",
        }

        super().__init__(
            placeholder=placeholder_map.get(group_name, "Wybierz"),
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"status_select:{group_name}",
        )
        self.group_name = group_name

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await send_interaction_message(interaction, "❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
            return

        role_key = self.values[0]
        try:
            role_name = await set_member_status_role(interaction.user, role_key)
            await interaction.response.send_message(
                f"✅ Ustawiono: **{role_name}**.",
                ephemeral=True,
            )
        except Exception as e:
            await send_interaction_message(interaction, f"❌ Nie udało się ustawić statusu: {e}", ephemeral=True)


class PrivateStatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(StatusRoleSelect("status"))
        self.add_item(StatusRoleSelect("mood"))
        self.add_item(StatusRoleSelect("activity"))

    @discord.ui.button(label="Wyczyść wszystko", style=discord.ButtonStyle.danger, custom_id="status_clear_all")
    async def clear_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await send_interaction_message(interaction, "❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
            return

        try:
            await clear_member_all_status_roles(interaction.user)
            await interaction.response.send_message("🧹 Wyczyściłem Twoje statusy.", ephemeral=True)
        except Exception as e:
            await send_interaction_message(interaction, f"❌ Nie udało się wyczyścić statusów: {e}", ephemeral=True)


class PublicStatusPanelLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ustaw swój status",
        style=discord.ButtonStyle.success,
        emoji="✨",
        custom_id="status_panel_open_private",
    )
    async def open_private_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_interaction_message(
            interaction,
            "Wybierz swój status w prywatnym okienku poniżej.",
            ephemeral=True,
            view=PrivateStatusView(),
        )
# =================================
# INTERACTION
# =================================


async def maybe_defer(interaction: discord.Interaction, ephemeral: bool = True):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
    except Exception:
        pass


async def send_interaction_message(
    interaction: discord.Interaction,
    content: str | None = None,
    ephemeral: bool = True,
    **kwargs,
):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, ephemeral=ephemeral, **kwargs)
        else:
            await interaction.response.send_message(content=content, ephemeral=ephemeral, **kwargs)
    except Exception:
        try:
            await interaction.followup.send(content=content, ephemeral=ephemeral, **kwargs)
        except Exception:
            pass


# =================================
# EDYCJA KANAŁÓW
# =================================


async def queue_channel_edit_priority(channel, new_name: str, priority: int = PRIORITY_DEFAULT):
    if channel is None:
        return

    channel_id = getattr(channel, "id", None)
    if channel_id is None or channel_id in dead_channel_ids:
        return

    new_name = trim_channel_name(new_name)
    if not new_name or getattr(channel, "name", None) == new_name:
        return

    if channel_last_desired_name.get(channel_id) == new_name:
        return

    channel_last_desired_name[channel_id] = new_name
    await channel_edit_priority_queue.put((priority, channel_id))


async def _apply_channel_name_edit(channel: discord.abc.GuildChannel | None, new_name: str):
    if channel is None:
        return

    new_name = trim_channel_name(new_name)
    if not new_name or channel.name == new_name:
        return

    lock = get_channel_lock(channel.id)

    async with lock:
        if channel.name == new_name:
            return

        try:
            await channel.edit(name=new_name)
            recent_channel_edit_times.append(datetime.now(UTC))
            logging.info("[KANAŁ] %s -> %s (id=%s)", channel.name, new_name, channel.id)
            await asyncio.sleep(CHANNEL_DELETE_DELAY)
        except discord.Forbidden:
            logging.warning("Brak uprawnień do zmiany nazwy kanału %s", channel.id)
        except discord.HTTPException as e:
            msg = str(e)
            if "Unknown Channel" in msg or "error code: 10003" in msg:
                dead_channel_ids.add(channel.id)
                logging.warning("Kanał %s już nie istnieje - oznaczam jako martwy", channel.id)
            else:
                logging.warning("Nie udało się zmienić nazwy kanału %s: %s", channel.id, e)


async def channel_edit_worker():
    while True:
        priority, channel_id = await channel_edit_priority_queue.get()
        try:
            if channel_id in dead_channel_ids:
                continue

            new_name = channel_last_desired_name.pop(channel_id, None)
            if not new_name:
                continue

            channel = bot.get_channel(channel_id)
            if channel is None:
                dead_channel_ids.add(channel_id)
                continue

            await _apply_channel_name_edit(channel, new_name)
        except Exception as e:
            logging.warning("[QUEUE] Błąd workera: %s", e)
        finally:
            channel_edit_priority_queue.task_done()


async def ensure_channel_edit_worker_running():
    global _channel_edit_worker_task
    if _channel_edit_worker_task is None or _channel_edit_worker_task.done():
        _channel_edit_worker_task = asyncio.create_task(channel_edit_worker())


async def flush_channel_edit_queue(timeout: float = 8.0):
    try:
        await asyncio.wait_for(channel_edit_priority_queue.join(), timeout=timeout)
    except Exception:
        pass


# =================================
# TWORZENIE KATEGORII I KANAŁÓW
# =================================


async def create_or_get_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    for category in guild.categories:
        if category.name == name:
            return category

    category = await guild.create_category(name)
    logging.info("[SETUP] Utworzono kategorię %s na serwerze %s", name, guild.name)
    await asyncio.sleep(CHANNEL_CREATE_DELAY)
    return category


async def create_or_get_voice_channel(category: discord.CategoryChannel, name: str) -> discord.VoiceChannel:
    existing = find_voice_channel_in_category_by_name(category, name)
    if existing:
        return existing

    channel = await category.create_voice_channel(name)
    logging.info("[SETUP] Utworzono kanał %s w kategorii %s", name, category.name)
    await asyncio.sleep(CHANNEL_CREATE_DELAY)
    return channel


async def setup_categories_and_channels(guild: discord.Guild):
    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    lang = get_lang_code(cfg)

    weather_category = guild.get_channel(cfg.get("weather_category_id")) if cfg.get("weather_category_id") else None
    clock_category = guild.get_channel(cfg.get("clock_category_id")) if cfg.get("clock_category_id") else None
    stats_category = guild.get_channel(cfg.get("stats_category_id")) if cfg.get("stats_category_id") else None
    allergy_category = guild.get_channel(cfg.get("allergy_category_id")) if cfg.get("allergy_category_id") else None

    if not isinstance(weather_category, discord.CategoryChannel):
        weather_category = await create_or_get_category(guild, get_category_name(lang, "weather"))
        cfg["weather_category_id"] = weather_category.id

    if not isinstance(clock_category, discord.CategoryChannel):
        clock_category = await create_or_get_category(guild, get_category_name(lang, "clock"))
        cfg["clock_category_id"] = clock_category.id

    if not isinstance(stats_category, discord.CategoryChannel):
        stats_category = await create_or_get_category(guild, get_category_name(lang, "stats"))
        cfg["stats_category_id"] = stats_category.id

    if not isinstance(allergy_category, discord.CategoryChannel):
        allergy_category = await create_or_get_category(guild, get_category_name(lang, "allergy"))
        cfg["allergy_category_id"] = allergy_category.id

    category_map = {
        "weather": weather_category,
        "clock": clock_category,
        "stats": stats_category,
        "allergy": allergy_category,
    }

    channels = dict(cfg.get("channels", {}))
    create_semaphore = asyncio.Semaphore(4)

    async def resolve_channel(key: str, group_name: str):
        target_category = category_map[group_name]
        fallback_name = get_channel_fallback_name(lang, key)

        current_channel = None
        channel_id = channels.get(key)
        if channel_id:
            current_channel = guild.get_channel(channel_id)

        if current_channel is None:
            current_channel = find_voice_channel_in_category_by_name(target_category, fallback_name)

        if current_channel is None:
            async with create_semaphore:
                current_channel = await create_or_get_voice_channel(target_category, fallback_name)

        return key, current_channel.id

    results = await asyncio.gather(
        *(resolve_channel(key, group_name) for key, (group_name, _) in CHANNEL_TEMPLATE_KEYS.items())
    )

    for key, channel_id in results:
        channels[key] = channel_id

    cfg["channels"] = channels
    save_guild_config(guild.id, cfg)
    return cfg


# =================================
# POGODA
# =================================


async def fetch_json(url: str):
    if bot.http_session is None or bot.http_session.closed:
        timeout = aiohttp.ClientTimeout(total=20)
        bot.http_session = aiohttp.ClientSession(timeout=timeout)

    async with bot.http_session.get(url, headers={"User-Agent": "KosmicznyZegar/25"}) as response:
        text = await response.text()
        lowered = text.lower()

        if text.startswith("<!DOCTYPE") or "<html" in lowered:
            raise RuntimeError("API returned HTML instead of JSON")

        return json.loads(text)


async def geocode_city(city_query: str, count: int = 10):
    city_query = city_query.strip()
    if not city_query:
        return []

    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={quote(city_query)}&count={count}&language=pl&format=json"
    )

    data = await fetch_json(url)
    results = data.get("results", []) or []

    parsed = []
    for item in results:
        parsed.append(
            {
                "name": item.get("name"),
                "country": item.get("country", "Unknown country"),
                "admin1": item.get("admin1"),
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
                "timezone": item.get("timezone") or DEFAULT_TIMEZONE,
            }
        )

    return parsed


def air_quality_text(eaqi, lang: str):
    if eaqi is None:
        return "⚪ Brak danych" if lang == "pl" else "⚪ No data"

    value = float(eaqi)

    if value <= 20:
        return "🟢 Powietrze bardzo dobre" if lang == "pl" else "🟢 Air quality very good"
    if value <= 40:
        return "🟡 Powietrze dobre" if lang == "pl" else "🟡 Air quality good"
    if value <= 60:
        return "🟠 Powietrze umiarkowane" if lang == "pl" else "🟠 Air quality moderate"
    if value <= 80:
        return "🔴 Powietrze dostateczne" if lang == "pl" else "🔴 Air quality fair"
    if value <= 100:
        return "🟣 Powietrze złe" if lang == "pl" else "🟣 Air quality bad"

    return "⚫ Powietrze bardzo złe" if lang == "pl" else "⚫ Air quality very bad"


def build_pollen_channel_text(alder, birch, grass, mugwort, ragweed, lang: str) -> str:
    labels_pl = [
        ("Olsza", alder),
        ("Brzoza", birch),
        ("Trawy", grass),
        ("Bylica", mugwort),
        ("Ambrozja", ragweed),
    ]
    labels_en = [
        ("Alder", alder),
        ("Birch", birch),
        ("Grass", grass),
        ("Mugwort", mugwort),
        ("Ragweed", ragweed),
    ]

    labels = labels_pl if lang == "pl" else labels_en
    active = [(name, float(value or 0)) for name, value in labels if float(value or 0) > 0]

    if not active:
        return trim_channel_name(f"🌿 {tr(lang, 'field_pollen')} {tr(lang, 'none')}")

    active.sort(key=lambda x: x[1], reverse=True)
    top = [f"{name} {int(value)}" for name, value in active[:3]]

    return trim_channel_name(f"🌿 {tr(lang, 'field_pollen')} " + " • ".join(top))




def _pollen_entries(alder, birch, grass, mugwort, ragweed, lang: str):
    labels_pl = [
        ("Olsza", alder),
        ("Brzoza", birch),
        ("Trawy", grass),
        ("Bylica", mugwort),
        ("Ambrozja", ragweed),
    ]
    labels_en = [
        ("Alder", alder),
        ("Birch", birch),
        ("Grass", grass),
        ("Mugwort", mugwort),
        ("Ragweed", ragweed),
    ]
    labels = labels_pl if lang == "pl" else labels_en
    return [(name, float(value or 0)) for name, value in labels]


def pollen_level_data(value: float, lang: str):
    v = float(value or 0)
    if v <= 0:
        return {"rank": 0, "emoji": "⚪", "short": "brak" if lang == "pl" else "none", "label": "Brak" if lang == "pl" else "None"}
    if v < 1:
        return {"rank": 1, "emoji": "🟢", "short": "niskie" if lang == "pl" else "low", "label": "Niskie" if lang == "pl" else "Low"}
    if v < 10:
        return {"rank": 2, "emoji": "🟡", "short": "umiarkowane" if lang == "pl" else "moderate", "label": "Umiarkowane" if lang == "pl" else "Moderate"}
    if v < 50:
        return {"rank": 3, "emoji": "🟠", "short": "wysokie" if lang == "pl" else "high", "label": "Wysokie" if lang == "pl" else "High"}
    return {"rank": 4, "emoji": "🔴", "short": "bardzo-wysokie" if lang == "pl" else "very-high", "label": "Bardzo wysokie" if lang == "pl" else "Very high"}


def build_pollen_details_text(alder, birch, grass, mugwort, ragweed, lang: str) -> str:
    parts = []
    for name, value in _pollen_entries(alder, birch, grass, mugwort, ragweed, lang):
        lvl = pollen_level_data(value, lang)
        parts.append(f"{lvl['emoji']} {name} — {lvl['label']}")
    return "\n".join(parts)


def build_allergy_live_channel_text(alder, birch, grass, mugwort, ragweed, lang: str) -> str:
    active = []
    for name, value in _pollen_entries(alder, birch, grass, mugwort, ragweed, lang):
        lvl = pollen_level_data(value, lang)
        if lvl['rank'] > 0:
            active.append((lvl['rank'], value, f"{name.lower()}-{lvl['short']}"))

    if not active:
        base = "🌿 pylenie-spokojne" if lang == "pl" else "🌿 low-pollen"
        return trim_channel_name(base)

    active.sort(key=lambda x: (x[0], x[1]), reverse=True)
    text = "🌿︱" + "︱".join(item[2] for item in active[:2])
    return trim_channel_name(text)


def build_allergy_alert_channel_text(alder, birch, grass, mugwort, ragweed, lang: str) -> str:
    alerts = []
    for name, value in _pollen_entries(alder, birch, grass, mugwort, ragweed, lang):
        lvl = pollen_level_data(value, lang)
        if lvl['rank'] >= 3:
            alerts.append((lvl['rank'], value, f"{name.lower()}-{lvl['short']}"))

    if not alerts:
        return trim_channel_name("🟢 ALERT brak" if lang == "pl" else "🟢 ALERT none")

    alerts.sort(key=lambda x: (x[0], x[1]), reverse=True)
    prefix = "🚨︱" if lang == "pl" else "🚨︱"
    return trim_channel_name(prefix + "︱".join(item[2] for item in alerts[:2]))


def build_allergy_alerts_text(alder, birch, grass, mugwort, ragweed, lang: str) -> str:
    alerts = []
    for name, value in _pollen_entries(alder, birch, grass, mugwort, ragweed, lang):
        lvl = pollen_level_data(value, lang)
        if lvl['rank'] >= 3:
            alerts.append(f"{lvl['emoji']} {name} — {lvl['label']}")

    if not alerts:
        return "🟢 Brak podwyższonego zagrożenia pyleniem" if lang == "pl" else "🟢 No elevated pollen risk"

    return "\n".join(alerts)


def build_allergy_advice_channel_text(alder, birch, grass, mugwort, ragweed, lang: str) -> str:
    max_rank = 0
    for _name, value in _pollen_entries(alder, birch, grass, mugwort, ragweed, lang):
        max_rank = max(max_rank, pollen_level_data(value, lang)['rank'])

    if lang == 'pl':
        if max_rank >= 4:
            text = '💊 unikaj spacerów-rano zamknij-okna leki'
        elif max_rank == 3:
            text = '💊 uważaj-na-zewnątrz okulary płukanie-nosa'
        elif max_rank == 2:
            text = '💊 obserwuj-objawy miej-leki-przy-sobie'
        elif max_rank == 1:
            text = '💊 lekkie-pylenie standardowa-ostrożność'
        else:
            text = '💊 dziś-spokojnie brak-istotnego-pylenia'
    else:
        if max_rank >= 4:
            text = '💊 avoid-morning-walks close-windows meds'
        elif max_rank == 3:
            text = '💊 be-careful-outside glasses nasal-rinse'
        elif max_rank == 2:
            text = '💊 watch-symptoms keep-meds-nearby'
        elif max_rank == 1:
            text = '💊 light-pollen standard-caution'
        else:
            text = '💊 calm-day no-significant-pollen'

    return trim_channel_name(text)

def build_weather_alerts(current: dict, lang: str) -> str:
    weather_code = int(current.get("weather_code", -1)) if current.get("weather_code") is not None else -1
    gusts = float(current.get("wind_gusts_10m", 0) or 0)
    precipitation = float(current.get("precipitation", 0) or 0)
    snowfall = float(current.get("snowfall", 0) or 0)

    alerts: list[str] = []

    if weather_code in {95, 96, 99}:
        alerts.append("burza" if lang == "pl" else "storm")
    if gusts >= 70:
        alerts.append("silny wiatr" if lang == "pl" else "strong wind")
    if precipitation >= 10:
        alerts.append("ulewa" if lang == "pl" else "heavy rain")
    if snowfall >= 1.0:
        alerts.append("intensywny śnieg" if lang == "pl" else "heavy snow")

    if not alerts:
        return "🟢 ALERT brak" if lang == "pl" else "🟢 ALERT none"

    return trim_channel_name(("🔴 ALERT " if len(alerts) >= 2 else "🟡 ALERT ") + " • ".join(alerts))


def day_length_text(sunrise_str, sunset_str, lang: str):
    try:
        sunrise = datetime.strptime(sunrise_str, "%H:%M")
        sunset = datetime.strptime(sunset_str, "%H:%M")
        diff = sunset - sunrise
        minutes = int(diff.total_seconds() // 60)
        hours = minutes // 60
        mins = minutes % 60
        prefix = "☀️ Dzień" if lang == "pl" else "☀️ Day"
        return f"{prefix} {hours}h {mins}m"
    except Exception:
        return "☀️ Dzień --" if lang == "pl" else "☀️ Day --"


def moon_phase_name(now: datetime, lang: str) -> str:
    diff = now - datetime(2001, 1, 1, tzinfo=now.tzinfo)
    days = diff.total_seconds() / 86400
    lunations = 0.20439731 + (days * 0.03386319269)
    phase_index = int((lunations % 1) * 8 + 0.5) & 7

    pl = {
        0: "🌑 Faza księżyca nów",
        1: "🌒 Faza księżyca sierp przybywający",
        2: "🌓 Faza księżyca pierwsza kwadra",
        3: "🌔 Faza księżyca garb przybywający",
        4: "🌕 Faza księżyca pełnia",
        5: "🌖 Faza księżyca garb ubywający",
        6: "🌗 Faza księżyca ostatnia kwadra",
        7: "🌘 Faza księżyca sierp ubywający",
    }
    en = {
        0: "🌑 Moon phase new moon",
        1: "🌒 Moon phase waxing crescent",
        2: "🌓 Moon phase first quarter",
        3: "🌔 Moon phase waxing gibbous",
        4: "🌕 Moon phase full moon",
        5: "🌖 Moon phase waning gibbous",
        6: "🌗 Moon phase last quarter",
        7: "🌘 Moon phase waning crescent",
    }

    return (pl if lang == "pl" else en).get(phase_index, "🌙 --")


def fallback_part_of_day(hour: int, minute: int, lang: str) -> str:
    total_minutes = hour * 60 + minute

    if 4 * 60 <= total_minutes < 6 * 60:
        return "🌓 Pora dnia świt" if lang == "pl" else "🌓 Part of day dawn"
    if 6 * 60 <= total_minutes < 11 * 60:
        return "🌓 Pora dnia przed południem" if lang == "pl" else "🌓 Part of day morning"
    if 11 * 60 <= total_minutes < 13 * 60:
        return "🌓 Pora dnia południe" if lang == "pl" else "🌓 Part of day noon"
    if 13 * 60 <= total_minutes < 18 * 60:
        return "🌓 Pora dnia po południu" if lang == "pl" else "🌓 Part of day afternoon"
    if 18 * 60 <= total_minutes < 20 * 60:
        return "🌓 Pora dnia zmierzch" if lang == "pl" else "🌓 Part of day dusk"

    return "🌓 Pora dnia noc" if lang == "pl" else "🌓 Part of day night"


def format_part_of_day(
    now: datetime,
    lang: str,
    sunrise_str: str | None = None,
    sunset_str: str | None = None,
) -> str:
    return fallback_part_of_day(now.hour, now.minute, lang)


async def get_weather_data(
    city_name: str,
    latitude: float,
    longitude: float,
    timezone_name: str = DEFAULT_TIMEZONE,
    lang: str = DEFAULT_LANGUAGE,
):
    encoded_timezone = quote(timezone_name)

    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,apparent_temperature,cloud_cover,precipitation,rain,showers,snowfall,weather_code,wind_speed_10m,wind_gusts_10m,pressure_msl,visibility,is_day"
        "&daily=sunrise,sunset"
        f"&timezone={encoded_timezone}"
    )

    air_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={latitude}&longitude={longitude}"
        "&current=european_aqi"
        f"&timezone={encoded_timezone}"
    )

    pollen_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={latitude}&longitude={longitude}"
        "&hourly=alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,ragweed_pollen"
        f"&timezone={encoded_timezone}"
    )

    weather_data, air_data, pollen_data = await asyncio.gather(
        fetch_json(weather_url),
        fetch_json(air_url),
        fetch_json(pollen_url),
    )

    current = weather_data.get("current") or {}
    daily = weather_data.get("daily") or {}
    air_current = air_data.get("current") or {}
    hourly = pollen_data.get("hourly") or {}

    hourly_time = hourly.get("time") or []
    current_time = current.get("time")
    pollen_index = hourly_time.index(current_time) if current_time and current_time in hourly_time else 0

    def pollen_value(name: str):
        values = hourly.get(name) or []
        if 0 <= pollen_index < len(values):
            return values[pollen_index]
        return 0

    alder = pollen_value("alder_pollen")
    birch = pollen_value("birch_pollen")
    grass = pollen_value("grass_pollen")
    mugwort = pollen_value("mugwort_pollen")
    ragweed = pollen_value("ragweed_pollen")

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    clouds = current.get("cloud_cover")
    wind = current.get("wind_speed_10m")
    pressure = current.get("pressure_msl")

    sunrise_raw_list = daily.get("sunrise") or []
    sunset_raw_list = daily.get("sunset") or []

    sunrise_raw = sunrise_raw_list[0] if sunrise_raw_list else None
    sunset_raw = sunset_raw_list[0] if sunset_raw_list else None

    sunrise_time = sunrise_raw[11:16] if isinstance(sunrise_raw, str) and len(sunrise_raw) >= 16 else "--:--"
    sunset_time = sunset_raw[11:16] if isinstance(sunset_raw, str) and len(sunset_raw) >= 16 else "--:--"

    precipitation = float(current.get("precipitation", 0) or 0)
    rainfall = float(current.get("rain", 0) or 0) + float(current.get("showers", 0) or 0)
    snowfall = float(current.get("snowfall", 0) or 0)

    if precipitation <= 0 and rainfall <= 0 and snowfall <= 0:
        rain_text = "🌧 Opady brak" if lang == "pl" else "🌧 Precipitation none"
    else:
        rain_text = (
            f"🌧 Opady deszcz {round(rainfall, 1)} mm"
            if lang == "pl"
            else f"🌧 Precipitation rain {round(rainfall, 1)} mm"
        )
        if snowfall > 0:
            rain_text += (
                f" / śnieg {round(snowfall, 1)} cm"
                if lang == "pl"
                else f" / snow {round(snowfall, 1)} cm"
            )

    return {
        "temperature": f"🌡 {city_name.upper()} {round(float(temp))}°C" if temp is not None else f"🌡 {city_name.upper()} --°C",
        "feels": f"🥵 {tr(lang, 'field_feels')} {round(float(feels))}°C" if feels is not None else f"🥵 {tr(lang, 'field_feels')} --°C",
        "clouds": f"☁ {tr(lang, 'field_clouds')} {round(float(clouds))}%" if clouds is not None else f"☁ {tr(lang, 'field_clouds')} --%",
        "air": air_quality_text(air_current.get("european_aqi"), lang),
        "pollen": build_pollen_channel_text(alder, birch, grass, mugwort, ragweed, lang),
        "pollen_details": build_pollen_details_text(alder, birch, grass, mugwort, ragweed, lang),
        "allergy_live": build_allergy_live_channel_text(alder, birch, grass, mugwort, ragweed, lang),
        "allergy_alert": build_allergy_alert_channel_text(alder, birch, grass, mugwort, ragweed, lang),
        "allergy_advice": build_allergy_advice_channel_text(alder, birch, grass, mugwort, ragweed, lang),
        "allergy_alerts_text": build_allergy_alerts_text(alder, birch, grass, mugwort, ragweed, lang),
        "rain": trim_channel_name(rain_text),
        "wind": f"💨 {tr(lang, 'field_wind')} {round(float(wind))} km/h" if wind is not None else f"💨 {tr(lang, 'field_wind')} -- km/h",
        "pressure": f"⏱ {tr(lang, 'field_pressure')} {round(float(pressure))} hPa" if pressure is not None else f"⏱ {tr(lang, 'field_pressure')} -- hPa",
        "alerts": build_weather_alerts(current, lang),
        "sunrise": f"🌅 {tr(lang, 'field_sunrise')} {sunrise_time}",
        "sunset": f"🌇 {tr(lang, 'field_sunset')} {sunset_time}",
        "sunrise_time": sunrise_time,
        "sunset_time": sunset_time,
        "day_length": day_length_text(sunrise_time, sunset_time, lang),
        "alerts_list": [],
    }


async def get_weather_data_for_guild(guild: discord.Guild, cfg: dict, *, force: bool = False) -> dict:
    if not force and guild.id in weather_cache and guild.id in weather_cache_fetched_at:
        age = (datetime.now(UTC) - weather_cache_fetched_at[guild.id]).total_seconds()
        if age < WEATHER_API_MIN_INTERVAL_SECONDS:
            return weather_cache[guild.id]

    weather = await get_weather_data(
        cfg["city_name"],
        cfg["latitude"],
        cfg["longitude"],
        cfg.get("timezone", DEFAULT_TIMEZONE),
        get_lang_code(cfg),
    )

    weather_cache[guild.id] = weather
    weather_cache_fetched_at[guild.id] = datetime.now(UTC)
    return weather


# =================================
# AKTUALIZACJA KANAŁÓW
# =================================


async def update_weather_channels(guild: discord.Guild, cfg: dict, weather: dict):
    weather_names = build_channel_snapshot(
        {
            key: weather.get(key, get_channel_fallback_name(get_lang_code(cfg), key))
            for key in [
                "temperature", "feels", "clouds", "air", "pollen", "rain", "wind", "pressure", "alerts",
                "allergy_live", "allergy_alert", "allergy_advice"
            ]
        }
    )

    if last_weather_snapshot.get(guild.id) == weather_names and channel_snapshot_is_applied(guild, cfg, weather_names):
        logging.info("[POGODA] Brak zmian dla serwera %s - pomijam edycję kanałów", guild.name)
        return

    last_weather_snapshot[guild.id] = dict(weather_names)

    for key, new_name in weather_names.items():
        await queue_channel_edit_priority(get_channel_from_config(guild, cfg, key), new_name, PRIORITY_WEATHER)


async def update_clock_channels(guild: discord.Guild, cfg: dict, weather: dict | None = None):
    lang = get_lang_code(cfg)
    timezone_obj = get_timezone_object(cfg.get("timezone", DEFAULT_TIMEZONE))
    now = datetime.now(timezone_obj)
    weekdays = LANGUAGES[lang]["weekday_short"]

    cached_weather = weather or weather_cache.get(guild.id, {})
    sunrise_time = cached_weather.get("sunrise_time")
    sunset_time = cached_weather.get("sunset_time")
    sunrise_label = cached_weather.get("sunrise", f"🌅 {tr(lang, 'field_sunrise')} --:--")
    sunset_label = cached_weather.get("sunset", f"🌇 {tr(lang, 'field_sunset')} --:--")
    day_length_label = cached_weather.get("day_length", "☀️ Dzień --" if lang == "pl" else "☀️ Day --")

    clock_names = build_channel_snapshot(
        {
            "date": f"{tr(lang, 'ch_date')} {weekdays[now.weekday()]} {now.strftime('%d.%m.%Y')}",
            "part_of_day": format_part_of_day(now, lang, sunrise_time, sunset_time),
            "sunrise": sunrise_label,
            "sunset": sunset_label,
            "day_length": day_length_label,
            "moon": moon_phase_name(now, lang),
        }
    )

    if last_clock_snapshot.get(guild.id) == clock_names and channel_snapshot_is_applied(guild, cfg, clock_names):
        logging.info("[ZEGAR] Brak zmian dla serwera %s - pomijam edycję kanałów", guild.name)
        return

    last_clock_snapshot[guild.id] = dict(clock_names)

    for key, new_name in clock_names.items():
        await queue_channel_edit_priority(get_channel_from_config(guild, cfg, key), new_name, PRIORITY_CLOCK)


async def ensure_guild_members_cached(guild: discord.Guild):
    try:
        if not guild.chunked:
            await guild.chunk(cache=True)
    except Exception as e:
        logging.warning("Nie udało się dochunkować członków dla serwera %s: %s", guild.id, e)


async def update_stats_channels(guild: discord.Guild, cfg: dict):
    await ensure_guild_members_cached(guild)

    lang = get_lang_code(cfg)
    members = list(guild.members)
    human_members = [m for m in members if not m.bot]
    bot_members = [m for m in members if m.bot]

    members_count = guild.member_count or len(members)
    humans_count = len(human_members)
    bots_count = len(bot_members)
    online_count = sum(1 for m in members if m.status in {discord.Status.online, discord.Status.idle, discord.Status.dnd})
    vc_count = sum(1 for m in members if m.voice and m.voice.channel)

    timezone_obj = get_timezone_object(cfg.get("timezone", DEFAULT_TIMEZONE))
    today = datetime.now(timezone_obj).date()
    joined_today_count = sum(
        1 for m in human_members if m.joined_at and m.joined_at.astimezone(timezone_obj).date() == today
    )

    try:
        bans_count = 0
        async for _ in guild.bans(limit=None):
            bans_count += 1
    except Exception:
        bans_count = 0

    updates = [
        ("members", tr(lang, "stats_members", count=members_count)),
        ("humans", tr(lang, "stats_humans", count=humans_count)),
        ("online", tr(lang, "stats_online", count=online_count)),
        ("bots", tr(lang, "stats_bots", count=bots_count)),
        ("vc", tr(lang, "stats_vc", count=vc_count)),
        ("joined_today", tr(lang, "stats_joined_today", count=joined_today_count)),
        ("bans", tr(lang, "stats_bans", count=bans_count)),
    ]

    for key, new_name in updates:
        channel = get_channel_from_config(guild, cfg, key)

        if key == "online":
            now = datetime.now(UTC)
            last_online = last_online_channel_update_at.get(guild.id)
            if last_online is not None and (now - last_online).total_seconds() < ONLINE_CHANNEL_MIN_UPDATE_SECONDS:
                continue
            last_online_channel_update_at[guild.id] = now

        await queue_channel_edit_priority(channel, new_name, PRIORITY_STATS)


# =================================
# REFRESH
# =================================


async def refresh_existing_panel(
    guild: discord.Guild,
    *,
    force_weather: bool = False,
    refresh_clock: bool = True,
    refresh_stats: bool = True,
) -> bool:
    cfg = get_guild_config(guild.id)
    if not cfg or not cfg.get("channels"):
        return False

    weather = await get_weather_data_for_guild(guild, cfg, force=force_weather)
    await update_weather_channels(guild, cfg, weather)

    if refresh_clock:
        await update_clock_channels(guild, cfg, weather)

    if refresh_stats:
        await update_stats_channels(guild, cfg)

    return True


async def schedule_background_refresh(
    guild: discord.Guild,
    *,
    force_full: bool = False,
    force_weather: bool = False,
    refresh_clock: bool = True,
    refresh_stats: bool = True,
):
    existing = background_refresh_tasks.get(guild.id)
    if existing and not existing.done():
        return

    async def runner():
        try:
            if force_full:
                await refresh_existing_panel(
                    guild,
                    force_weather=force_weather or force_full,
                    refresh_clock=refresh_clock,
                    refresh_stats=refresh_stats,
                )
            else:
                cfg = get_guild_config(guild.id)
                if not cfg or not cfg.get("channels"):
                    return

                if refresh_stats:
                    await update_stats_channels(guild, cfg)
                await refresh_status_panel(guild)
                await refresh_status_panel(guild)

                if refresh_clock:
                    await update_clock_channels(guild, cfg)

                if force_weather:
                    weather = await get_weather_data_for_guild(guild, cfg, force=True)
                    await update_weather_channels(guild, cfg, weather)

            await flush_channel_edit_queue(timeout=8.0)
            await refresh_status_panel(guild)
        except Exception as e:
            logging.warning("Błąd refresh dla serwera %s: %s", guild.id, e)
        finally:
            background_refresh_tasks.pop(guild.id, None)

    background_refresh_tasks[guild.id] = asyncio.create_task(runner())


# =================================
# KOMENDY
# =================================



async def status_role_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = current.lower().strip()
    choices = []
    for key in STATUS_ROLE_IDS:
        display = ROLE_DISPLAY_NAMES.get(key, key)
        if not current or current in key.lower() or current in display.lower():
            choices.append(app_commands.Choice(name=build_role_label(key)[:100], value=key))
    return choices[:25]


async def mood_role_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = current.lower().strip()
    choices = []
    for key in MOOD_ROLE_IDS:
        display = ROLE_DISPLAY_NAMES.get(key, key)
        if not current or current in key.lower() or current in display.lower():
            choices.append(app_commands.Choice(name=build_role_label(key)[:100], value=key))
    return choices[:25]


async def activity_role_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = current.lower().strip()
    choices = []
    for key in ACTIVITY_ROLE_IDS:
        display = ROLE_DISPLAY_NAMES.get(key, key)
        if not current or current in key.lower() or current in display.lower():
            choices.append(app_commands.Choice(name=build_role_label(key)[:100], value=key))
    return choices[:25]


async def city_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    static_choices = [
        app_commands.Choice(name="Warszawa, Polska", value="Warszawa"),
        app_commands.Choice(name="Rzeszów, Polska", value="Rzeszów"),
        app_commands.Choice(name="Kraków, Polska", value="Kraków"),
        app_commands.Choice(name="Wrocław, Polska", value="Wrocław"),
        app_commands.Choice(name="Poznań, Polska", value="Poznań"),
        app_commands.Choice(name="Gdańsk, Polska", value="Gdańsk"),
    ]

    if not current.strip():
        return static_choices[:25]

    lowered = current.lower()
    filtered = [c for c in static_choices if lowered in c.name.lower() or lowered in c.value.lower()]

    if len(current.strip()) < 2:
        return filtered[:25] or static_choices[:25]

    try:
        results = await geocode_city(current, count=5)
        dynamic = []

        for item in results[:25]:
            label = item["name"] or "Unknown city"
            if item.get("admin1"):
                label += f", {item['admin1']}"
            if item.get("country"):
                label += f", {item['country']}"

            dynamic.append(app_commands.Choice(name=label[:100], value=item["name"] or current))

        combined = filtered[:]
        existing_values = {c.value for c in combined}

        for choice in dynamic:
            if choice.value not in existing_values:
                combined.append(choice)
                existing_values.add(choice.value)

        return combined[:25]
    except Exception:
        return filtered[:25] or static_choices[:25]


@bot.tree.command(name="setup", description="Tworzy kategorie i kanały bota")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_command(interaction: discord.Interaction):
    await maybe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    lang = get_lang_code(cfg)

    try:
        await setup_categories_and_channels(guild)
        await schedule_background_refresh(guild, force_full=True, force_weather=True)
        await interaction.followup.send(tr(lang, "setup_ok"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(tr(lang, "setup_error", error=e), ephemeral=True)


@bot.tree.command(name="refresh", description="Odświeża wszystkie kanały bota")
@app_commands.checks.has_permissions(manage_guild=True)
async def refresh_command(interaction: discord.Interaction):
    await maybe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    lang = get_lang_code(cfg)

    try:
        if not cfg.get("channels"):
            await interaction.followup.send(tr(lang, "refresh_no_config"), ephemeral=True)
            return

        await schedule_background_refresh(guild, force_full=True, force_weather=True)
        await interaction.followup.send(tr(lang, "refresh_ok"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(tr(lang, "refresh_error", error=e), ephemeral=True)


@bot.tree.command(name="status", description="Pokazuje status konfiguracji bota")
async def status_command(interaction: discord.Interaction):
    await maybe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await send_interaction_message(interaction, tr(DEFAULT_LANGUAGE, "no_config"), ephemeral=True)
        return

    lang = get_lang_code(cfg)

    embed = discord.Embed(title=tr(lang, "status_title"), color=discord.Color.blue())
    embed.add_field(name=tr(lang, "status_weather_cat"), value=str(cfg.get("weather_category_id")), inline=False)
    embed.add_field(name=tr(lang, "status_clock_cat"), value=str(cfg.get("clock_category_id")), inline=False)
    embed.add_field(name=tr(lang, "status_stats_cat"), value=str(cfg.get("stats_category_id")), inline=False)
    embed.add_field(name=tr(lang, "status_allergy_cat"), value=str(cfg.get("allergy_category_id")), inline=False)
    embed.add_field(name=tr(lang, "status_saved_channels"), value=str(len(cfg.get("channels", {}))), inline=False)
    embed.add_field(
        name=tr(lang, "status_city"),
        value=f"{cfg.get('city_name', DEFAULT_CITY_NAME)}, {cfg.get('country', DEFAULT_COUNTRY)}",
        inline=False,
    )
    embed.add_field(name=tr(lang, "status_lat"), value=str(cfg.get("latitude", DEFAULT_LATITUDE)), inline=True)
    embed.add_field(name=tr(lang, "status_lon"), value=str(cfg.get("longitude", DEFAULT_LONGITUDE)), inline=True)
    embed.add_field(name=tr(lang, "status_timezone"), value=str(cfg.get("timezone", DEFAULT_TIMEZONE)), inline=False)
    embed.add_field(name=tr(lang, "status_language"), value=tr(lang, "lang_name"), inline=False)

    await send_interaction_message(interaction, embed=embed, ephemeral=True)


@bot.tree.command(name="pogoda", description="Pokazuje aktualną pogodę")
async def weather_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        guild = interaction.guild
        cfg = get_guild_config(guild.id) if guild else None

        lang = get_lang_code(cfg)
        city_name = cfg["city_name"] if cfg else DEFAULT_CITY_NAME
        latitude = cfg["latitude"] if cfg else DEFAULT_LATITUDE
        longitude = cfg["longitude"] if cfg else DEFAULT_LONGITUDE
        country = cfg["country"] if cfg else DEFAULT_COUNTRY
        timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE

        weather = await get_weather_data(city_name, latitude, longitude, timezone_name, lang)

        if guild:
            weather_cache[guild.id] = weather
            weather_cache_fetched_at[guild.id] = datetime.now(UTC)

        embed = discord.Embed(
            title=tr(lang, "weather_title", city=city_name, country=country),
            color=discord.Color.teal(),
        )
        embed.add_field(name=tr(lang, "field_temperature"), value=weather["temperature"], inline=False)
        embed.add_field(name=tr(lang, "field_feels"), value=weather["feels"], inline=False)
        embed.add_field(name=tr(lang, "field_clouds"), value=weather["clouds"], inline=False)
        embed.add_field(name=tr(lang, "field_air"), value=weather["air"], inline=False)
        embed.add_field(name=tr(lang, "field_pollen"), value=weather.get("pollen_details", weather["pollen"]), inline=False)
        embed.add_field(name=tr(lang, "field_allergy_alert"), value=weather.get("allergy_alerts_text", tr(lang, "none")), inline=False)
        embed.add_field(name=tr(lang, "field_allergy_advice"), value=weather.get("allergy_advice", tr(lang, "none")), inline=False)
        embed.add_field(name=tr(lang, "field_rain"), value=weather["rain"], inline=False)
        embed.add_field(name=tr(lang, "field_wind"), value=weather["wind"], inline=False)
        embed.add_field(name=tr(lang, "field_pressure"), value=weather["pressure"], inline=False)
        embed.add_field(name=tr(lang, "field_alerts"), value=weather["alerts"], inline=False)
        embed.add_field(name=tr(lang, "field_sunrise"), value=weather["sunrise"], inline=False)
        embed.add_field(name=tr(lang, "field_sunset"), value=weather["sunset"], inline=False)
        embed.add_field(name=tr(lang, "field_day_length"), value=weather["day_length"], inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ {e}", ephemeral=True)


@bot.tree.command(name="czas", description="Pokazuje aktualny czas")
async def time_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None

    lang = get_lang_code(cfg)
    timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
    city_name = cfg["city_name"] if cfg else DEFAULT_CITY_NAME

    timezone_obj = get_timezone_object(timezone_name)
    now = datetime.now(timezone_obj)

    embed = discord.Embed(title=tr(lang, "time_title"), color=discord.Color.orange())
    embed.add_field(name=tr(lang, "time_city"), value=city_name, inline=False)
    embed.add_field(name=tr(lang, "time_clock"), value=now.strftime("%H:%M:%S"), inline=False)
    embed.add_field(name=tr(lang, "time_date"), value=now.strftime("%d.%m.%Y"), inline=False)
    embed.add_field(name=tr(lang, "time_part_of_day"), value=format_part_of_day(now, lang), inline=False)
    embed.add_field(name=tr(lang, "time_timezone"), value=timezone_name, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="miasto", description="Ustawia miasto dla pogody i zegara na tym serwerze")
@app_commands.describe(nazwa="Miasto, np. Warszawa, Rzeszów, Kraków")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.autocomplete(nazwa=city_autocomplete)
async def city_command(interaction: discord.Interaction, nazwa: str):
    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await send_interaction_message(interaction, tr(DEFAULT_LANGUAGE, "city_setup_first"), ephemeral=True)
        return

    lang = get_lang_code(cfg)
    await interaction.response.defer(ephemeral=True)

    try:
        results = await geocode_city(nazwa, count=5)
        if not results:
            await interaction.followup.send(tr(lang, "city_not_found", city=nazwa), ephemeral=True)
            return

        city = results[0]
        cfg["city_name"] = city["name"] or nazwa
        cfg["latitude"] = city["latitude"]
        cfg["longitude"] = city["longitude"]
        cfg["country"] = city.get("country") or DEFAULT_COUNTRY
        cfg["timezone"] = city.get("timezone") or DEFAULT_TIMEZONE

        save_guild_config(guild.id, cfg)

        weather_cache.pop(guild.id, None)
        weather_cache_fetched_at.pop(guild.id, None)

        await schedule_background_refresh(guild, force_full=True, force_weather=True)

        extra = f", {city['admin1']}" if city.get("admin1") else ""
        await interaction.followup.send(
            tr(lang, "city_updated", city=f"{city['name']}{extra}, {city['country']}"),
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(tr(lang, "city_error", error=e), ephemeral=True)


@bot.tree.command(name="language", description="Zmienia język bota na tym serwerze")
@app_commands.describe(code="Kod języka: pl lub en")
@app_commands.checks.has_permissions(manage_guild=True)
async def language_command(interaction: discord.Interaction, code: str):
    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)

    code = code.lower().strip()
    if code not in LANGUAGES:
        await send_interaction_message(interaction, tr(get_lang_code(cfg), "language_invalid"), ephemeral=True)
        return

    cfg["language"] = code
    save_guild_config(guild.id, cfg)

    weather_cache.pop(guild.id, None)
    weather_cache_fetched_at.pop(guild.id, None)

    await maybe_defer(interaction, ephemeral=True)
    await schedule_background_refresh(guild, force_full=True, force_weather=True)
    await interaction.followup.send(tr(code, "language_set"), ephemeral=True)


async def delete_category_if_exists(guild: discord.Guild, category_id: int | None):
    if not category_id:
        return
    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return

    channels_to_delete = list(category.channels)
    for ch in channels_to_delete:
        try:
            await ch.delete(reason="Usunięcie konfiguracji bota")
            await asyncio.sleep(CHANNEL_DELETE_DELAY)
        except Exception as e:
            logging.warning("Nie udało się usunąć kanału %s: %s", getattr(ch, "id", None), e)

    try:
        await category.delete(reason="Usunięcie konfiguracji bota")
    except Exception as e:
        logging.warning("Nie udało się usunąć kategorii %s: %s", category.id, e)


@bot.tree.command(name="usun_wszystko", description="Usuwa kategorie bota i czyści konfigurację")
@app_commands.checks.has_permissions(manage_guild=True)
async def usun_wszystko_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, "❌ Tylko na serwerze.", ephemeral=True)
        return

    await maybe_defer(interaction, ephemeral=True)
    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.followup.send("ℹ️ Brak konfiguracji do usunięcia.", ephemeral=True)
        return

    try:
        await delete_category_if_exists(guild, cfg.get("weather_category_id"))
        await delete_category_if_exists(guild, cfg.get("clock_category_id"))
        await delete_category_if_exists(guild, cfg.get("stats_category_id"))
        await delete_category_if_exists(guild, cfg.get("allergy_category_id"))

        cfg = build_default_guild_config(guild.id)
        save_guild_config(guild.id, cfg)
        save_status_panel_reference(guild.id, None, None)
        weather_cache.pop(guild.id, None)
        weather_cache_fetched_at.pop(guild.id, None)
        last_weather_snapshot.pop(guild.id, None)
        last_clock_snapshot.pop(guild.id, None)

        await interaction.followup.send("✅ Usunąłem kategorie bota i wyczyściłem konfigurację. Możesz zrobić `/setup` od nowa.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Nie udało się usunąć wszystkiego: {e}", ephemeral=True)


@bot.tree.command(name="napraw_id", description="Naprawia zapisane ID kanałów w bazie")
@app_commands.checks.has_permissions(manage_guild=True)
async def napraw_id_command(interaction: discord.Interaction):
    await maybe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg or not cfg.get("channels"):
        await send_interaction_message(
            interaction,
            "ℹ️ Brak konfiguracji kanałów. Najpierw użyj /setup.",
            ephemeral=True,
        )
        return

    repaired = 0
    checked = 0

    for key in CHANNEL_TEMPLATE_KEYS.keys():
        checked += 1

        before_cfg = get_guild_config(guild.id) or {}
        before_id = before_cfg.get("channels", {}).get(key)

        channel = get_channel_from_config(guild, before_cfg, key)

        after_cfg = get_guild_config(guild.id) or {}
        after_id = after_cfg.get("channels", {}).get(key)

        if channel is not None and before_id != after_id:
            repaired += 1

    await interaction.followup.send(
        f"✅ Auto-naprawa zakończona. Sprawdzono {checked} wpisów, naprawiono {repaired} ID kanałów.",
        ephemeral=True,
    )



@bot.tree.command(name="statusy", description="Otwiera prywatne okienko do ustawiania statusów")
async def statusy_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, "❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    await send_interaction_message(
        interaction,
        "✨ Tutaj ustawisz swój status, nastrój i aktywność.",
        ephemeral=True,
        view=PrivateStatusView(),
    )


@bot.tree.command(name="panel_statusow", description="Tworzy lub odświeża publiczny panel statusów")
@app_commands.checks.has_permissions(manage_guild=True)
async def panel_statusow_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, "❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    await maybe_defer(interaction, ephemeral=True)

    channel: discord.TextChannel | None = None
    if isinstance(interaction.channel, discord.TextChannel):
        channel = interaction.channel
    elif guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        channel = guild.system_channel

    if channel is None:
        await interaction.followup.send(
            "❌ Użyj tej komendy na kanale tekstowym, gdzie bot może wysyłać wiadomości.",
            ephemeral=True,
        )
        return

    existing_channel_id = cfg.get("status_panel_channel_id")
    existing_message_id = cfg.get("status_panel_message_id")

    if existing_channel_id and existing_message_id:
        ok = await refresh_status_panel(guild, force=True)
        if ok:
            await interaction.followup.send("✅ Panel statusów został odświeżony.", ephemeral=True)
            return

    message = await channel.send(embed=build_status_panel_embed(guild), view=PublicStatusPanelLauncherView())
    cfg["status_panel_channel_id"] = channel.id
    cfg["status_panel_message_id"] = message.id
    save_guild_config(guild.id, cfg)
    save_status_panel_reference(guild.id, channel.id, message.id)

    await interaction.followup.send("✅ Panel statusów został utworzony.", ephemeral=True)


@bot.tree.command(name="pokaz_statusy", description="Pokazuje aktualne liczniki ról statusowych")
async def pokaz_statusy_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await send_interaction_message(interaction, "❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    await send_interaction_message(interaction, ephemeral=True, embed=build_status_panel_embed(guild))


@bot.tree.command(name="ustaw_status_swoj", description="Ustaw swój status szybkim wyborem")
@app_commands.autocomplete(status=status_role_autocomplete, nastroj=mood_role_autocomplete, aktywnosc=activity_role_autocomplete)
@app_commands.describe(
    status="Klucz statusu, np. dostepny",
    nastroj="Klucz nastroju, np. na_luzie",
    aktywnosc="Klucz aktywności, np. gram",
)
async def ustaw_status_swoj_command(
    interaction: discord.Interaction,
    status: str | None = None,
    nastroj: str | None = None,
    aktywnosc: str | None = None,
):
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        await send_interaction_message(interaction, "❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    updates = []
    errors = []

    if status:
        if status not in STATUS_ROLE_IDS:
            errors.append(f"Nieznany status: `{status}`")
        else:
            try:
                await set_member_status_role(member, status)
                updates.append(ROLE_DISPLAY_NAMES.get(status, status))
            except Exception as e:
                errors.append(str(e))

    if nastroj:
        if nastroj not in MOOD_ROLE_IDS:
            errors.append(f"Nieznany nastrój: `{nastroj}`")
        else:
            try:
                await set_member_status_role(member, nastroj)
                updates.append(ROLE_DISPLAY_NAMES.get(nastroj, nastroj))
            except Exception as e:
                errors.append(str(e))

    if aktywnosc:
        if aktywnosc not in ACTIVITY_ROLE_IDS:
            errors.append(f"Nieznana aktywność: `{aktywnosc}`")
        else:
            try:
                await set_member_status_role(member, aktywnosc)
                updates.append(ROLE_DISPLAY_NAMES.get(aktywnosc, aktywnosc))
            except Exception as e:
                errors.append(str(e))

    if not updates and not errors:
        await send_interaction_message(
            interaction,
            "ℹ️ Podaj przynajmniej jeden argument albo użyj `/statusy` dla wygodnego okienka.",
            ephemeral=True,
        )
        return

    msg = []
    if updates:
        msg.append("✅ Ustawiono: " + ", ".join(f"**{item}**" for item in updates))
    if errors:
        msg.append("❌ " + " | ".join(errors))
    await send_interaction_message(interaction, "\n".join(msg), ephemeral=True)
# =================================
# EVENTY I TASKI
# =================================


def schedule_stats_refresh(guild: discord.Guild):
    if guild.id in stats_update_tasks and not stats_update_tasks[guild.id].done():
        return

    async def delayed_refresh():
        try:
            await asyncio.sleep(STATUS_ROLE_DEBOUNCE_SECONDS)
            cfg = get_guild_config(guild.id)
            if cfg and cfg.get("channels"):
                await update_stats_channels(guild, cfg)
        except Exception as e:
            logging.warning("Błąd odświeżania statystyk live dla %s: %s", guild.id, e)
        finally:
            stats_update_tasks.pop(guild.id, None)

    stats_update_tasks[guild.id] = asyncio.create_task(delayed_refresh())


@bot.event
async def on_member_join(member: discord.Member):
    schedule_stats_refresh(member.guild)


@bot.event
async def on_member_remove(member: discord.Member):
    schedule_stats_refresh(member.guild)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    before_ids = {role.id for role in before.roles}
    after_ids = {role.id for role in after.roles}
    all_status_ids = get_all_status_role_ids()

    if (before_ids ^ after_ids) & all_status_ids:
        schedule_stats_refresh(after.guild)
        schedule_status_panel_refresh(after.guild, delay=1.0)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if before.channel != after.channel:
        schedule_stats_refresh(member.guild)


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    if before.status != after.status:
        schedule_stats_refresh(after.guild)


@tasks.loop(minutes=WEATHER_REFRESH_MINUTES)
async def auto_refresh():
    for guild in bot.guilds:
        try:
            cfg = get_guild_config(guild.id)
            if cfg and cfg.get("channels"):
                weather = await get_weather_data_for_guild(guild, cfg, force=False)
                await update_weather_channels(guild, cfg, weather)
        except Exception as e:
            logging.warning("Błąd auto_refresh dla serwera %s: %s", guild.id, e)


@tasks.loop(seconds=CLOCK_REFRESH_SECONDS)
async def auto_refresh_clock_only():
    for guild in bot.guilds:
        try:
            cfg = get_guild_config(guild.id)
            if cfg and cfg.get("channels"):
                await update_clock_channels(guild, cfg)
        except Exception as e:
            logging.warning("Błąd auto_refresh_clock_only dla serwera %s: %s", guild.id, e)


@tasks.loop(seconds=STATS_FALLBACK_REFRESH_SECONDS)
async def auto_refresh_stats_only():
    for guild in bot.guilds:
        try:
            cfg = get_guild_config(guild.id)
            if cfg and cfg.get("channels"):
                await update_stats_channels(guild, cfg)
        except Exception as e:
            logging.warning("Błąd auto_refresh_stats_only dla serwera %s: %s", guild.id, e)


@tasks.loop(seconds=STATUS_CLOCK_REFRESH_SECONDS)
async def update_status_clock():
    global last_presence_text

    timezone_obj = get_timezone_object(DEFAULT_TIMEZONE)
    now = datetime.now(timezone_obj)
    presence_text = f"🕒 {now.strftime('%H:%M')}"

    if last_presence_text == presence_text:
        return

    try:
        await bot.change_presence(activity=discord.CustomActivity(name=presence_text))
        last_presence_text = presence_text
    except Exception as e:
        logging.warning("Błąd update_status_clock: %s", e)


@auto_refresh.before_loop
async def before_auto_refresh():
    await bot.wait_until_ready()


@auto_refresh_clock_only.before_loop
async def before_auto_refresh_clock_only():
    await bot.wait_until_ready()


@auto_refresh_stats_only.before_loop
async def before_auto_refresh_stats_only():
    await bot.wait_until_ready()


@update_status_clock.before_loop
async def before_update_status_clock():
    await bot.wait_until_ready()


async def sync_all_commands():
    try:
        global_synced = await bot.tree.sync()
        logging.info("Globalnie zsynchronizowano %s komend slash", len(global_synced))
    except Exception as e:
        logging.error("Błąd globalnej synchronizacji komend: %s", e)


@bot.event
async def on_ready():
    global _channel_edit_worker_task

    dead_channel_ids.clear()
    channel_last_desired_name.clear()

    if _channel_edit_worker_task is None or _channel_edit_worker_task.done():
        _channel_edit_worker_task = asyncio.create_task(channel_edit_worker())

    try:
        bot.add_view(PublicStatusPanelLauncherView())
    except Exception:
        pass

    logging.info("Zalogowano jako %s (%s)", bot.user, bot.user.id if bot.user else "brak ID")

    for guild in bot.guilds:
        try:
            cfg = get_guild_config(guild.id)
            if cfg and cfg.get("channels"):
                await ensure_guild_members_cached(guild)
                await update_stats_channels(guild, cfg)
        except Exception as e:
            logging.warning(
                "Nie udało się zrobić początkowego odświeżenia statystyk dla %s: %s",
                guild.id,
                e,
            )

    if not auto_refresh.is_running():
        auto_refresh.start()
    if not auto_refresh_clock_only.is_running():
        auto_refresh_clock_only.start()
    if not auto_refresh_stats_only.is_running():
        auto_refresh_stats_only.start()
    if not update_status_clock.is_running():
        update_status_clock.start()

    if not bot.synced_once:
        await sync_all_commands()
        bot.synced_once = True


# =================================
# START
# =================================


def main():
    if not TOKEN:
        raise RuntimeError("Brak DISCORD_TOKEN w zmiennych środowiskowych.")

    init_db()
    logging.info("Start bota. Logi zapisują się także do pliku: %s", LOG_FILE)
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
