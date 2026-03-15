# ================================
# KOSMICZNY ZEGAR 24 - BOT
# ================================

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import sqlite3
import json
import os
import logging
from datetime import datetime
import pytz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("Brakuje DISCORD_TOKEN w zmiennych środowiskowych")

TIMEZONE = pytz.timezone("Europe/Warsaw")

CITY_NAME = "WARSZAWA"
LATITUDE = 52.2297
LONGITUDE = 21.0122

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "bot_data.db"

# ================================
# KANAŁY
# ================================

CHANNEL_TEMPLATES = {

    # POGODA
    "temperature": ("weather", "🌡️ • Temperatura"),
    "feels": ("weather", "🥵 • Odczuwalna"),
    "clouds": ("weather", "☁️ • Zachmurzenie"),
    "air": ("weather", "🟢 • Powietrze"),
    "pollen": ("weather", "🌿 • Pylenie"),
    "rain": ("weather", "🌧️ • Opady"),
    "wind": ("weather", "💨 • Wiatr"),
    "pressure": ("weather", "🧭 • Ciśnienie"),

    # KOSMICZNY ZEGAR
    "date": ("clock", "📅 • Data"),
    "part_of_day": ("clock", "🌤️ • Pora dnia"),
    "sunrise": ("clock", "🌅 • Wschód"),
    "sunset": ("clock", "🌇 • Zachód"),
    "day_length": ("clock", "☀️ • Długość dnia"),
    "moon": ("clock", "🌙 • Faza księżyca"),

    # STATYSTYKI
    "members": ("stats", "👥 • Wszyscy"),
    "online": ("stats", "🟢 • Online"),
    "bots": ("stats", "🤖 • Boty"),
    "vc": ("stats", "🔊 • Na VC"),
}

CATEGORY_NAMES = {
    "weather": "🌤️ Pogoda",
    "clock": "🛰️ Kosmiczny Zegar",
    "stats": "📊 Statystyki",
}

# ================================
# BAZA
# ================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id INTEGER PRIMARY KEY,
        weather_category_id INTEGER,
        clock_category_id INTEGER,
        stats_category_id INTEGER,
        channels_json TEXT
    )
    """)

    conn.commit()
    conn.close()


def get_guild_config(guild_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,))
    row = c.fetchone()

    conn.close()

    if not row:
        return None

    return {
        "guild_id": row[0],
        "weather_category_id": row[1],
        "clock_category_id": row[2],
        "stats_category_id": row[3],
        "channels": json.loads(row[4]) if row[4] else {}
    }


def save_guild_config(guild_id: int, cfg: dict):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    INSERT OR REPLACE INTO guild_config
    VALUES (?, ?, ?, ?, ?)
    """, (
        guild_id,
        cfg.get("weather_category_id"),
        cfg.get("clock_category_id"),
        cfg.get("stats_category_id"),
        json.dumps(cfg.get("channels", {}))
    ))

    conn.commit()
    conn.close()


def get_channel_from_config(guild: discord.Guild, cfg: dict, key: str):
    channel_id = cfg["channels"].get(key)

    if not channel_id:
        return None

    return guild.get_channel(channel_id)

# ================================
# POGODA
# ================================

async def fetch_json(url: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            return await r.json()


def format_part_of_day(hour: int):

    if 5 <= hour < 8:
        return "🌅 • Świt"

    if 8 <= hour < 12:
        return "🌄 • Poranek"

    if 12 <= hour < 17:
        return "☀️ • Dzień"

    if 17 <= hour < 21:
        return "🌆 • Wieczór"

    return "🌙 • Noc"


async def get_weather_data():

    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}"
        f"&longitude={LONGITUDE}"
        "&current=temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure,cloud_cover"
        "&daily=sunrise,sunset"
        "&timezone=Europe%2FWarsaw"
    )

    data = await fetch_json(weather_url)

    current = data.get("current", {})
    daily = data.get("daily", {})

    sunrise = daily.get("sunrise", ["--"])[0]
    sunset = daily.get("sunset", ["--"])[0]

    sunrise = sunrise.split("T")[1][:5] if sunrise != "--" else "--"
    sunset = sunset.split("T")[1][:5] if sunset != "--" else "--"

    return {
        "temperature": f"🌡️ • {CITY_NAME} {round(current.get('temperature_2m',0))}°C",
        "feels": f"🥵 • Odczuwalna {round(current.get('apparent_temperature',0))}°C",
        "clouds": f"☁️ • Zachmurzenie {round(current.get('cloud_cover',0))}%",
        "rain": f"🌧️ • Opady {current.get('precipitation',0)} mm",
        "wind": f"💨 • Wiatr {round(current.get('wind_speed_10m',0))} km/h",
        "pressure": f"🧭 • Ciśnienie {round(current.get('surface_pressure',0))} hPa",
        "sunrise": f"🌅 • Wschód {sunrise}",
        "sunset": f"🌇 • Zachód {sunset}",
    }

# ================================
# KANAŁY
# ================================

async def safe_edit_channel_name(channel, name):

    if not channel:
        return

    if channel.name == name:
        return

    try:
        await channel.edit(name=name)
        await asyncio.sleep(1.2)
    except Exception as e:
        logging.error(e)

# ================================
# UPDATE
# ================================

async def update_weather_channels(guild, cfg):

    weather = await get_weather_data()

    for key in ["temperature","feels","clouds","rain","wind","pressure"]:
        await safe_edit_channel_name(
            get_channel_from_config(guild,cfg,key),
            weather[key]
        )

async def update_clock_channels(guild, cfg):

    weather = await get_weather_data()
    now = datetime.now(TIMEZONE)

    weekdays = ["pon.","wt.","śr.","czw.","pt.","sob.","niedz."]

    await safe_edit_channel_name(
        get_channel_from_config(guild,cfg,"date"),
        f"📅 • {weekdays[now.weekday()]} {now.strftime('%d.%m.%Y')}"
    )

    await safe_edit_channel_name(
        get_channel_from_config(guild,cfg,"part_of_day"),
        format_part_of_day(now.hour)
    )

    await safe_edit_channel_name(
        get_channel_from_config(guild,cfg,"sunrise"),
        weather["sunrise"]
    )

    await safe_edit_channel_name(
        get_channel_from_config(guild,cfg,"sunset"),
        weather["sunset"]
    )

async def update_stats_channels(guild,cfg):

    members = guild.member_count
    bots = len([m for m in guild.members if m.bot])
    online = len([m for m in guild.members if m.status != discord.Status.offline])
    vc = len([m for m in guild.members if m.voice])

    await safe_edit_channel_name(get_channel_from_config(guild,cfg,"members"),f"👥 • Wszyscy {members}")
    await safe_edit_channel_name(get_channel_from_config(guild,cfg,"online"),f"🟢 • Online {online}")
    await safe_edit_channel_name(get_channel_from_config(guild,cfg,"bots"),f"🤖 • Boty {bots}")
    await safe_edit_channel_name(get_channel_from_config(guild,cfg,"vc"),f"🔊 • Na VC {vc}")

# ================================
# REFRESH
# ================================

async def refresh_all(guild):

    cfg = await ensure_categories_and_channels(guild)

    await update_weather_channels(guild,cfg)
    await update_clock_channels(guild,cfg)
    await update_stats_channels(guild,cfg)

@tasks.loop(minutes=10)
async def auto_refresh():

    for guild in bot.guilds:

        try:
            await refresh_all(guild)
        except Exception as e:
            logging.error(e)

@auto_refresh.before_loop
async def before_loop():
    await bot.wait_until_ready()

# ================================
# READY
# ================================

@bot.event
async def on_ready():

    logging.info(f"Zalogowano jako {bot.user}")

    try:
        synced = await bot.tree.sync()
        logging.info(f"Zsynchronizowano {len(synced)} komend")
    except Exception as e:
        logging.error(e)

    if not auto_refresh.is_running():
        auto_refresh.start()

init_db()
bot.run(TOKEN)
