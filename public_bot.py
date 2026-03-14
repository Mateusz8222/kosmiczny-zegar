import os
import json
import asyncio
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("PUBLIC_DISCORD_TOKEN") or os.getenv("DISCORD_TOKEN")

DB_FILE = "bot_data.db"
DEFAULT_CITY = "Rzeszów"
DEFAULT_LAT = 50.0413
DEFAULT_LON = 21.9990
DEFAULT_TIMEZONE = "Europe/Warsaw"

EDIT_DELAY_SECONDS = 2.0
HTTP_TIMEOUT_SECONDS = 15
QUICK_REFRESH_DEFAULT_DELAY = 20.0
WEATHER_CACHE_SECONDS = 300
PANEL_REFRESH_MINUTES = 5
STATS_REFRESH_MINUTES = 3
CHANNELS_REFRESH_MINUTES = 10
PRESENCE_REFRESH_MINUTES = 1

CLEAN_OLD_GUILD_COMMANDS_ON_START = False

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

warsaw_tz = ZoneInfo(DEFAULT_TIMEZONE)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True

last_channel_names: dict[int, str] = {}
guild_refresh_locks: dict[int, asyncio.Lock] = {}
guild_refresh_tasks: dict[int, asyncio.Task] = {}
guild_presence_debounce: dict[int, datetime] = {}
weather_cache: dict[int, dict] = {}
http_session: aiohttp.ClientSession | None = None
bot_started_at = datetime.now(warsaw_tz)

CATEGORY_NAMES = {
    "clock": "🛰️ Kosmiczny Zegar",
    "weather": "🌤️ Pogoda",
    "stats": "📊 Statystyki",
}

CHANNEL_TEMPLATES = {
    "date": ("clock", "📅・Data"),
    "part_of_day": ("clock", "🌆・Pora dnia"),
    "moon_phase": ("clock", "🌙・Faza księżyca"),
    "sunrise": ("clock", "🌅・Wschód"),
    "sunset": ("clock", "🌇・Zachód"),
    "day_length": ("clock", "☀️・Długość dnia"),

    "temp": ("weather", "🌡️・Temperatura"),
    "feels_like": ("weather", "🥵・Odczuwalna"),
    "clouds": ("weather", "☁️・Zachmurzenie"),
    "air_quality": ("weather", "🟢・Powietrze"),
    "pollen": ("weather", "🌿・Pylenie"),
    "precip": ("weather", "🌧️・Opady"),
    "wind": ("weather", "💨・Wiatr"),
    "pressure": ("weather", "🧭・Ciśnienie"),

    "all_members": ("stats", "👥・Wszyscy"),
    "users": ("stats", "🙂・Użytkownicy"),
    "bots": ("stats", "🤖・Boty"),
    "online": ("stats", "🟢・Online"),
    "voice": ("stats", "🎤・Na VC"),
}


class KosmicznyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents
        )
        self.synced_once = False
        self.ready_logged = False

    async def setup_hook(self):
        try:
            await get_http_session()
            logging.info("[SETUP_HOOK] Sesja HTTP gotowa")
        except Exception as e:
            logging.error(f"[SETUP_HOOK] Błąd sesji HTTP: {e}")

        try:
            self.add_view(RefreshPanelView())
            logging.info("[SETUP_HOOK] Zarejestrowano persistent view")
        except Exception as e:
            logging.error(f"[SETUP_HOOK] Błąd rejestracji view: {e}")


bot = KosmicznyBot()


def now_warsaw() -> datetime:
    return datetime.now(warsaw_tz)


def uptime_text() -> str:
    delta = now_warsaw() - bot_started_at
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m"


def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    return any(col["name"] == column_name for col in columns)


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS guild_configs (
        guild_id TEXT PRIMARY KEY,
        city_name TEXT NOT NULL DEFAULT 'Rzeszów',
        latitude REAL NOT NULL DEFAULT 50.0413,
        longitude REAL NOT NULL DEFAULT 21.9990,
        timezone TEXT NOT NULL DEFAULT 'Europe/Warsaw',
        clock_category_id INTEGER,
        weather_category_id INTEGER,
        stats_category_id INTEGER,
        channels_json TEXT NOT NULL DEFAULT '{}'
    )
    """)

    if not column_exists(cursor, "guild_configs", "panel_channel_id"):
        cursor.execute("ALTER TABLE guild_configs ADD COLUMN panel_channel_id INTEGER")

    if not column_exists(cursor, "guild_configs", "panel_message_id"):
        cursor.execute("ALTER TABLE guild_configs ADD COLUMN panel_message_id INTEGER")

    conn.commit()
    conn.close()


def get_all_guild_configs() -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM guild_configs")
    rows = cursor.fetchall()

    result = {}
    for row in rows:
        result[row["guild_id"]] = row_to_config(row)

    conn.close()
    return result


def row_to_config(row: sqlite3.Row) -> dict:
    return {
        "city_name": row["city_name"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "timezone": row["timezone"],
        "clock_category_id": row["clock_category_id"],
        "weather_category_id": row["weather_category_id"],
        "stats_category_id": row["stats_category_id"],
        "panel_channel_id": row["panel_channel_id"] if "panel_channel_id" in row.keys() else None,
        "panel_message_id": row["panel_message_id"] if "panel_message_id" in row.keys() else None,
        "channels": json.loads(row["channels_json"]) if row["channels_json"] else {}
    }


def get_guild_config(guild_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM guild_configs WHERE guild_id = ?", (str(guild_id),))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return row_to_config(row)


def save_guild_config(guild_id: int, data: dict):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO guild_configs (
        guild_id,
        city_name,
        latitude,
        longitude,
        timezone,
        clock_category_id,
        weather_category_id,
        stats_category_id,
        channels_json,
        panel_channel_id,
        panel_message_id
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(guild_id) DO UPDATE SET
        city_name = excluded.city_name,
        latitude = excluded.latitude,
        longitude = excluded.longitude,
        timezone = excluded.timezone,
        clock_category_id = excluded.clock_category_id,
        weather_category_id = excluded.weather_category_id,
        stats_category_id = excluded.stats_category_id,
        channels_json = excluded.channels_json,
        panel_channel_id = excluded.panel_channel_id,
        panel_message_id = excluded.panel_message_id
    """, (
        str(guild_id),
        data.get("city_name", DEFAULT_CITY),
        float(data.get("latitude", DEFAULT_LAT)),
        float(data.get("longitude", DEFAULT_LON)),
        data.get("timezone", DEFAULT_TIMEZONE),
        data.get("clock_category_id"),
        data.get("weather_category_id"),
        data.get("stats_category_id"),
        json.dumps(data.get("channels", {}), ensure_ascii=False),
        data.get("panel_channel_id"),
        data.get("panel_message_id"),
    ))

    conn.commit()
    conn.close()


def delete_guild_config(guild_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM guild_configs WHERE guild_id = ?", (str(guild_id),))
    conn.commit()
    conn.close()


def get_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in guild_refresh_locks:
        guild_refresh_locks[guild_id] = asyncio.Lock()
    return guild_refresh_locks[guild_id]


def format_polish_date(dt: datetime) -> str:
    dni = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]
    return f"📅・{dni[dt.weekday()]} {dt.strftime('%d.%m.%Y')}"


def get_part_of_day(hour: int) -> str:
    if 4 <= hour < 6:
        return "🌅・Świt"
    if 6 <= hour < 10:
        return "🌄・Poranek"
    if 10 <= hour < 14:
        return "☀️・Południe"
    if 14 <= hour < 18:
        return "🌤・Popołudnie"
    if 18 <= hour < 22:
        return "🌆・Wieczór"
    return "🌙・Noc"


def get_moon_phase(dt: datetime) -> str:
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
        0: "🌑・Nów",
        1: "🌒・Przybywający sierp",
        2: "🌓・Pierwsza kwadra",
        3: "🌔・Przybywający garb",
        4: "🌕・Pełnia",
        5: "🌖・Ubywający garb",
        6: "🌗・Ostatnia kwadra",
        7: "🌘・Ubywający sierp",
    }
    return phases.get(phase_index, "🌙・Księżyc")


def format_moon_for_command(dt: datetime) -> str:
    return get_moon_phase(dt).replace("・", " ").strip()


def air_quality_channel(eaqi: float | int | None) -> str:
    if eaqi is None:
        return "⚪・Powietrze brak danych"

    value = float(eaqi)

    if value <= 20:
        return "🟢・Powietrze bardzo dobre"
    if value <= 40:
        return "🟢・Powietrze dobre"
    if value <= 60:
        return "🟡・Powietrze umiarkowane"
    if value <= 80:
        return "🟠・Powietrze dostateczne"
    if value <= 100:
        return "🔴・Powietrze złe"
    return "☠️・Powietrze bardzo złe"


def strongest_pollen_name(alder, birch, grass, mugwort, ragweed, olive) -> tuple[str | None, float]:
    pollen = {
        "olcha": float(alder or 0),
        "brzoza": float(birch or 0),
        "trawy": float(grass or 0),
        "bylica": float(mugwort or 0),
        "ambrozja": float(ragweed or 0),
        "oliwka": float(olive or 0),
    }
    name = max(pollen, key=pollen.get)
    return name, pollen[name]


def pollen_channel(alder, birch, grass, mugwort, ragweed, olive) -> str:
    name, level = strongest_pollen_name(alder, birch, grass, mugwort, ragweed, olive)

    if level <= 0:
        return "🌿・Brak pylenia"
    if level <= 10:
        return f"🌼・Pylenie niskie • {name}"
    if level <= 50:
        return f"🤧・Pylenie umiarkowane • {name}"
    if level <= 100:
        return f"🤧・Pylenie wysokie • {name}"
    return f"☠️・Pylenie bardzo wysokie • {name}"


def format_day_length_from_times(sunrise_text: str, sunset_text: str) -> str:
    try:
        sunrise_dt = datetime.strptime(sunrise_text, "%H:%M")
        sunset_dt = datetime.strptime(sunset_text, "%H:%M")
        delta = sunset_dt - sunrise_dt
        total_minutes = int(delta.total_seconds() // 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"☀️・Długość dnia {hours}h {minutes}m"
    except Exception:
        return "☀️・Długość dnia --"


async def get_http_session() -> aiohttp.ClientSession:
    global http_session

    if http_session is None or http_session.closed:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        connector = aiohttp.TCPConnector(limit=20)
        http_session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": "KosmicznyZegarBot/2.1"}
        )

    return http_session


async def safe_edit_channel_name(channel: discord.abc.GuildChannel | None, new_name: str):
    if channel is None:
        return

    current_name = channel.name

    if current_name == new_name:
        last_channel_names[channel.id] = current_name
        logging.info(f"[SKIP] {channel.id}: bez zmian ('{new_name}')")
        return

    if last_channel_names.get(channel.id) == new_name:
        logging.info(f"[CACHE SKIP] {channel.id}: już ustawione ('{new_name}')")
        return

    try:
        await channel.edit(
            name=new_name,
            reason="Kosmiczny Zegar: automatyczna aktualizacja"
        )
        last_channel_names[channel.id] = new_name
        logging.info(f"[EDIT] {channel.id}: '{new_name}'")
        await asyncio.sleep(EDIT_DELAY_SECONDS)

    except discord.Forbidden:
        logging.error(f"[FORBIDDEN] Brak uprawnień do zmiany kanału {channel.id}")
    except discord.HTTPException as e:
        logging.error(f"[HTTP] Kanał {channel.id}: status={getattr(e, 'status', '?')} | {e}")
    except Exception as e:
        logging.error(f"[ERROR] Nieznany błąd dla kanału {channel.id}: {e}")


async def geocode_city(city_name: str):
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={city_name}&count=1&language=pl&format=json"
    )

    session = await get_http_session()

    async with session.get(url) as response:
        response.raise_for_status()
        data = await response.json()

    results = data.get("results", [])
    if not results:
        return None

    result = results[0]
    return {
        "name": result.get("name", city_name),
        "country": result.get("country", ""),
        "latitude": result.get("latitude"),
        "longitude": result.get("longitude"),
        "timezone": result.get("timezone", DEFAULT_TIMEZONE),
    }


async def fetch_weather_raw(latitude: float, longitude: float, timezone_name: str):
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}"
        f"&longitude={longitude}"
        "&current=temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure,cloud_cover"
        "&daily=sunrise,sunset"
        f"&timezone={timezone_name}"
    )

    session = await get_http_session()

    async with session.get(url) as response:
        response.raise_for_status()
        return await response.json()


async def fetch_air_quality_raw(latitude: float, longitude: float, timezone_name: str):
    url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={latitude}"
        f"&longitude={longitude}"
        "&current=european_aqi,alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,ragweed_pollen,olive_pollen"
        f"&timezone={timezone_name}"
    )

    session = await get_http_session()

    async with session.get(url) as response:
        response.raise_for_status()
        return await response.json()


def parse_weather(weather_data: dict, air_data: dict, city_name: str) -> dict:
    current = weather_data.get("current", {})
    daily = weather_data.get("daily", {})
    air_current = air_data.get("current", {})

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    precip = current.get("precipitation")
    wind = current.get("wind_speed_10m")
    pressure = current.get("surface_pressure")
    cloud_cover = current.get("cloud_cover")

    european_aqi = air_current.get("european_aqi")
    alder_pollen = air_current.get("alder_pollen")
    birch_pollen = air_current.get("birch_pollen")
    grass_pollen = air_current.get("grass_pollen")
    mugwort_pollen = air_current.get("mugwort_pollen")
    ragweed_pollen = air_current.get("ragweed_pollen")
    olive_pollen = air_current.get("olive_pollen")

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
        precip_text = "🌧️・Opady --"
    elif float(precip) <= 0:
        precip_text = "🌧️・Brak opadów"
    else:
        precip_text = f"🌧️・Opady {round(float(precip), 1)} mm"

    clouds_text = (
        f"☁️・Zachmurzenie {round(float(cloud_cover))}%"
        if cloud_cover is not None
        else "☁️・Zachmurzenie --%"
    )

    return {
        "temp": f"🌡️・{city_name} {round(float(temp))}°C" if temp is not None else f"🌡️・{city_name} --°C",
        "feels_like": f"🥵・Odczuwalna {round(float(feels))}°C" if feels is not None else "🥵・Odczuwalna --°C",
        "clouds": clouds_text,
        "air_quality": air_quality_channel(european_aqi),
        "pollen": pollen_channel(
            alder_pollen,
            birch_pollen,
            grass_pollen,
            mugwort_pollen,
            ragweed_pollen,
            olive_pollen
        ),
        "precip": precip_text,
        "wind": f"💨・Wiatr {round(float(wind))} km/h" if wind is not None else "💨・Wiatr -- km/h",
        "pressure": f"🧭・Ciśnienie {round(float(pressure))} hPa" if pressure is not None else "🧭・Ciśnienie -- hPa",
        "sunrise": f"🌅・Wschód {sunrise_text}",
        "sunset": f"🌇・Zachód {sunset_text}",
        "day_length": format_day_length_from_times(sunrise_text, sunset_text),
    }


async def get_weather_for_guild(guild_id: int, guild_cfg: dict, use_cache: bool = True) -> dict:
    latitude = float(guild_cfg.get("latitude", DEFAULT_LAT))
    longitude = float(guild_cfg.get("longitude", DEFAULT_LON))
    timezone_name = guild_cfg.get("timezone", DEFAULT_TIMEZONE)
    city_name = guild_cfg.get("city_name", DEFAULT_CITY)

    cached = weather_cache.get(guild_id)
    if use_cache and cached:
        age = (now_warsaw() - cached["time"]).total_seconds()
        if age < WEATHER_CACHE_SECONDS:
            return cached["weather"]

    weather_data, air_data = await asyncio.gather(
        fetch_weather_raw(latitude, longitude, timezone_name),
        fetch_air_quality_raw(latitude, longitude, timezone_name),
    )

    weather = parse_weather(weather_data, air_data, city_name)

    weather_cache[guild_id] = {
        "time": now_warsaw(),
        "weather": weather
    }
    return weather


async def create_or_get_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str
) -> discord.VoiceChannel:
    for channel in category.voice_channels:
        if channel.name == name:
            return channel

    return await guild.create_voice_channel(
        name=name,
        category=category,
        overwrites=category.overwrites,
        reason="Kosmiczny Zegar: tworzenie brakującego kanału"
    )


def get_channel_from_config(guild: discord.Guild, guild_cfg: dict, key: str):
    channel_id = guild_cfg.get("channels", {}).get(key)
    if not channel_id:
        return None
    return guild.get_channel(channel_id)


def get_panel_channel(guild: discord.Guild, guild_cfg: dict):
    panel_channel_id = guild_cfg.get("panel_channel_id")
    if not panel_channel_id:
        return None
    channel = guild.get_channel(panel_channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


async def ensure_required_channels(guild: discord.Guild, guild_cfg: dict) -> dict:
    changed = False
    channels = dict(guild_cfg.get("channels", {}))

    clock_category = guild.get_channel(guild_cfg.get("clock_category_id")) if guild_cfg.get("clock_category_id") else None
    weather_category = guild.get_channel(guild_cfg.get("weather_category_id")) if guild_cfg.get("weather_category_id") else None
    stats_category = guild.get_channel(guild_cfg.get("stats_category_id")) if guild_cfg.get("stats_category_id") else None

    if not isinstance(clock_category, discord.CategoryChannel):
        clock_category = discord.utils.get(guild.categories, name=CATEGORY_NAMES["clock"])
        if clock_category is None:
            clock_category = await guild.create_category(
                CATEGORY_NAMES["clock"],
                reason="Kosmiczny Zegar: odtwarzanie kategorii"
            )
        guild_cfg["clock_category_id"] = clock_category.id
        changed = True

    if not isinstance(weather_category, discord.CategoryChannel):
        weather_category = discord.utils.get(guild.categories, name=CATEGORY_NAMES["weather"])
        if weather_category is None:
            weather_category = await guild.create_category(
                CATEGORY_NAMES["weather"],
                reason="Kosmiczny Zegar: odtwarzanie kategorii"
            )
        guild_cfg["weather_category_id"] = weather_category.id
        changed = True

    if not isinstance(stats_category, discord.CategoryChannel):
        stats_category = discord.utils.get(guild.categories, name=CATEGORY_NAMES["stats"])
        if stats_category is None:
            stats_category = await guild.create_category(
                CATEGORY_NAMES["stats"],
                reason="Kosmiczny Zegar: odtwarzanie kategorii"
            )
        guild_cfg["stats_category_id"] = stats_category.id
        changed = True

    category_map = {
        "clock": clock_category,
        "weather": weather_category,
        "stats": stats_category,
    }

    for key, (category_key, fallback_name) in CHANNEL_TEMPLATES.items():
        channel = get_channel_from_config(guild, guild_cfg, key)
        if channel is None:
            category = category_map[category_key]
            created = await create_or_get_voice_channel(guild, category, fallback_name)
            channels[key] = created.id
            changed = True

    if changed:
        guild_cfg["channels"] = channels
        save_guild_config(guild.id, guild_cfg)

    return guild_cfg


async def create_setup_for_guild(guild: discord.Guild) -> dict:
    existing_cfg = get_guild_config(guild.id) or {}

    clock_category = guild.get_channel(existing_cfg.get("clock_category_id")) if existing_cfg.get("clock_category_id") else None
    weather_category = guild.get_channel(existing_cfg.get("weather_category_id")) if existing_cfg.get("weather_category_id") else None
    stats_category = guild.get_channel(existing_cfg.get("stats_category_id")) if existing_cfg.get("stats_category_id") else None

    if not isinstance(clock_category, discord.CategoryChannel):
        clock_category = discord.utils.get(guild.categories, name=CATEGORY_NAMES["clock"])
        if clock_category is None:
            clock_category = await guild.create_category(
                CATEGORY_NAMES["clock"],
                reason="Kosmiczny Zegar: tworzenie kategorii"
            )

    if not isinstance(weather_category, discord.CategoryChannel):
        weather_category = discord.utils.get(guild.categories, name=CATEGORY_NAMES["weather"])
        if weather_category is None:
            weather_category = await guild.create_category(
                CATEGORY_NAMES["weather"],
                reason="Kosmiczny Zegar: tworzenie kategorii"
            )

    if not isinstance(stats_category, discord.CategoryChannel):
        stats_category = discord.utils.get(guild.categories, name=CATEGORY_NAMES["stats"])
        if stats_category is None:
            stats_category = await guild.create_category(
                CATEGORY_NAMES["stats"],
                reason="Kosmiczny Zegar: tworzenie kategorii"
            )

    channels = {}
    for key, (category_key, fallback_name) in CHANNEL_TEMPLATES.items():
        category = {
            "clock": clock_category,
            "weather": weather_category,
            "stats": stats_category
        }[category_key]
        created = await create_or_get_voice_channel(guild, category, fallback_name)
        channels[key] = created.id

    guild_data = {
        "city_name": existing_cfg.get("city_name", DEFAULT_CITY),
        "latitude": existing_cfg.get("latitude", DEFAULT_LAT),
        "longitude": existing_cfg.get("longitude", DEFAULT_LON),
        "timezone": existing_cfg.get("timezone", DEFAULT_TIMEZONE),
        "clock_category_id": clock_category.id,
        "weather_category_id": weather_category.id,
        "stats_category_id": stats_category.id,
        "panel_channel_id": existing_cfg.get("panel_channel_id"),
        "panel_message_id": existing_cfg.get("panel_message_id"),
        "channels": channels
    }

    save_guild_config(guild.id, guild_data)
    return guild_data


async def update_time_channels_for_guild(guild: discord.Guild, guild_cfg: dict, weather: dict | None = None):
    dt = now_warsaw()

    updates = {
        "date": format_polish_date(dt),
        "part_of_day": get_part_of_day(dt.hour),
        "moon_phase": get_moon_phase(dt),
        "sunrise": None,
        "sunset": None,
        "day_length": None,
    }

    if weather is None:
        try:
            weather = await get_weather_for_guild(guild.id, guild_cfg, use_cache=True)
        except Exception as e:
            logging.error(f"[TIME] Błąd pobierania danych dla {guild.id}: {e}")
            weather = None

    if weather:
        updates["sunrise"] = weather["sunrise"]
        updates["sunset"] = weather["sunset"]
        updates["day_length"] = weather["day_length"]

    for key, new_name in updates.items():
        if new_name is None:
            continue
        channel = get_channel_from_config(guild, guild_cfg, key)
        await safe_edit_channel_name(channel, new_name)


async def update_weather_channels_for_guild(guild: discord.Guild, guild_cfg: dict, weather: dict | None = None):
    try:
        if weather is None:
            weather = await get_weather_for_guild(guild.id, guild_cfg, use_cache=True)

        for key in ["temp", "feels_like", "clouds", "air_quality", "pollen", "precip", "wind", "pressure"]:
            channel = get_channel_from_config(guild, guild_cfg, key)
            await safe_edit_channel_name(channel, weather[key])

    except Exception as e:
        logging.error(f"[WEATHER] Błąd aktualizacji pogody dla serwera {guild.id}: {e}")


async def update_server_stats_for_guild(guild: discord.Guild, guild_cfg: dict):
    all_members_count = guild.member_count or 0
    users_count = 0
    bots_count = 0
    online_count = 0
    voice_count = 0

    for member in guild.members:
        if member.bot:
            bots_count += 1
        else:
            users_count += 1

        if member.status != discord.Status.offline:
            online_count += 1

        if member.voice and member.voice.channel:
            voice_count += 1

    updates = {
        "all_members": f"👥・Wszyscy {all_members_count}",
        "users": f"🙂・Użytkownicy {users_count}",
        "bots": f"🤖・Boty {bots_count}",
        "online": f"🟢・Online {online_count}",
        "voice": f"🎤・Na VC {voice_count}",
    }

    for key, new_name in updates.items():
        channel = get_channel_from_config(guild, guild_cfg, key)
        await safe_edit_channel_name(channel, new_name)


def build_panel_embed(guild: discord.Guild, guild_cfg: dict):
    embed = discord.Embed(
        title="🛰️ Kosmiczny Zegar — Panel PRO",
        description="Panel konfiguracji, statusu i szybkiego odświeżania.",
        color=discord.Color.blurple()
    )
    embed.add_field(name="📍 Miasto", value=guild_cfg.get("city_name", DEFAULT_CITY), inline=True)
    embed.add_field(name="🕒 Strefa", value=guild_cfg.get("timezone", DEFAULT_TIMEZONE), inline=True)
    embed.add_field(name="📡 Serwery bota", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="🧩 Kanały", value=str(len(guild_cfg.get("channels", {}))), inline=True)
    embed.add_field(name="👥 Global users", value=str(sum(g.member_count or 0 for g in bot.guilds)), inline=True)
    embed.add_field(name="⏱ Uptime", value=uptime_text(), inline=True)

    panel_channel = get_panel_channel(guild, guild_cfg)
    embed.add_field(
        name="📝 Stały panel",
        value=panel_channel.mention if panel_channel else "Nie ustawiono",
        inline=False
    )

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    embed.set_footer(text=f"Serwer: {guild.name}")
    return embed


def build_help_embed():
    embed = discord.Embed(
        title="📘 Kosmiczny Zegar — Pomoc PRO",
        description="Lista komend bota.",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="⚙️ Konfiguracja",
        value=(
            "`/setup` — tworzy kategorie i kanały\n"
            "`/miasto` — ustawia miasto\n"
            "`/refresh` — pełne odświeżenie\n"
            "`/status` — status konfiguracji\n"
            "`/panel` — podgląd panelu\n"
            "`/ustaw_panel` — zapisuje stały panel\n"
            "`/odswiez_panel` — odświeża stały panel\n"
            "`/usun_setup` — usuwa setup"
        ),
        inline=False
    )
    embed.add_field(
        name="🌍 Informacje",
        value=(
            "`/pogoda` — aktualna pogoda\n"
            "`/czas` — aktualny czas\n"
            "`/ksiezyc` — faza księżyca"
        ),
        inline=False
    )
    embed.add_field(
        name="🤖 Bot",
        value=(
            "`/ping` — sprawdzenie działania\n"
            "`/botstats` — statystyki bota\n"
            "`/invite` — link zaproszenia\n"
            "`/help` — pomoc"
        ),
        inline=False
    )
    return embed


def build_weather_embed(guild_cfg: dict, weather: dict):
    embed = discord.Embed(
        title="🌤 Pogoda",
        description=f"Miasto: **{guild_cfg.get('city_name', DEFAULT_CITY)}**",
        color=discord.Color.teal()
    )
    embed.add_field(name="Temperatura", value=weather["temp"], inline=False)
    embed.add_field(name="Odczuwalna", value=weather["feels_like"], inline=False)
    embed.add_field(name="Zachmurzenie", value=weather["clouds"], inline=False)
    embed.add_field(name="Powietrze", value=weather["air_quality"], inline=False)
    embed.add_field(name="Pylenie", value=weather["pollen"], inline=False)
    embed.add_field(name="Opady", value=weather["precip"], inline=False)
    embed.add_field(name="Wiatr", value=weather["wind"], inline=False)
    embed.add_field(name="Ciśnienie", value=weather["pressure"], inline=False)
    embed.add_field(name="Wschód", value=weather["sunrise"], inline=False)
    embed.add_field(name="Zachód", value=weather["sunset"], inline=False)
    embed.add_field(name="Długość dnia", value=weather["day_length"], inline=False)
    return embed


def build_botstats_embed():
    servers = len(bot.guilds)
    users = sum(g.member_count or 0 for g in bot.guilds)

    embed = discord.Embed(
        title="📊 Statystyki bota",
        color=discord.Color.blue()
    )
    embed.add_field(name="🌍 Serwery", value=str(servers), inline=False)
    embed.add_field(name="👥 Łącznie użytkowników", value=str(users), inline=False)
    embed.add_field(name="🤖 Bot", value=str(bot.user), inline=False)
    embed.add_field(name="⏱ Uptime", value=uptime_text(), inline=False)

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    return embed


def build_invite_embed():
    link = "https://discord.com/oauth2/authorize?client_id=1481070169077055548&permissions=2147568640&scope=bot%20applications.commands"
    support = "https://discord.gg/FqhhUrfc"

    embed = discord.Embed(
        title="➕ Dodaj Kosmiczny Zegar",
        description=f"[Kliknij tutaj, aby dodać bota]({link})",
        color=discord.Color.blurple()
    )
    embed.add_field(name="📨 Serwer wsparcia", value=f"[Dołącz tutaj]({support})", inline=False)
    return embed


class RefreshPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Odśwież teraz",
        emoji="🔄",
        style=discord.ButtonStyle.blurple,
        custom_id="kosmiczny_refresh_button"
    )
    async def refresh_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Tej akcji można użyć tylko na serwerze.", ephemeral=True)
            return

        cfg = get_guild_config(guild.id)
        if not cfg:
            await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await update_one_guild(guild)
            await refresh_saved_panel(guild)
            await interaction.followup.send("✅ Kanały i panel zostały odświeżone.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Błąd odświeżania: {e}", ephemeral=True)


async def send_or_update_panel_message(guild: discord.Guild, target_channel: discord.TextChannel | None = None):
    cfg = get_guild_config(guild.id)
    if not cfg:
        return False, "Brak konfiguracji. Użyj `/setup`."

    embed = build_panel_embed(guild, cfg)
    view = RefreshPanelView()

    panel_channel = target_channel or get_panel_channel(guild, cfg)
    if panel_channel is None:
        return False, "Nie ustawiono kanału panelu."

    message_id = cfg.get("panel_message_id")

    try:
        if message_id:
            try:
                msg = await panel_channel.fetch_message(message_id)
                await msg.edit(embed=embed, view=view)
                return True, "Panel został zaktualizowany."
            except discord.NotFound:
                cfg["panel_message_id"] = None
                save_guild_config(guild.id, cfg)
            except discord.Forbidden:
                return False, "Bot nie ma dostępu do wiadomości panelu."
            except discord.HTTPException as e:
                return False, f"Błąd odświeżania wiadomości panelu: {e}"

        new_msg = await panel_channel.send(embed=embed, view=view)
        cfg["panel_channel_id"] = panel_channel.id
        cfg["panel_message_id"] = new_msg.id
        save_guild_config(guild.id, cfg)
        return True, "Panel został utworzony."
    except discord.Forbidden:
        return False, "Bot nie ma uprawnień do pisania w tym kanale."
    except discord.HTTPException as e:
        return False, f"Błąd tworzenia panelu: {e}"


async def refresh_saved_panel(guild: discord.Guild):
    cfg = get_guild_config(guild.id)
    if not cfg or not cfg.get("panel_channel_id"):
        return

    ok, msg = await send_or_update_panel_message(guild)
    if not ok:
        logging.warning(f"[PANEL] {guild.id}: {msg}")


async def update_one_guild(guild: discord.Guild):
    guild_cfg = get_guild_config(guild.id)
    if not guild_cfg:
        logging.warning(f"[UPDATE] Brak konfiguracji dla serwera {guild.name} ({guild.id})")
        return

    lock = get_lock(guild.id)
    async with lock:
        try:
            guild_cfg = await ensure_required_channels(guild, guild_cfg)

            try:
                weather = await get_weather_for_guild(guild.id, guild_cfg, use_cache=True)
            except Exception as e:
                logging.error(f"[UPDATE] Błąd pobierania pogody dla {guild.id}: {e}")
                weather = None

            await update_time_channels_for_guild(guild, guild_cfg, weather=weather)
            await update_weather_channels_for_guild(guild, guild_cfg, weather=weather)
            await update_server_stats_for_guild(guild, guild_cfg)

        except Exception as e:
            logging.error(f"[UPDATE] Krytyczny błąd aktualizacji serwera {guild.id}: {e}")


async def update_only_stats_for_guild(guild: discord.Guild):
    guild_cfg = get_guild_config(guild.id)
    if not guild_cfg:
        return

    lock = get_lock(guild.id)
    async with lock:
        try:
            guild_cfg = await ensure_required_channels(guild, guild_cfg)
            await update_server_stats_for_guild(guild, guild_cfg)
        except Exception as e:
            logging.error(f"[STATS] Błąd aktualizacji statystyk dla {guild.id}: {e}")


async def schedule_quick_refresh(guild: discord.Guild, delay: float = QUICK_REFRESH_DEFAULT_DELAY):
    if guild is None:
        return

    old_task = guild_refresh_tasks.get(guild.id)
    if old_task and not old_task.done():
        old_task.cancel()

    async def delayed():
        try:
            await asyncio.sleep(delay)
            await update_one_guild(guild)
            await refresh_saved_panel(guild)
            logging.info(f"[REFRESH] Zakończono odświeżenie dla {guild.name} ({guild.id})")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"[REFRESH] Błąd odświeżenia dla {guild.id}: {e}")

    guild_refresh_tasks[guild.id] = asyncio.create_task(delayed())


async def delete_managed_categories(guild: discord.Guild, cfg: dict):
    category_ids = [
        cfg.get("clock_category_id"),
        cfg.get("weather_category_id"),
        cfg.get("stats_category_id"),
    ]

    for category_id in category_ids:
        if not category_id:
            continue

        category = guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            continue

        for ch in list(category.channels):
            try:
                await ch.delete(reason="Kosmiczny Zegar: usuwanie setupu")
            except discord.Forbidden:
                logging.error(f"[DELETE] Brak uprawnień do usunięcia kanału {ch.id}")
            except discord.HTTPException as e:
                logging.error(f"[DELETE] Błąd usuwania kanału {ch.id}: {e}")

        try:
            await category.delete(reason="Kosmiczny Zegar: usuwanie setupu")
        except discord.Forbidden:
            logging.error(f"[DELETE] Brak uprawnień do usunięcia kategorii {category.id}")
        except discord.HTTPException as e:
            logging.error(f"[DELETE] Błąd usuwania kategorii {category.id}: {e}")


async def clear_guild_commands_once():
    for guild in bot.guilds:
        try:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            logging.info(f"[CLEAR GUILD] Wyczyszczono komendy guild na {guild.name} ({guild.id})")
        except Exception as e:
            logging.error(f"[CLEAR GUILD] Błąd dla {guild.name} ({guild.id}): {e}")


async def sync_global_commands():
    try:
        synced = await bot.tree.sync()
        logging.info(f"[SYNC GLOBAL] Zsynchronizowano {len(synced)} komend globalnych")
        return len(synced)
    except Exception as e:
        logging.error(f"[SYNC GLOBAL] Błąd sync globalnego: {e}")
        return 0


@tasks.loop(minutes=CHANNELS_REFRESH_MINUTES)
async def channels_refresh_loop():
    config = get_all_guild_configs()
    if not config:
        logging.warning("[LOOP] Brak konfiguracji w bazie danych")
        return

    logging.info(f"[LOOP] Start pełnego odświeżania | serwery={len(config)}")

    for guild_id in config.keys():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_one_guild(guild)
        else:
            logging.warning(f"[LOOP] Bot nie widzi serwera o ID {guild_id}")

    logging.info("[LOOP] Pełne odświeżanie zakończone")


@tasks.loop(minutes=STATS_REFRESH_MINUTES)
async def stats_refresh_loop():
    config = get_all_guild_configs()
    if not config:
        return

    logging.info(f"[STATS LOOP] Start odświeżania statystyk | serwery={len(config)}")

    for guild_id in config.keys():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_only_stats_for_guild(guild)

    logging.info("[STATS LOOP] Odświeżanie statystyk zakończone")


@tasks.loop(minutes=PANEL_REFRESH_MINUTES)
async def panel_refresh_loop():
    config = get_all_guild_configs()
    if not config:
        return

    for guild_id in config.keys():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await refresh_saved_panel(guild)


@tasks.loop(minutes=PRESENCE_REFRESH_MINUTES)
async def presence_loop():
    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.CustomActivity(name=f"🕒 {now_warsaw().strftime('%H:%M')}")
        )
    except Exception as e:
        logging.error(f"[PRESENCE] Błąd ustawiania statusu: {e}")


@channels_refresh_loop.before_loop
async def before_channels_refresh_loop():
    await bot.wait_until_ready()


@stats_refresh_loop.before_loop
async def before_stats_refresh_loop():
    await bot.wait_until_ready()


@panel_refresh_loop.before_loop
async def before_panel_refresh_loop():
    await bot.wait_until_ready()


@presence_loop.before_loop
async def before_presence_loop():
    await bot.wait_until_ready()


@bot.tree.command(name="ping", description="Sprawdza czy publiczny bot działa")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Publiczny bot działa!", ephemeral=True)


@bot.tree.command(name="setup", description="Tworzy kategorie i kanały Kosmicznego Zegara")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_clock(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    try:
        await create_setup_for_guild(guild)
        await update_one_guild(guild)
        await refresh_saved_panel(guild)

        await interaction.followup.send(
            "✅ Utworzono i od razu odświeżono:\n"
            "🛰️ Kosmiczny Zegar\n"
            "🌤️ Pogoda\n"
            "📊 Statystyki",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Bot nie ma wymaganych uprawnień. Potrzebuje `Manage Channels`.",
            ephemeral=True
        )
    except Exception as e:
        logging.error(f"Błąd /setup na serwerze {guild.id}: {e}")
        await interaction.followup.send(
            f"❌ Wystąpił błąd podczas setupu: {e}",
            ephemeral=True
        )


@bot.tree.command(name="status", description="Pokazuje status konfiguracji Kosmicznego Zegara")
async def status_clock(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Ten serwer nie ma jeszcze konfiguracji. Użyj `/setup`.", ephemeral=True)
        return

    panel_channel = get_panel_channel(guild, cfg)
    embed = discord.Embed(title="🛰️ Status Kosmicznego Zegara", color=discord.Color.blue())
    embed.add_field(name="Miasto", value=cfg.get("city_name", DEFAULT_CITY), inline=True)
    embed.add_field(name="Strefa czasowa", value=cfg.get("timezone", DEFAULT_TIMEZONE), inline=True)
    embed.add_field(name="Kanały", value=str(len(cfg.get("channels", {}))), inline=True)
    embed.add_field(
        name="Stały panel",
        value=panel_channel.mention if panel_channel else "Nie ustawiono",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="refresh", description="Natychmiast odświeża wszystkie kanały")
@app_commands.checks.has_permissions(manage_guild=True)
async def refresh_clock(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        await update_one_guild(guild)
        await refresh_saved_panel(guild)
        await interaction.followup.send("✅ Wszystkie kanały i panel zostały odświeżone.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd odświeżania: {e}", ephemeral=True)


@bot.tree.command(name="miasto", description="Ustawia miasto z całego świata")
@app_commands.describe(nazwa="Np. Rzeszów, Warszawa, Berlin, London")
@app_commands.checks.has_permissions(manage_guild=True)
async def city_clock(interaction: discord.Interaction, nazwa: str):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        result = await geocode_city(nazwa)
        if not result:
            await interaction.followup.send("❌ Nie znaleziono takiego miasta.", ephemeral=True)
            return

        current_cfg = get_guild_config(guild.id) or {}

        city_display = result["name"]
        if result.get("country"):
            city_display = f'{result["name"]}, {result["country"]}'

        new_cfg = {
            "city_name": city_display,
            "latitude": result["latitude"],
            "longitude": result["longitude"],
            "timezone": result["timezone"],
            "clock_category_id": current_cfg.get("clock_category_id"),
            "weather_category_id": current_cfg.get("weather_category_id"),
            "stats_category_id": current_cfg.get("stats_category_id"),
            "panel_channel_id": current_cfg.get("panel_channel_id"),
            "panel_message_id": current_cfg.get("panel_message_id"),
            "channels": current_cfg.get("channels", {})
        }

        save_guild_config(guild.id, new_cfg)
        weather_cache.pop(guild.id, None)

        await update_one_guild(guild)
        await refresh_saved_panel(guild)

        await interaction.followup.send(
            f"✅ Ustawiono miasto: **{city_display}**",
            ephemeral=True
        )
    except Exception as e:
        logging.error(f"Błąd /miasto na serwerze {guild.id}: {e}")
        await interaction.followup.send(f"❌ Błąd ustawiania miasta: {e}", ephemeral=True)


@bot.tree.command(name="botstats", description="Pokazuje statystyki publicznego bota")
async def botstats(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_botstats_embed(), ephemeral=True)


@bot.tree.command(name="invite", description="Link do dodania bota")
async def invite_bot(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_invite_embed(), ephemeral=True)


@bot.tree.command(name="panel", description="Pokazuje panel Kosmicznego Zegara")
@app_commands.checks.has_permissions(manage_guild=True)
async def panel_clock(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej akcji można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    embed = build_panel_embed(guild, cfg)
    view = RefreshPanelView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="ustaw_panel", description="Tworzy lub aktualizuje stały panel w wybranym kanale tekstowym")
@app_commands.describe(kanal="Kanał tekstowy, w którym ma być zapisany panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def set_panel_command(interaction: discord.Interaction, kanal: discord.TextChannel):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        cfg["panel_channel_id"] = kanal.id
        cfg["panel_message_id"] = None
        save_guild_config(guild.id, cfg)

        ok, msg = await send_or_update_panel_message(guild, target_channel=kanal)
        if ok:
            await interaction.followup.send(f"✅ Stały panel ustawiony w kanale {kanal.mention}.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd ustawiania panelu: {e}", ephemeral=True)


@bot.tree.command(name="odswiez_panel", description="Odświeża zapisany stały panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def refresh_panel_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    ok, msg = await send_or_update_panel_message(guild)
    if ok:
        await interaction.followup.send("✅ Panel został odświeżony.", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ {msg}", ephemeral=True)


@bot.tree.command(name="usun_setup", description="Usuwa cały setup Kosmicznego Zegara z serwera")
@app_commands.checks.has_permissions(manage_guild=True)
async def remove_setup_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Ten serwer nie ma zapisanej konfiguracji.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await delete_managed_categories(guild, cfg)
        weather_cache.pop(guild.id, None)
        delete_guild_config(guild.id)
        await interaction.followup.send("✅ Setup Kosmicznego Zegara został usunięty.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd usuwania setupu: {e}", ephemeral=True)


@bot.tree.command(name="help", description="Pokazuje listę komend")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)


@bot.tree.command(name="pogoda", description="Pokazuje aktualną pogodę")
async def weather_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    try:
        weather = await get_weather_for_guild(guild.id, cfg, use_cache=True)
        await interaction.response.send_message(embed=build_weather_embed(cfg, weather), ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd pobierania pogody: {e}", ephemeral=True)


@bot.tree.command(name="czas", description="Pokazuje aktualny czas")
async def time_command(interaction: discord.Interaction):
    dt = now_warsaw()
    embed = discord.Embed(title="🕒 Aktualny czas", color=discord.Color.orange())
    embed.add_field(name="Godzina", value=dt.strftime("%H:%M:%S"), inline=False)
    embed.add_field(name="Data", value=dt.strftime("%d.%m.%Y"), inline=False)
    embed.add_field(name="Pora dnia", value=get_part_of_day(dt.hour).replace("・", " "), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ksiezyc", description="Pokazuje aktualną fazę księżyca")
async def moon_command(interaction: discord.Interaction):
    embed = discord.Embed(title="🌙 Faza księżyca", color=discord.Color.purple())
    embed.description = f"**{format_moon_for_command(now_warsaw())}**"
    await interaction.response.send_message(embed=embed, ephemeral=True)


@setup_clock.error
@refresh_clock.error
@city_clock.error
@panel_clock.error
@set_panel_command.error
@refresh_panel_command.error
@remove_setup_command.error
async def common_manage_guild_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("❌ Musisz mieć uprawnienie `Manage Server`.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Musisz mieć uprawnienie `Manage Server`.", ephemeral=True)
    else:
        logging.error(f"[COMMAND ERROR] {error}")
        if interaction.response.is_done():
            await interaction.followup.send("❌ Wystąpił nieoczekiwany błąd.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Wystąpił nieoczekiwany błąd.", ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)

    if isinstance(original, app_commands.errors.MissingPermissions):
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Nie masz wymaganych uprawnień.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Nie masz wymaganych uprawnień.", ephemeral=True)
        except Exception:
            pass
        return

    logging.error(f"[APP CMD ERROR] {type(original).__name__}: {original}")

    try:
        if interaction.response.is_done():
            await interaction.followup.send("❌ Wystąpił błąd podczas wykonywania komendy.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Wystąpił błąd podczas wykonywania komendy.", ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_member_join(member: discord.Member):
    await schedule_quick_refresh(member.guild, delay=30.0)


@bot.event
async def on_member_remove(member: discord.Member):
    await schedule_quick_refresh(member.guild, delay=30.0)


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    if before.channel != after.channel:
        await schedule_quick_refresh(member.guild, delay=20.0)


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    if before.status == after.status:
        return

    now = now_warsaw()
    last_run = guild_presence_debounce.get(after.guild.id)

    if last_run and (now - last_run).total_seconds() < 180:
        return

    guild_presence_debounce[after.guild.id] = now
    await schedule_quick_refresh(after.guild, delay=90.0)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    guild = getattr(channel, "guild", None)
    if guild is None:
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        return

    changed = False

    if channel.id in {
        cfg.get("clock_category_id"),
        cfg.get("weather_category_id"),
        cfg.get("stats_category_id"),
    }:
        if channel.id == cfg.get("clock_category_id"):
            cfg["clock_category_id"] = None
        if channel.id == cfg.get("weather_category_id"):
            cfg["weather_category_id"] = None
        if channel.id == cfg.get("stats_category_id"):
            cfg["stats_category_id"] = None
        changed = True

    if channel.id == cfg.get("panel_channel_id"):
        cfg["panel_channel_id"] = None
        cfg["panel_message_id"] = None
        changed = True

    channels = dict(cfg.get("channels", {}))
    removed_keys = [key for key, ch_id in channels.items() if ch_id == channel.id]
    for key in removed_keys:
        channels.pop(key, None)
        changed = True

    if changed:
        cfg["channels"] = channels
        save_guild_config(guild.id, cfg)
        logging.info(f"[DELETE EVENT] Zaktualizowano konfigurację po usunięciu kanału {channel.id}")

        if channels or cfg.get("clock_category_id") or cfg.get("weather_category_id") or cfg.get("stats_category_id"):
            await schedule_quick_refresh(guild, delay=20.0)


@bot.event
async def on_guild_join(guild: discord.Guild):
    logging.info(f"[GUILD JOIN] Bot dołączył do serwera: {guild.name} ({guild.id})")


@bot.event
async def on_ready():
    if not bot.ready_logged:
        logging.info(f"Zalogowano jako {bot.user}")
        bot.ready_logged = True

    if not bot.synced_once:
        try:
            if CLEAN_OLD_GUILD_COMMANDS_ON_START:
                await clear_guild_commands_once()

            await sync_global_commands()
            bot.synced_once = True
            logging.info("[READY] Synchronizacja komend zakończona")
        except Exception as e:
            logging.error(f"[READY] Błąd synchronizacji komend: {e}")

    if not channels_refresh_loop.is_running():
        channels_refresh_loop.start()
        logging.info("[READY] Uruchomiono channels_refresh_loop")

    if not stats_refresh_loop.is_running():
        stats_refresh_loop.start()
        logging.info("[READY] Uruchomiono stats_refresh_loop")

    if not panel_refresh_loop.is_running():
        panel_refresh_loop.start()
        logging.info("[READY] Uruchomiono panel_refresh_loop")

    if not presence_loop.is_running():
        presence_loop.start()
        logging.info("[READY] Uruchomiono presence_loop")

    for guild in bot.guilds:
        if not get_guild_config(guild.id):
            logging.warning(f"[READY] Brak configu dla serwera {guild.name} ({guild.id})")


async def close_http_session():
    global http_session
    if http_session and not http_session.closed:
        await http_session.close()
        logging.info("Sesja HTTP zamknięta")


async def main():
    if not TOKEN:
        raise ValueError("Brak PUBLIC_DISCORD_TOKEN lub DISCORD_TOKEN w Railway Variables")

    init_db()

    try:
        await bot.start(TOKEN)
    finally:
        await close_http_session()


if __name__ == "__main__":
    asyncio.run(main())
