# ================================
# KOSMICZNY ZEGAR PUBLIC - BOT v25 MAX+
# MULTILANGUAGE: PL / EN
# + PANEL STATUSÓW / NASTROJU / AKTYWNOŚCI PRO MAX
# + WSZYSTKIE POPRAWKI STABILNOŚCI + EFEKTY WOW
# ================================
import asyncio
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
import aiohttp
import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks

# ================================
# LOGI
# ================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ================================
# KONFIGURACJA
# ================================
TOKEN = os.getenv("DISCORD_TOKEN_PUBLIC") or os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Brakuje DISCORD_TOKEN_PUBLIC lub DISCORD_TOKEN w zmiennych środowiskowych")

DEFAULT_CITY_NAME = "Warszawa"
DEFAULT_LATITUDE = 52.2297
DEFAULT_LONGITUDE = 21.0122
DEFAULT_COUNTRY = "Polska"
DEFAULT_TIMEZONE = "Europe/Warsaw"
DEFAULT_LANGUAGE = "pl"
WEATHER_REFRESH_MINUTES = 15
CHANNEL_EDIT_DELAY = 1.2
CATEGORY_DELETE_DELAY = 0.5
STATS_DEBOUNCE_SECONDS = 10
MAX_CHANNEL_NAME_LEN = 95

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)
DB_FILE = "bot_data_public.db"
bot_start_time = datetime.now(UTC)
stats_update_tasks: dict[int, asyncio.Task] = {}

# ================================
# TŁUMACZENIA (bez zmian – zostawiłem oryginalne)
# ================================
# (cały słownik LANGUAGES zostawiam bez zmian – jest bardzo długi, więc nie kopiuję go tutaj ponownie,
# ale w pełnym pliku jest dokładnie taki sam jak podałeś)

# ================================
# MAPY KLUCZY KANAŁÓW + NOWY KLUCZ DLA EFEKTU MOON
# ================================
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
}

# ================================
# PANEL STATUSÓW (bez zmian)
# ================================
# (cały kod panelu StatusPanelView + role config zostawiam dokładnie taki sam)

# ================================
# BAZA DANYCH + POMOCNICZE FUNKCJE (bez zmian oprócz trim)
# ================================
# (init_db, get_guild_config, save_guild_config, tr, itd. – identyczne)

def trim_channel_name(text: str) -> str:
    return text[:MAX_CHANNEL_NAME_LEN]

# ================================
# POPRAWIONE FUNKCJE PYLENIE I ALERTY (zawsze trim na końcu)
# ================================
def build_pollen_channel_text(...):  # oryginalna funkcja
    # ... cały kod bez zmian ...
    text = base + joined
    return trim_channel_name(text)   # <--- ZMIANA

def format_alerts_channel(...):
    # ... cały kod ...
    if len(text) <= MAX_CHANNEL_NAME_LEN:
        return text
    # ... trimming ...
    return trim_channel_name(base + " ".join(trimmed))  # <--- ZMIANA

# ================================
# NOWY TASK – RESET LICZNIKA "DZISIAJ WESZŁO"
# ================================
@tasks.loop(hours=1)
async def daily_stats_reset_check():
    for guild in bot.guilds:
        cfg = get_guild_config(guild.id)
        if cfg:
            get_joined_today_count(guild.id, cfg.get("timezone", DEFAULT_TIMEZONE))

@daily_stats_reset_check.before_loop
async def before_daily_reset():
    await bot.wait_until_ready()

# ================================
# POPRAWIONY STATUS ZEGARA (z obsługą rate-limit)
# ================================
@tasks.loop(seconds=5)
async def update_status_clock():
    timezone = pytz.timezone("Europe/Warsaw")
    now = datetime.now(timezone)
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=f"🕒 {now.strftime('%H:%M:%S')}"
    )
    try:
        await bot.change_presence(status=discord.Status.online, activity=activity)
    except discord.HTTPException as e:
        if e.status == 429:
            logging.warning("Rate limit na statusie zegara – czekam 30s")
            await asyncio.sleep(30)
        elif e.status >= 500:
            logging.warning(f"Discord server error {e.status} – czekam 15s")
            await asyncio.sleep(15)
        else:
            logging.error(f"HTTP error przy statusie: {e}")
    except Exception as e:
        logging.error(f"Nieoczekiwany błąd statusu: {e}")

@update_status_clock.before_loop
async def before_update_status_clock():
    await bot.wait_until_ready()

# ================================
# GLOBALNY ERROR HANDLER
# ================================
@bot.event
async def on_error(event, *args, **kwargs):
    import traceback
    logging.error(f"Global error in {event}:\n{traceback.format_exc()}")

# ================================
# GEOCODING Z BEZPIECZNYM FALLBACKIEM
# ================================
async def geocode_city(city_query: str, count: int = 10):
    try:
        city_query = city_query.strip()
        if not city_query:
            return []
        encoded_name = quote(city_query)
        url = (
            "https://geocoding-api.open-meteo.com/v1/search"
            f"?name={encoded_name}&count={count}&language=pl&format=json"
        )
        data = await fetch_json(url)
        # ... reszta parsowania bez zmian ...
        return parsed
    except Exception as e:
        logging.error(f"Geocoding error: {e}")
        return []

# ================================
# UPDATE CLOCK – EFEKT WOW (pełnia/nów z emoji)
# ================================
async def update_clock_channels(guild: discord.Guild, cfg: dict, weather: dict):
    lang = get_lang_code(cfg)
    timezone_obj = get_timezone_object(cfg.get("timezone", DEFAULT_TIMEZONE))
    now = datetime.now(timezone_obj)

    # ... reszta kanałów bez zmian (date, part_of_day, sunrise, sunset, day_length) ...

    moon_text = moon_phase_name(now, lang)
    if "pełnia" in moon_text.lower():
        moon_text = f"🌕🔥 {moon_text}"
    elif "nów" in moon_text.lower():
        moon_text = f"🌑✨ {moon_text}"
    else:
        moon_text = f"🌙 {moon_text}"

    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "moon"),
        moon_text
    )

# ================================
# AUTO REFRESH + reszta zadań (bez zmian oprócz startu nowego taska)
# ================================
@tasks.loop(minutes=WEATHER_REFRESH_MINUTES)
async def auto_refresh():
    # ... oryginalny kod ...
    pass

# ================================
# ON_READY – START WSZYSTKICH TASKÓW
# ================================
@bot.event
async def on_ready():
    logging.info(f"Zalogowano jako {bot.user}")
    try:
        synced = await bot.tree.sync()
        logging.info(f"Zsynchronizowano {len(synced)} komend")
    except Exception as e:
        logging.error(f"Błąd sync: {e}")

    bot.add_view(StatusPanelView())

    if not auto_refresh.is_running():
        auto_refresh.start()
    if not update_status_clock.is_running():
        update_status_clock.start()
    if not daily_stats_reset_check.is_running():
        daily_stats_reset_check.start()

    for guild in bot.guilds:
        await refresh_stats_only(guild)

# ================================
# RESZTA KODU (komendy, setup, delete, panel, eventy) – BEZ ZMIAN
# ================================
# (wszystko od @bot.tree.command aż do końca – dokładnie tak jak podałeś)

# Na końcu pliku:
init_db()
bot.run(TOKEN)
