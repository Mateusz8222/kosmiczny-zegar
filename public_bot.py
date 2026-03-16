# ================================
# KOSMICZNY ZEGAR PUBLIC - BOT v21
# ================================

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
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

WEATHER_REFRESH_MINUTES = 15
CHANNEL_EDIT_DELAY = 0.3
CATEGORY_DELETE_DELAY = 0.5
STATS_DEBOUNCE_SECONDS = 1.5
MAX_CHANNEL_NAME_LEN = 95

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "bot_data_public.db"

CHANNEL_TEMPLATES = {
    # POGODA
    "temperature": ("weather", "🌡 Temperatura"),
    "feels": ("weather", "🥵 Odczuwalna"),
    "clouds": ("weather", "☁ Zachmurzenie"),
    "air": ("weather", "🌫 Powietrze"),
    "pollen": ("weather", "🌿 Pylenie"),
    "rain": ("weather", "🌧 Opady"),
    "wind": ("weather", "💨 Wiatr"),
    "pressure": ("weather", "⏱ Ciśnienie"),
    "alerts": ("weather", "🟢 ALERT brak"),

    # KOSMICZNY ZEGAR
    "date": ("clock", "📅 Data"),
    "part_of_day": ("clock", "🌓 Pora dnia"),
    "sunrise": ("clock", "🌅 Wschód"),
    "sunset": ("clock", "🌇 Zachód"),
    "day_length": ("clock", "☀️ Dzień"),
    "moon": ("clock", "🌙 Faza księżyca"),

    # STATYSTYKI
    "members": ("stats", "👥 Wszyscy"),
    "humans": ("stats", "👤 Ludzie"),
    "online": ("stats", "🟢 Online"),
    "bots": ("stats", "🤖 Boty"),
    "vc": ("stats", "🔊 Na VC"),
    "joined_today": ("stats", "📥 Dzisiaj weszło 0"),
}

CATEGORY_NAMES = {
    "weather": "🌤️ Pogoda",
    "clock": "🛰️ Kosmiczny Zegar",
    "stats": "📊 Statystyki",
}

stats_update_tasks: dict[int, asyncio.Task] = {}

# ================================
# BAZA DANYCH
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
        channels_json TEXT,
        city_name TEXT,
        latitude REAL,
        longitude REAL,
        country TEXT,
        timezone TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS guild_daily_stats (
        guild_id INTEGER PRIMARY KEY,
        stats_date TEXT,
        joined_today_count INTEGER DEFAULT 0
    )
    """)

    c.execute("PRAGMA table_info(guild_config)")
    columns = [row[1] for row in c.fetchall()]

    if "city_name" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN city_name TEXT")
    if "latitude" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN latitude REAL")
    if "longitude" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN longitude REAL")
    if "country" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN country TEXT")
    if "timezone" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN timezone TEXT")

    conn.commit()
    conn.close()


def get_guild_config(guild_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        SELECT
            guild_id,
            weather_category_id,
            clock_category_id,
            stats_category_id,
            channels_json,
            city_name,
            latitude,
            longitude,
            country,
            timezone
        FROM guild_config
        WHERE guild_id=?
    """, (guild_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "guild_id": row[0],
        "weather_category_id": row[1],
        "clock_category_id": row[2],
        "stats_category_id": row[3],
        "channels": json.loads(row[4]) if row[4] else {},
        "city_name": row[5] or DEFAULT_CITY_NAME,
        "latitude": row[6] if row[6] is not None else DEFAULT_LATITUDE,
        "longitude": row[7] if row[7] is not None else DEFAULT_LONGITUDE,
        "country": row[8] or DEFAULT_COUNTRY,
        "timezone": row[9] or DEFAULT_TIMEZONE,
    }


def save_guild_config(guild_id: int, cfg: dict):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    INSERT OR REPLACE INTO guild_config
    (
        guild_id,
        weather_category_id,
        clock_category_id,
        stats_category_id,
        channels_json,
        city_name,
        latitude,
        longitude,
        country,
        timezone
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        guild_id,
        cfg.get("weather_category_id"),
        cfg.get("clock_category_id"),
        cfg.get("stats_category_id"),
        json.dumps(cfg.get("channels", {})),
        cfg.get("city_name", DEFAULT_CITY_NAME),
        cfg.get("latitude", DEFAULT_LATITUDE),
        cfg.get("longitude", DEFAULT_LONGITUDE),
        cfg.get("country", DEFAULT_COUNTRY),
        cfg.get("timezone", DEFAULT_TIMEZONE)
    ))

    conn.commit()
    conn.close()


def get_channel_from_config(guild: discord.Guild, cfg: dict, key: str):
    channel_id = cfg["channels"].get(key)
    if not channel_id:
        logging.warning(f"Brak channel_id w config dla klucza: {key}")
        return None

    channel = guild.get_channel(channel_id)
    if channel is None:
        logging.warning(f"Nie znaleziono kanału w guild dla klucza: {key}, id: {channel_id}")

    return channel


def get_today_string_for_timezone(timezone_name: str) -> str:
    timezone_obj = get_timezone_object(timezone_name)
    return datetime.now(timezone_obj).strftime("%Y-%m-%d")


def get_joined_today_count(guild_id: int, timezone_name: str) -> int:
    today = get_today_string_for_timezone(timezone_name)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        SELECT stats_date, joined_today_count
        FROM guild_daily_stats
        WHERE guild_id=?
    """, (guild_id,))
    row = c.fetchone()

    if not row:
        c.execute("""
            INSERT INTO guild_daily_stats (guild_id, stats_date, joined_today_count)
            VALUES (?, ?, 0)
        """, (guild_id, today))
        conn.commit()
        conn.close()
        return 0

    stats_date, joined_today_count = row

    if stats_date != today:
        c.execute("""
            UPDATE guild_daily_stats
            SET stats_date=?, joined_today_count=0
            WHERE guild_id=?
        """, (today, guild_id))
        conn.commit()
        conn.close()
        return 0

    conn.close()
    return int(joined_today_count or 0)


def increment_joined_today_count(guild_id: int, timezone_name: str) -> int:
    today = get_today_string_for_timezone(timezone_name)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        SELECT stats_date, joined_today_count
        FROM guild_daily_stats
        WHERE guild_id=?
    """, (guild_id,))
    row = c.fetchone()

    if not row:
        new_count = 1
        c.execute("""
            INSERT INTO guild_daily_stats (guild_id, stats_date, joined_today_count)
            VALUES (?, ?, ?)
        """, (guild_id, today, new_count))
        conn.commit()
        conn.close()
        return new_count

    stats_date, joined_today_count = row

    if stats_date != today:
        new_count = 1
        c.execute("""
            UPDATE guild_daily_stats
            SET stats_date=?, joined_today_count=?
            WHERE guild_id=?
        """, (today, new_count, guild_id))
        conn.commit()
        conn.close()
        return new_count

    new_count = int(joined_today_count or 0) + 1
    c.execute("""
        UPDATE guild_daily_stats
        SET joined_today_count=?
        WHERE guild_id=?
    """, (new_count, guild_id))
    conn.commit()
    conn.close()
    return new_count

# ================================
# POMOCNICZE
# ================================

def build_default_guild_config(guild_id: int) -> dict:
    return {
        "guild_id": guild_id,
        "weather_category_id": None,
        "clock_category_id": None,
        "stats_category_id": None,
        "channels": {},
        "city_name": DEFAULT_CITY_NAME,
        "latitude": DEFAULT_LATITUDE,
        "longitude": DEFAULT_LONGITUDE,
        "country": DEFAULT_COUNTRY,
        "timezone": DEFAULT_TIMEZONE,
    }


def get_timezone_object(timezone_name: str):
    try:
        return pytz.timezone(timezone_name)
    except Exception:
        return pytz.timezone(DEFAULT_TIMEZONE)


def find_voice_channel_in_category_by_name(
    category: discord.CategoryChannel | None,
    name: str
):
    if category is None:
        return None

    for channel in category.voice_channels:
        if channel.name == name:
            return channel

    return None


async def fetch_json(url: str):
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            text = await response.text()
            lowered = text.lower()

            if text.startswith("<!DOCTYPE") or "<html" in lowered:
                raise RuntimeError("API zwróciło HTML zamiast JSON (Cloudflare / blokada)")

            try:
                return json.loads(text)
            except Exception as e:
                raise RuntimeError(f"Nie udało się odczytać JSON z API: {e}")


async def geocode_city(city_query: str, count: int = 10):
    city_query = city_query.strip()
    if not city_query:
        return []

    encoded_name = quote(city_query)

    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={encoded_name}"
        f"&count={count}"
        "&language=pl"
        "&format=json"
    )

    data = await fetch_json(url)
    results = data.get("results", [])

    parsed = []
    for item in results:
        parsed.append({
            "name": item.get("name"),
            "country": item.get("country", "Nieznany kraj"),
            "admin1": item.get("admin1"),
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
            "timezone": item.get("timezone", DEFAULT_TIMEZONE),
        })

    return parsed


def trim_channel_name(text: str) -> str:
    return text[:MAX_CHANNEL_NAME_LEN]

# ================================
# POWIETRZE / PYLENIE / OPADY / ALERTY
# ================================

def air_quality_text(eaqi):
    if eaqi is None:
        return "⚪ Powietrze brak danych"

    value = float(eaqi)

    if value <= 20:
        return "🟢 Powietrze bardzo dobre"
    if value <= 40:
        return "🟡 Powietrze dobre"
    if value <= 60:
        return "🟠 Powietrze umiarkowane"
    if value <= 80:
        return "🔴 Powietrze dostateczne"
    if value <= 100:
        return "🟣 Powietrze złe"
    return "⚫ Powietrze bardzo złe"


def pollen_level_name(value: float) -> str:
    if value <= 0:
        return "brak"
    if value <= 10:
        return "niskie"
    if value <= 50:
        return "średnie"
    if value <= 100:
        return "wysokie"
    return "bardzo wysokie"


def build_pollen_channel_text(alder, birch, grass, mugwort, ragweed) -> str:
    pollens = [
        ("Olsza", float(alder or 0)),
        ("Brzoza", float(birch or 0)),
        ("Trawy", float(grass or 0)),
        ("Bylica", float(mugwort or 0)),
        ("Ambrozja", float(ragweed or 0)),
    ]

    active = [(name, value) for name, value in pollens if value > 0]

    if not active:
        return "🌿 Pylenie brak"

    # najpierw najwyższe stężenia
    active.sort(key=lambda x: x[1], reverse=True)

    formatted_items = [
        f"{name} {pollen_level_name(value)}"
        for name, value in active
    ]

    base = "🌿 Pylenie "
    joined = " • ".join(formatted_items)
    text = base + joined

    if len(text) <= MAX_CHANNEL_NAME_LEN:
        return text

    trimmed: list[str] = []
    for item in formatted_items:
        candidate = base + " • ".join(trimmed + [item])
        if len(candidate) <= MAX_CHANNEL_NAME_LEN:
            trimmed.append(item)
        else:
            break

    remaining = len(formatted_items) - len(trimmed)
    if remaining > 0:
        suffix = f" +{remaining}"
        candidate = base + " • ".join(trimmed) + suffix
        if len(candidate) <= MAX_CHANNEL_NAME_LEN:
            return candidate

    return trim_channel_name(base + " • ".join(trimmed))


def format_precipitation_channel(current: dict) -> str:
    weather_code = int(current.get("weather_code", -1)) if current.get("weather_code") is not None else -1
    precipitation = float(current.get("precipitation", 0) or 0)
    rain = float(current.get("rain", 0) or 0)
    showers = float(current.get("showers", 0) or 0)
    snowfall = float(current.get("snowfall", 0) or 0)

    rain_total = rain + showers

    hail_codes = {96, 99}
    snow_codes = {71, 73, 75, 77, 85, 86}
    rain_codes = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}

    has_hail = weather_code in hail_codes
    has_snow = snowfall > 0 or weather_code in snow_codes
    has_rain = rain_total > 0 or (precipitation > 0 and weather_code in rain_codes)

    if not has_rain and not has_snow and not has_hail and precipitation <= 0:
        return "🌧 Opady brak"

    parts = []

    if has_hail:
        parts.append("grad")

    if has_rain:
        parts.append(f"deszcz {round(rain_total, 1)} mm")

    if has_snow:
        parts.append(f"śnieg {round(snowfall, 1)} cm")

    if not parts and precipitation > 0:
        parts.append(f"opad {round(precipitation, 1)} mm")

    text = "Opady " + " / ".join(parts)

    if has_hail and has_rain and has_snow:
        text = f"⛈🌧🌨 {text}"
    elif has_hail and has_rain:
        text = f"⛈🌧 {text}"
    elif has_hail and has_snow:
        text = f"⛈🌨 {text}"
    elif has_rain and has_snow:
        text = f"🌨🌧 {text}"
    elif has_hail:
        text = f"⛈ {text}"
    elif has_snow:
        text = f"🌨 {text}"
    else:
        text = f"🌧 {text}"

    return trim_channel_name(text)


def parse_hhmm_to_today(now: datetime, hhmm: str) -> datetime | None:
    try:
        hour, minute = map(int, hhmm.split(":"))
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return None


def fallback_part_of_day(hour: int, minute: int = 0) -> str:
    total_minutes = hour * 60 + minute

    if 4 * 60 <= total_minutes < 6 * 60:
        return "🌓 Pora dnia świt"
    if 6 * 60 <= total_minutes < 11 * 60:
        return "🌓 Pora dnia przed południem"
    if 11 * 60 <= total_minutes < 13 * 60:
        return "🌓 Pora dnia południe"
    if 13 * 60 <= total_minutes < 18 * 60:
        return "🌓 Pora dnia po południu"
    if 18 * 60 <= total_minutes < 20 * 60:
        return "🌓 Pora dnia zmierzch"
    return "🌓 Pora dnia noc"


def format_part_of_day(now: datetime, sunrise_str: str | None = None, sunset_str: str | None = None) -> str:
    sunrise = parse_hhmm_to_today(now, sunrise_str) if sunrise_str else None
    sunset = parse_hhmm_to_today(now, sunset_str) if sunset_str else None

    if sunrise is None or sunset is None or sunrise >= sunset:
        return fallback_part_of_day(now.hour, now.minute)

    dawn_start = sunrise - timedelta(minutes=45)
    dawn_end = sunrise + timedelta(minutes=30)

    noon_start = now.replace(hour=11, minute=0, second=0, microsecond=0)
    noon_end = now.replace(hour=13, minute=0, second=0, microsecond=0)

    dusk_start = sunset - timedelta(minutes=45)
    dusk_end = sunset + timedelta(minutes=35)

    if now < dawn_start:
        return "🌓 Pora dnia noc"
    if dawn_start <= now < dawn_end:
        return "🌓 Pora dnia świt"
    if dawn_end <= now < noon_start:
        return "🌓 Pora dnia przed południem"
    if noon_start <= now < noon_end:
        return "🌓 Pora dnia południe"
    if noon_end <= now < dusk_start:
        return "🌓 Pora dnia po południu"
    if dusk_start <= now < dusk_end:
        return "🌓 Pora dnia zmierzch"
    return "🌓 Pora dnia noc"


def day_length_text(sunrise_str, sunset_str):
    try:
        sunrise = datetime.strptime(sunrise_str, "%H:%M")
        sunset = datetime.strptime(sunset_str, "%H:%M")

        diff = sunset - sunrise
        minutes = int(diff.total_seconds() // 60)
        hours = minutes // 60
        mins = minutes % 60

        return f"☀️ Dzień {hours}h {mins}m"
    except Exception:
        return "☀️ Dzień --"


def moon_phase_name(now: datetime) -> str:
    year = now.year
    month = now.month
    day = now.day

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
        0: "🌑 Faza księżyca nów",
        1: "🌒 Faza księżyca sierp przybywający",
        2: "🌓 Faza księżyca pierwsza kwadra",
        3: "🌔 Faza księżyca garb przybywający",
        4: "🌕 Faza księżyca pełnia",
        5: "🌖 Faza księżyca garb ubywający",
        6: "🌗 Faza księżyca ostatnia kwadra",
        7: "🌘 Faza księżyca sierp ubywający",
    }

    return phases.get(phase_index, "🌙 Faza księżyca --")


def build_weather_alerts(current: dict) -> dict:
    alerts: list[str] = []
    level = 0

    weather_code = int(current.get("weather_code", -1)) if current.get("weather_code") is not None else -1
    temperature = float(current.get("temperature_2m", 999)) if current.get("temperature_2m") is not None else 999
    precipitation = float(current.get("precipitation", 0) or 0)
    rain = float(current.get("rain", 0) or 0)
    showers = float(current.get("showers", 0) or 0)
    snowfall = float(current.get("snowfall", 0) or 0)
    gusts = float(current.get("wind_gusts_10m", 0) or 0)
    visibility = float(current.get("visibility", 999999) or 999999)

    if weather_code in {45, 48} or visibility <= 1000:
        alerts.append("mgła")
        level = max(level, 1)

    if snowfall > 0 and gusts >= 40:
        alerts.append("zawieje śnieżne")
        level = max(level, 1)

    if weather_code in {56, 57, 66, 67} or (temperature <= 1 and precipitation > 0):
        alerts.append("gołoledź")
        level = max(level, 2)

    if weather_code in {65, 82} or precipitation >= 10 or rain >= 10 or showers >= 10:
        alerts.append("ulewy")
        level = max(level, 2)

    if weather_code in {75, 86} or snowfall >= 1.0:
        alerts.append("intensywne opady śniegu")
        level = max(level, 2)

    if snowfall > 0 and gusts >= 55:
        alerts.append("zamiecie śnieżne")
        level = max(level, 2)

    if gusts >= 70:
        alerts.append("wichury")
        level = max(level, 2)

    if weather_code in {95, 96, 99}:
        alerts.append("burze")
        level = max(level, 3)

    if weather_code in {96, 99}:
        alerts.append("grad")
        level = max(level, 3)

    if gusts >= 118:
        alerts.append("orkan")
        level = max(level, 3)

    unique_alerts: list[str] = []
    for alert in alerts:
        if alert not in unique_alerts:
            unique_alerts.append(alert)

    return {
        "alerts": unique_alerts,
        "level": level
    }


def format_alerts_channel(alerts: list[str], level: int) -> str:
    if not alerts or level == 0:
        return "🟢 ALERT brak"

    formatted_alerts = [f"❗{alert}" for alert in alerts]

    if level == 1:
        base = "🟡 ALERT 1° "
    elif level == 2:
        base = "🟠 ALERT 2° "
    else:
        base = "🔴 ALERT 3° "

    joined = " ".join(formatted_alerts)
    text = base + joined

    if len(text) <= MAX_CHANNEL_NAME_LEN:
        return text

    trimmed: list[str] = []
    for alert in formatted_alerts:
        candidate = base + " ".join(trimmed + [alert])
        if len(candidate) <= MAX_CHANNEL_NAME_LEN:
            trimmed.append(alert)
        else:
            break

    remaining = len(formatted_alerts) - len(trimmed)
    if remaining > 0:
        suffix = f" +{remaining}"
        candidate = base + " ".join(trimmed) + suffix
        if len(candidate) <= MAX_CHANNEL_NAME_LEN:
            return candidate

    return trim_channel_name(base + " ".join(trimmed))

# ================================
# POBIERANIE POGODY
# ================================

async def get_weather_data(city_name: str, latitude: float, longitude: float, timezone_name: str = DEFAULT_TIMEZONE):
    encoded_timezone = quote(timezone_name)

    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}"
        f"&longitude={longitude}"
        "&current=temperature_2m,apparent_temperature,precipitation,rain,showers,snowfall,"
        "wind_speed_10m,wind_gusts_10m,surface_pressure,cloud_cover,weather_code,visibility"
        "&daily=sunrise,sunset"
        f"&timezone={encoded_timezone}"
    )

    air_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={latitude}"
        f"&longitude={longitude}"
        "&current=european_aqi,alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,ragweed_pollen"
        f"&timezone={encoded_timezone}"
    )

    weather_data, air_data = await asyncio.gather(
        fetch_json(weather_url),
        fetch_json(air_url)
    )

    current = weather_data.get("current", {})
    daily = weather_data.get("daily", {})
    air_current = air_data.get("current", {})

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    wind = current.get("wind_speed_10m")
    pressure = current.get("surface_pressure")
    clouds = current.get("cloud_cover")

    alder = air_current.get("alder_pollen")
    birch = air_current.get("birch_pollen")
    grass = air_current.get("grass_pollen")
    mugwort = air_current.get("mugwort_pollen")
    ragweed = air_current.get("ragweed_pollen")

    sunrise_list = daily.get("sunrise", [])
    sunset_list = daily.get("sunset", [])

    sunrise = sunrise_list[0] if sunrise_list else None
    sunset = sunset_list[0] if sunset_list else None

    sunrise_time = sunrise.split("T")[1][:5] if sunrise else "--:--"
    sunset_time = sunset.split("T")[1][:5] if sunset else "--:--"

    alerts_data = build_weather_alerts(current)
    alerts = alerts_data["alerts"]
    alert_level = alerts_data["level"]

    return {
        "temperature": f"🌡 {city_name.upper()} {round(float(temp))}°C" if temp is not None else f"🌡 {city_name.upper()} --°C",
        "feels": f"🥵 Odczuwalna {round(float(feels))}°C" if feels is not None else "🥵 Odczuwalna --°C",
        "clouds": f"☁ Zachmurzenie {round(float(clouds))}%" if clouds is not None else "☁ Zachmurzenie --%",
        "air": air_quality_text(air_current.get("european_aqi")),
        "pollen": build_pollen_channel_text(alder, birch, grass, mugwort, ragweed),
        "rain": format_precipitation_channel(current),
        "wind": f"💨 Wiatr {round(float(wind))} km/h" if wind is not None else "💨 Wiatr -- km/h",
        "pressure": f"⏱ Ciśnienie {round(float(pressure))} hPa" if pressure is not None else "⏱ Ciśnienie -- hPa",
        "alerts": format_alerts_channel(alerts, alert_level),
        "alerts_list": alerts,
        "alert_level": alert_level,
        "sunrise": f"🌅 Wschód {sunrise_time}",
        "sunset": f"🌇 Zachód {sunset_time}",
        "sunrise_time": sunrise_time,
        "sunset_time": sunset_time,
        "day_length": day_length_text(sunrise_time, sunset_time)
    }

# ================================
# KATEGORIE I KANAŁY
# ================================

async def create_or_get_category(guild: discord.Guild, name: str):
    existing = discord.utils.get(guild.categories, name=name)
    if existing:
        return existing

    return await guild.create_category(
        name=name,
        reason="Kosmiczny Zegar: tworzenie kategorii"
    )


async def create_or_get_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str
):
    existing = find_voice_channel_in_category_by_name(category, name)
    if existing:
        return existing

    return await guild.create_voice_channel(
        name=name,
        category=category,
        reason="Kosmiczny Zegar: tworzenie kanału"
    )


async def setup_categories_and_channels(guild: discord.Guild):
    cfg = get_guild_config(guild.id)

    if not cfg:
        cfg = build_default_guild_config(guild.id)

    weather_category = guild.get_channel(cfg.get("weather_category_id")) if cfg.get("weather_category_id") else None
    clock_category = guild.get_channel(cfg.get("clock_category_id")) if cfg.get("clock_category_id") else None
    stats_category = guild.get_channel(cfg.get("stats_category_id")) if cfg.get("stats_category_id") else None

    if not isinstance(weather_category, discord.CategoryChannel):
        weather_category = await create_or_get_category(guild, CATEGORY_NAMES["weather"])
        cfg["weather_category_id"] = weather_category.id

    if not isinstance(clock_category, discord.CategoryChannel):
        clock_category = await create_or_get_category(guild, CATEGORY_NAMES["clock"])
        cfg["clock_category_id"] = clock_category.id

    if not isinstance(stats_category, discord.CategoryChannel):
        stats_category = await create_or_get_category(guild, CATEGORY_NAMES["stats"])
        cfg["stats_category_id"] = stats_category.id

    category_map = {
        "weather": weather_category,
        "clock": clock_category,
        "stats": stats_category
    }

    channels = dict(cfg.get("channels", {}))

    for key, (group_name, fallback_name) in CHANNEL_TEMPLATES.items():
        target_category = category_map[group_name]
        current_channel = None
        channel_id = channels.get(key)

        if channel_id:
            current_channel = guild.get_channel(channel_id)

        if current_channel is None:
            current_channel = find_voice_channel_in_category_by_name(
                target_category,
                fallback_name
            )

        if current_channel is None:
            current_channel = await create_or_get_voice_channel(
                guild,
                target_category,
                fallback_name
            )

        channels[key] = current_channel.id

    cfg["channels"] = channels
    save_guild_config(guild.id, cfg)

    return cfg


async def safe_edit_channel_name(channel: discord.abc.GuildChannel | None, new_name: str):
    if channel is None:
        logging.warning(f"Nie znaleziono kanału do zmiany nazwy na: {new_name}")
        return

    new_name = trim_channel_name(new_name)

    if channel.name == new_name:
        logging.info(f"Bez zmian: {channel.name}")
        return

    try:
        old_name = channel.name
        await channel.edit(
            name=new_name,
            reason="Kosmiczny Zegar: aktualizacja nazwy kanału"
        )
        logging.info(f"Zmieniono kanał: {old_name} -> {new_name}")
        await asyncio.sleep(CHANNEL_EDIT_DELAY)
    except Exception as e:
        logging.error(
            f"Błąd zmiany nazwy kanału {getattr(channel, 'id', 'brak_id')} "
            f"({getattr(channel, 'name', 'brak_nazwy')}): {e}"
        )

# ================================
# AKTUALIZACJA KANAŁÓW
# ================================

async def update_weather_channels(guild: discord.Guild, cfg: dict, weather: dict):
    for key in [
        "temperature",
        "feels",
        "clouds",
        "rain",
        "wind",
        "pressure",
        "air",
        "pollen",
        "alerts",
    ]:
        await safe_edit_channel_name(
            get_channel_from_config(guild, cfg, key),
            weather[key]
        )


async def update_clock_channels(guild: discord.Guild, cfg: dict, weather: dict):
    timezone_obj = get_timezone_object(cfg.get("timezone", DEFAULT_TIMEZONE))
    now = datetime.now(timezone_obj)
    weekdays = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]

    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "date"),
        f"📅 Data {weekdays[now.weekday()]} {now.strftime('%d.%m.%Y')}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "part_of_day"),
        format_part_of_day(now, weather.get("sunrise_time"), weather.get("sunset_time"))
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "sunrise"),
        weather["sunrise"]
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "sunset"),
        weather["sunset"]
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "day_length"),
        weather["day_length"]
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "moon"),
        moon_phase_name(now)
    )


async def update_stats_channels(guild: discord.Guild, cfg: dict):
    members = [m for m in guild.members]
    human_members = [m for m in members if not m.bot]
    bot_members = [m for m in members if m.bot]

    members_count = len(members)
    humans_count = len(human_members)
    bots_count = len(bot_members)

    online_count = len([
        m for m in human_members
        if m.status in (
            discord.Status.online,
            discord.Status.idle,
            discord.Status.dnd
        )
    ])

    vc_count = len([
        m for m in human_members
        if m.voice and m.voice.channel is not None
    ])

    joined_today_count = get_joined_today_count(
        guild.id,
        cfg.get("timezone", DEFAULT_TIMEZONE)
    )

    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "members"),
        f"👥 Wszyscy {members_count}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "humans"),
        f"👤 Ludzie {humans_count}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "online"),
        f"🟢 Online {online_count}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "bots"),
        f"🤖 Boty {bots_count}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "vc"),
        f"🔊 Na VC {vc_count}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "joined_today"),
        f"📥 Dzisiaj weszło {joined_today_count}"
    )


async def refresh_existing_panel(guild: discord.Guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        return False

    weather = await get_weather_data(
        city_name=cfg["city_name"],
        latitude=cfg["latitude"],
        longitude=cfg["longitude"],
        timezone_name=cfg.get("timezone", DEFAULT_TIMEZONE)
    )

    await update_weather_channels(guild, cfg, weather)
    await update_clock_channels(guild, cfg, weather)
    await update_stats_channels(guild, cfg)

    return True


async def refresh_weather_and_clock_only(guild: discord.Guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        return False

    weather = await get_weather_data(
        city_name=cfg["city_name"],
        latitude=cfg["latitude"],
        longitude=cfg["longitude"],
        timezone_name=cfg.get("timezone", DEFAULT_TIMEZONE)
    )

    await update_weather_channels(guild, cfg, weather)
    await update_clock_channels(guild, cfg, weather)

    return True


async def refresh_stats_only(guild: discord.Guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        return

    await update_stats_channels(guild, cfg)

# ================================
# STATUS ZEGARA BOTA
# ================================

@tasks.loop(seconds=1)
async def update_status_clock():
    timezone = pytz.timezone("Europe/Warsaw")
    now = datetime.now(timezone)

    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=f"🕒 {now.strftime('%H:%M:%S')}"
    )

    await bot.change_presence(
        status=discord.Status.online,
        activity=activity
    )


@update_status_clock.before_loop
async def before_update_status_clock():
    await bot.wait_until_ready()

# ================================
# AUTO REFRESH POGODY I ZEGARA
# ================================

@tasks.loop(minutes=WEATHER_REFRESH_MINUTES)
async def auto_refresh():
    for guild in bot.guilds:
        try:
            cfg = get_guild_config(guild.id)
            if not cfg:
                continue

            await refresh_existing_panel(guild)

        except Exception as e:
            logging.error(f"Błąd auto_refresh dla {guild.id}: {e}")


@auto_refresh.before_loop
async def before_auto_refresh():
    await bot.wait_until_ready()

# ================================
# LIVE STATYSTYKI
# ================================

async def _debounced_stats_refresh(guild: discord.Guild):
    try:
        await asyncio.sleep(STATS_DEBOUNCE_SECONDS)
        await refresh_stats_only(guild)
    except Exception as e:
        logging.error(f"Błąd live stats dla {guild.id}: {e}")
    finally:
        stats_update_tasks.pop(guild.id, None)


def schedule_stats_refresh(guild: discord.Guild):
    if guild is None:
        return

    if not get_guild_config(guild.id):
        return

    existing = stats_update_tasks.get(guild.id)
    if existing and not existing.done():
        existing.cancel()

    stats_update_tasks[guild.id] = asyncio.create_task(_debounced_stats_refresh(guild))

# ================================
# AUTOCOMPLETE MIASTA
# ================================

async def city_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    if not current or len(current.strip()) < 2:
        return [
            app_commands.Choice(name="Warszawa, Polska", value="Warszawa"),
            app_commands.Choice(name="Rzeszów, Polska", value="Rzeszów"),
            app_commands.Choice(name="London, Wielka Brytania", value="London"),
            app_commands.Choice(name="New York, USA", value="New York"),
        ]

    try:
        results = await geocode_city(current, count=10)
        choices = []

        for item in results[:25]:
            label = item["name"] or "Nieznane miasto"
            if item.get("admin1"):
                label += f", {item['admin1']}"
            if item.get("country"):
                label += f", {item['country']}"

            value = item["name"] or current

            choices.append(
                app_commands.Choice(
                    name=label[:100],
                    value=value[:100]
                )
            )

        return choices
    except Exception:
        return []

# ================================
# KOMENDY
# ================================

@bot.tree.command(name="setup", description="Tworzy kategorie i kanały bota")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ Tej komendy można użyć tylko na serwerze.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await setup_categories_and_channels(guild)
        await refresh_existing_panel(guild)

        await interaction.followup.send(
            "✅ Utworzono i odświeżono wszystkie kategorie oraz kanały.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Błąd setupu: {e}",
            ephemeral=True
        )


@bot.tree.command(name="refresh", description="Odświeża wszystkie kanały bota")
@app_commands.checks.has_permissions(manage_guild=True)
async def refresh_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ Tej komendy można użyć tylko na serwerze.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        refreshed = await refresh_existing_panel(guild)

        if not refreshed:
            await interaction.followup.send(
                "ℹ️ Brak konfiguracji. Najpierw użyj `/setup`.",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            "✅ Wszystkie kanały zostały odświeżone.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Błąd refreshu: {e}",
            ephemeral=True
        )


@bot.tree.command(name="status", description="Pokazuje status konfiguracji bota")
async def status_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ Tej komendy można użyć tylko na serwerze.",
            ephemeral=True
        )
        return

    cfg = get_guild_config(guild.id)

    if not cfg:
        await interaction.response.send_message(
            "ℹ️ Brak konfiguracji. Użyj `/setup`.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="📊 Status Kosmicznego Zegara",
        color=discord.Color.blue()
    )
    embed.add_field(name="Kategoria Pogoda", value=str(cfg.get("weather_category_id")), inline=False)
    embed.add_field(name="Kategoria Kosmiczny Zegar", value=str(cfg.get("clock_category_id")), inline=False)
    embed.add_field(name="Kategoria Statystyki", value=str(cfg.get("stats_category_id")), inline=False)
    embed.add_field(name="Zapisane kanały", value=str(len(cfg.get("channels", {}))), inline=False)
    embed.add_field(name="Miasto", value=f"{cfg.get('city_name', DEFAULT_CITY_NAME)}, {cfg.get('country', DEFAULT_COUNTRY)}", inline=False)
    embed.add_field(name="Szerokość", value=str(cfg.get("latitude", DEFAULT_LATITUDE)), inline=True)
    embed.add_field(name="Długość", value=str(cfg.get("longitude", DEFAULT_LONGITUDE)), inline=True)
    embed.add_field(name="Strefa czasowa", value=str(cfg.get("timezone", DEFAULT_TIMEZONE)), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="pogoda", description="Pokazuje aktualną pogodę")
async def weather_command(interaction: discord.Interaction):
    try:
        guild = interaction.guild
        cfg = get_guild_config(guild.id) if guild else None

        city_name = cfg["city_name"] if cfg else DEFAULT_CITY_NAME
        latitude = cfg["latitude"] if cfg else DEFAULT_LATITUDE
        longitude = cfg["longitude"] if cfg else DEFAULT_LONGITUDE
        timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
        country = cfg["country"] if cfg else DEFAULT_COUNTRY

        weather = await get_weather_data(
            city_name=city_name,
            latitude=latitude,
            longitude=longitude,
            timezone_name=timezone_name
        )

        embed = discord.Embed(
            title=f"🌤️ Pogoda - {city_name}, {country}",
            color=discord.Color.teal()
        )
        embed.add_field(name="Temperatura", value=weather["temperature"], inline=False)
        embed.add_field(name="Odczuwalna", value=weather["feels"], inline=False)
        embed.add_field(name="Zachmurzenie", value=weather["clouds"], inline=False)
        embed.add_field(name="Powietrze", value=weather["air"], inline=False)
        embed.add_field(name="Pylenie", value=weather["pollen"], inline=False)
        embed.add_field(name="Opady", value=weather["rain"], inline=False)
        embed.add_field(name="Wiatr", value=weather["wind"], inline=False)
        embed.add_field(name="Ciśnienie", value=weather["pressure"], inline=False)
        embed.add_field(name="Alerty", value=", ".join(weather["alerts_list"]) if weather["alerts_list"] else "brak", inline=False)
        embed.add_field(name="Poziom alertu", value=f"{weather['alert_level']}°" if weather["alert_level"] > 0 else "brak", inline=False)
        embed.add_field(name="Wschód", value=weather["sunrise"], inline=False)
        embed.add_field(name="Zachód", value=weather["sunset"], inline=False)
        embed.add_field(name="Długość dnia", value=weather["day_length"], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.response.send_message(
            f"❌ Błąd pobierania pogody: {e}",
            ephemeral=True
        )


@bot.tree.command(name="czas", description="Pokazuje aktualny czas")
async def time_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
    city_name = cfg["city_name"] if cfg else DEFAULT_CITY_NAME

    timezone_obj = get_timezone_object(timezone_name)
    now = datetime.now(timezone_obj)

    sunrise_time = None
    sunset_time = None
    try:
        weather = await get_weather_data(
            city_name=city_name,
            latitude=cfg["latitude"] if cfg else DEFAULT_LATITUDE,
            longitude=cfg["longitude"] if cfg else DEFAULT_LONGITUDE,
            timezone_name=timezone_name
        )
        sunrise_time = weather.get("sunrise_time")
        sunset_time = weather.get("sunset_time")
    except Exception:
        pass

    embed = discord.Embed(
        title="🕐 Aktualny czas",
        color=discord.Color.orange()
    )
    embed.add_field(name="Miasto", value=city_name, inline=False)
    embed.add_field(name="Godzina", value=now.strftime("%H:%M:%S"), inline=False)
    embed.add_field(name="Data", value=now.strftime("%d.%m.%Y"), inline=False)
    embed.add_field(name="Pora dnia", value=format_part_of_day(now, sunrise_time, sunset_time), inline=False)
    embed.add_field(name="Strefa czasowa", value=timezone_name, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ksiezyc", description="Pokazuje aktualną fazę księżyca")
async def moon_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
    timezone_obj = get_timezone_object(timezone_name)
    now = datetime.now(timezone_obj)

    await interaction.response.send_message(
        moon_phase_name(now),
        ephemeral=True
    )


@bot.tree.command(name="miasto", description="Ustawia miasto dla pogody i zegara na tym serwerze")
@app_commands.describe(nazwa="Nazwa miasta, np. Rzeszów, London, Tokyo")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.autocomplete(nazwa=city_autocomplete)
async def city_command(interaction: discord.Interaction, nazwa: str):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ Tej komendy można użyć tylko na serwerze.",
            ephemeral=True
        )
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message(
            "ℹ️ Najpierw użyj `/setup`, aby utworzyć kategorie i kanały.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        results = await geocode_city(nazwa, count=10)

        if not results:
            await interaction.followup.send(
                f"❌ Nie znaleziono miasta: `{nazwa}`",
                ephemeral=True
            )
            return

        city = results[0]

        cfg["city_name"] = city["name"] or DEFAULT_CITY_NAME
        cfg["latitude"] = city["latitude"] if city["latitude"] is not None else DEFAULT_LATITUDE
        cfg["longitude"] = city["longitude"] if city["longitude"] is not None else DEFAULT_LONGITUDE
        cfg["country"] = city.get("country", DEFAULT_COUNTRY)
        cfg["timezone"] = city.get("timezone", DEFAULT_TIMEZONE)

        save_guild_config(guild.id, cfg)

        refreshed = await refresh_weather_and_clock_only(guild)

        if not refreshed:
            await interaction.followup.send(
                "ℹ️ Nie udało się odświeżyć kanałów. Użyj najpierw `/setup`.",
                ephemeral=True
            )
            return

        extra = ""
        if city.get("admin1"):
            extra = f", {city['admin1']}"

        await interaction.followup.send(
            f"✅ Ustawiono miasto: **{city['name']}{extra}, {city['country']}** i zaktualizowano pogodę oraz zegar.",
            ephemeral=True
        )

    except Exception as e:
        await interaction.followup.send(
            f"❌ Błąd ustawiania miasta: {e}",
            ephemeral=True
        )

# ================================
# USUWANIE KATEGORII
# ================================

async def delete_category_with_channels(guild: discord.Guild, category_id: int | None):
    if not category_id:
        return False

    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return False

    for ch in list(category.channels):
        try:
            await ch.delete(reason="Kosmiczny Zegar: usuwanie kategorii")
            await asyncio.sleep(CATEGORY_DELETE_DELAY)
        except Exception as e:
            logging.error(f"Błąd usuwania kanału {getattr(ch, 'id', 'brak_id')}: {e}")

    try:
        await category.delete(reason="Kosmiczny Zegar: usuwanie kategorii")
        return True
    except Exception as e:
        logging.error(f"Błąd usuwania kategorii {category.id}: {e}")
        return False


def remove_channel_keys_by_group(cfg: dict, group_name: str):
    channels = dict(cfg.get("channels", {}))

    keys_to_remove = [
        key for key, (category_key, _) in CHANNEL_TEMPLATES.items()
        if category_key == group_name
    ]

    for key in keys_to_remove:
        channels.pop(key, None)

    cfg["channels"] = channels
    return cfg


@bot.tree.command(name="usun_pogoda", description="Usuwa kategorię Pogoda razem z kanałami")
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_weather_category_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Brak konfiguracji.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    await delete_category_with_channels(guild, cfg.get("weather_category_id"))
    cfg["weather_category_id"] = None
    cfg = remove_channel_keys_by_group(cfg, "weather")
    save_guild_config(guild.id, cfg)

    await interaction.followup.send("✅ Usunięto kategorię Pogoda.", ephemeral=True)


@bot.tree.command(name="usun_kosmiczny_zegar", description="Usuwa kategorię Kosmiczny Zegar razem z kanałami")
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_clock_category_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Brak konfiguracji.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    await delete_category_with_channels(guild, cfg.get("clock_category_id"))
    cfg["clock_category_id"] = None
    cfg = remove_channel_keys_by_group(cfg, "clock")
    save_guild_config(guild.id, cfg)

    await interaction.followup.send("✅ Usunięto kategorię Kosmiczny Zegar.", ephemeral=True)


@bot.tree.command(name="usun_statystyki", description="Usuwa kategorię Statystyki razem z kanałami")
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_stats_category_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Brak konfiguracji.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    await delete_category_with_channels(guild, cfg.get("stats_category_id"))
    cfg["stats_category_id"] = None
    cfg = remove_channel_keys_by_group(cfg, "stats")
    save_guild_config(guild.id, cfg)

    await interaction.followup.send("✅ Usunięto kategorię Statystyki.", ephemeral=True)


@bot.tree.command(name="usun_wszystko", description="Usuwa wszystkie kategorie bota")
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_all_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Brak konfiguracji.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    await delete_category_with_channels(guild, cfg.get("weather_category_id"))
    await delete_category_with_channels(guild, cfg.get("clock_category_id"))
    await delete_category_with_channels(guild, cfg.get("stats_category_id"))

    cfg["weather_category_id"] = None
    cfg["clock_category_id"] = None
    cfg["stats_category_id"] = None
    cfg["channels"] = {}

    save_guild_config(guild.id, cfg)

    await interaction.followup.send("✅ Usunięto wszystkie kategorie bota.", ephemeral=True)

# ================================
# EVENTY LIVE STATYSTYK
# ================================

@bot.event
async def on_member_join(member: discord.Member):
    cfg = get_guild_config(member.guild.id)
    timezone_name = cfg.get("timezone", DEFAULT_TIMEZONE) if cfg else DEFAULT_TIMEZONE

    increment_joined_today_count(member.guild.id, timezone_name)
    schedule_stats_refresh(member.guild)


@bot.event
async def on_member_remove(member: discord.Member):
    schedule_stats_refresh(member.guild)


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    if before.status != after.status:
        schedule_stats_refresh(after.guild)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if before.channel != after.channel:
        schedule_stats_refresh(member.guild)

# ================================
# START BOTA
# ================================

@bot.event
async def on_ready():
    logging.info(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")

    try:
        synced = await bot.tree.sync()
        logging.info(f"Zsynchronizowano {len(synced)} komend")
        for cmd in synced:
            logging.info(f"Komenda aktywna: /{cmd.name}")
    except Exception as e:
        logging.error(f"Błąd synchronizacji komend: {e}")

    if not auto_refresh.is_running():
        auto_refresh.start()

    if not update_status_clock.is_running():
        update_status_clock.start()


init_db()
bot.run(TOKEN)
