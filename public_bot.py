import os
import math
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# =========================
# KONFIGURACJA
# =========================

GUILD_ID = 1479242779138097202  # <-- wpisz ID swojego serwera

# Kanały - PODMIEŃ na swoje prawdziwe ID
CHANNELS = {
    "date": 1473701946656428221,
    "part_of_day": 1479242955630839111,
    "moon_phase": 1479933022508810240,

    "temp": 1479938583199617085,
    "feels_like": 1480202733125505054,
    "precip": 1480293436761309224,
    "wind": 1479950404539256863,
    "pressure": 1480202713575850114,
    "sunrise": 1479942128640462929,
    "sunset": 1479942157518503936,

    "members": 1479241181865971933,
    "online": 1479245135119257630,
    "voice": 1479245305449939085,
}

# Miasto / lokalizacja
CITY_NAME = "Rzeszów"
LATITUDE = 50.0413
LONGITUDE = 21.9990
TIMEZONE = "Europe/Warsaw"

# Opóźnienie między zmianami kanałów
EDIT_DELAY_SECONDS = 2.5

# Logi
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

warsaw_tz = ZoneInfo(TIMEZONE)

# Cache nazw, żeby nie edytować bez potrzeby
last_channel_names = {}


# =========================
# POMOCNICZE
# =========================

def now_warsaw():
    return datetime.now(warsaw_tz)


def get_part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "☀️ | Poranek"
    elif 12 <= hour < 18:
        return "🌞 | Popołudnie"
    elif 18 <= hour < 22:
        return "🌆 | Wieczór"
    else:
        return "🌙 | Noc"


def get_moon_phase(dt: datetime) -> str:
    # Prosta faza księżyca
    year = dt.year
    month = dt.month
    day = dt.day

    if month < 3:
        year -= 1
        month += 12

    month += 1
    c = 365.25 * year
    e = 30.6 * month
    jd = c + e + day - 694039.09
    jd /= 29.5305882
    b = int(jd)
    jd -= b
    phase_index = round(jd * 8)
    if phase_index >= 8:
        phase_index = 0

    phases = {
        0: "🌑 | Nów",
        1: "🌒 | I kwadra",
        2: "🌓 | I kwadra",
        3: "🌔 | Przybywa",
        4: "🌕 | Pełnia",
        5: "🌖 | Ubywa",
        6: "🌗 | III kwadra",
        7: "🌘 | Stary księżyc",
    }
    return phases.get(phase_index, "🌙 | Księżyc")


def format_polish_date(dt: datetime) -> str:
    dni = [
        "pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."
    ]
    day_name = dni[dt.weekday()]
    return f"🗓️ | {day_name} {dt.strftime('%d.%m.%Y')}"


async def safe_edit_channel_name(channel: discord.abc.GuildChannel, new_name: str):
    global last_channel_names

    if channel is None:
        logging.warning("Kanał nie istnieje.")
        return

    current_name = channel.name

    # Nie zmieniaj jeśli taka sama nazwa
    if current_name == new_name:
        logging.info(f"[SKIP] {channel.id}: bez zmian ('{new_name}')")
        last_channel_names[channel.id] = current_name
        return

    # Dodatkowy cache
    if last_channel_names.get(channel.id) == new_name:
        logging.info(f"[CACHE SKIP] {channel.id}: już ustawione ('{new_name}')")
        return

    try:
        await channel.edit(name=new_name)
        last_channel_names[channel.id] = new_name
        logging.info(f"[EDIT] {channel.id}: zmieniono na '{new_name}'")
        await asyncio.sleep(EDIT_DELAY_SECONDS)

    except discord.Forbidden:
        logging.error(f"[ERROR] Brak uprawnień do zmiany kanału {channel.id}")
    except discord.HTTPException as e:
        logging.error(f"[ERROR] HTTPException dla kanału {channel.id}: {e}")
    except Exception as e:
        logging.error(f"[ERROR] Nieznany błąd dla kanału {channel.id}: {e}")


def get_channel(guild: discord.Guild, key: str):
    channel_id = CHANNELS.get(key)
    if not channel_id:
        return None
    return guild.get_channel(channel_id)


# =========================
# POGODA
# =========================

async def fetch_weather():
    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}"
        f"&longitude={LONGITUDE}"
        "&current=temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure"
        f"&daily=sunrise,sunset"
        f"&timezone={TIMEZONE}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(weather_url, timeout=20) as resp:
            data = await resp.json()
            return data


def parse_weather(data: dict):
    current = data.get("current", {})
    daily = data.get("daily", {})

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    precip = current.get("precipitation")
    wind = current.get("wind_speed_10m")
    pressure = current.get("surface_pressure")

    sunrise_list = daily.get("sunrise", [])
    sunset_list = daily.get("sunset", [])

    sunrise = sunrise_list[0] if sunrise_list else None
    sunset = sunset_list[0] if sunset_list else None

    sunrise_text = "--:--"
    sunset_text = "--:--"

    if sunrise:
        sunrise_text = sunrise.split("T")[1][:5]
    if sunset:
        sunset_text = sunset.split("T")[1][:5]

    if precip is None:
        precip_text = "🌧️ | Opady --"
    elif float(precip) <= 0:
        precip_text = "🌤️ | Bez opadów"
    else:
        precip_text = f"🌧️ | Opady {round(float(precip), 1)} mm"

    return {
        "temp": f"🌡️ | {CITY_NAME} {round(float(temp))}°C" if temp is not None else f"🌡️ | {CITY_NAME} --°C",
        "feels_like": f"🥵 | Odczuwalna {round(float(feels))}°C" if feels is not None else "🥵 | Odczuwalna --°C",
        "precip": precip_text,
        "wind": f"💨 | Wiatr {round(float(wind))} km/h" if wind is not None else "💨 | Wiatr -- km/h",
        "pressure": f"⏱️ | Ciśnienie {round(float(pressure))} hPa" if pressure is not None else "⏱️ | Ciśnienie -- hPa",
        "sunrise": f"🌅 | Wschód {sunrise_text}",
        "sunset": f"🌇 | Zachód {sunset_text}",
    }


# =========================
# AKTUALIZACJE KANAŁÓW
# =========================

async def update_time_channels():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logging.warning("Nie znaleziono serwera.")
        return

    dt = now_warsaw()

    updates = {
        "date": format_polish_date(dt),
        "part_of_day": get_part_of_day(dt.hour),
        "moon_phase": get_moon_phase(dt),
    }

    logging.info("[LOOP] Aktualizacja kanałów czasu...")

    for key, new_name in updates.items():
        channel = get_channel(guild, key)
        await safe_edit_channel_name(channel, new_name)

    logging.info("[INFO] Kanały czasu zostały odświeżone.")


async def update_weather_channels():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logging.warning("Nie znaleziono serwera.")
        return

    logging.info("[LOOP] Aktualizacja pogody...")

    try:
        data = await fetch_weather()
        weather = parse_weather(data)

        for key in ["temp", "feels_like", "precip", "wind", "pressure", "sunrise", "sunset"]:
            channel = get_channel(guild, key)
            await safe_edit_channel_name(channel, weather[key])

        logging.info("[INFO] Kanały pogodowe zostały odświeżone.")

    except Exception as e:
        logging.error(f"[ERROR] Błąd podczas pobierania pogody: {e}")


async def update_server_stats():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logging.warning("Nie znaleziono serwera.")
        return

    logging.info("[LOOP] Aktualizacja statystyk serwera...")

    members_count = guild.member_count or 0

    online_count = 0
    voice_count = 0

    for member in guild.members:
        if member.bot:
            continue

        if member.status != discord.Status.offline:
            online_count += 1

        if member.voice and member.voice.channel:
            voice_count += 1

    updates = {
        "members": f"👥 Członkowie • {members_count}",
        "online": f"🟢 Online • {online_count}",
        "voice": f"🎤 Na VC • {voice_count}",
    }

    for key, new_name in updates.items():
        channel = get_channel(guild, key)
        await safe_edit_channel_name(channel, new_name)

    logging.info("[INFO] Statystyki serwera zostały odświeżone.")


# =========================
# PĘTLE
# =========================

@tasks.loop(minutes=10)
async def time_loop():
    await update_time_channels()


@tasks.loop(minutes=15)
async def weather_loop():
    await update_weather_channels()


@tasks.loop(minutes=5)
async def stats_loop():
    await update_server_stats()


@time_loop.before_loop
@weather_loop.before_loop
@stats_loop.before_loop
async def before_loops():
    await bot.wait_until_ready()


# =========================
# EVENTY
# =========================

@bot.event
async def on_ready():
    logging.info(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")

    guild = bot.get_guild(GUILD_ID)
    if guild:
        logging.info(f"Połączono z serwerem: {guild.name} ({guild.id})")
    else:
        logging.warning("Bot nie widzi serwera po GUILD_ID.")

    if not time_loop.is_running():
        time_loop.start()

    if not weather_loop.is_running():
        weather_loop.start()

    if not stats_loop.is_running():
        stats_loop.start()

    # Jednorazowe odświeżenie po starcie
    await update_time_channels()
    await update_weather_channels()
    await update_server_stats()


# =========================
# TESTOWA KOMENDA
# =========================

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")


bot.run(TOKEN)
