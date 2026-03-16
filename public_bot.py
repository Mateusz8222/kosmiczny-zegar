# ================================
# KOSMICZNY ZEGAR 24 - BOT v11
# ================================

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
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

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Brakuje DISCORD_TOKEN w zmiennych środowiskowych")

DEFAULT_CITY_NAME = "Warszawa"
DEFAULT_LATITUDE = 52.2297
DEFAULT_LONGITUDE = 21.0122
DEFAULT_COUNTRY = "Polska"
DEFAULT_TIMEZONE = "Europe/Warsaw"

WEATHER_REFRESH_MINUTES = 15
CHANNEL_EDIT_DELAY = 1.1
CATEGORY_DELETE_DELAY = 0.7
STATS_DEBOUNCE_SECONDS = 2.0
MAX_CHANNEL_NAME_LEN = 95

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "bot_data.db"

CHANNEL_TEMPLATES = {
    # POGODA
    "temperature": ("weather", "🌡 Temperatura"),
    "feels": ("weather", "🤒 Odczuwalna"),
    "clouds": ("weather", "☁ Zachmurzenie"),
    "air": ("weather", "🌬 Powietrze"),
    "pollen": ("weather", "🌿 Pylenie"),
    "rain": ("weather", "🌧 Opady"),
    "wind": ("weather", "💨 Wiatr"),
    "pressure": ("weather", "⏱ Ciśnienie"),

    # KOSMICZNY ZEGAR
    "date": ("clock", "📅 Data"),
    "part_of_day": ("clock", "🌓 Pora dnia"),
    "sunrise": ("clock", "🌅 Wschód"),
    "sunset": ("clock", "🌇 Zachód"),
    "day_length": ("clock", "☀️ Dzień"),
    "moon": ("clock", "🌙 Faza księżyca"),

    # STATYSTYKI
    "members": ("stats", "👥 Wszyscy"),
    "online": ("stats", "🟢 Online"),
    "bots": ("stats", "🤖 Boty"),
    "vc": ("stats", "🔊 Na VC"),
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
        return None
    return guild.get_channel(channel_id)

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


def air_quality_text(eaqi):
    if eaqi is None:
        return "🌬 Powietrze brak danych"

    value = float(eaqi)

    if value <= 20:
        return "🌬 Powietrze bardzo dobre"
    if value <= 40:
        return "🌬 Powietrze dobre"
    if value <= 60:
        return "🌬 Powietrze umiarkowane"
    if value <= 80:
        return "🌬 Powietrze dostateczne"
    if value <= 100:
        return "🌬 Powietrze złe"

    return "🌬 Powietrze bardzo złe"


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


def build_single_pollen_text(alder, birch, grass, mugwort, ragweed) -> str:
    values = {
        "Olsza": float(alder or 0),
        "Brzoza": float(birch or 0),
        "Trawy": float(grass or 0),
        "Bylica": float(mugwort or 0),
        "Ambrozja": float(ragweed or 0),
    }

    top_name = max(values, key=values.get)
    top_value = values[top_name]

    if top_value <= 0:
        return "🌿 Pylenie brak"

    return f"🌿 Pylenie {top_name} {pollen_level_name(top_value)}"


def format_part_of_day(hour: int) -> str:
    if 5 <= hour < 8:
        return "🌓 Pora dnia świt"
    if 8 <= hour < 12:
        return "🌓 Pora dnia poranek"
    if 12 <= hour < 17:
        return "🌓 Pora dnia dzień"
    if 17 <= hour < 21:
        return "🌓 Pora dnia wieczór"
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

# ================================
# POBIERANIE POGODY
# ================================

async def get_weather_data(city_name: str, latitude: float, longitude: float, timezone_name: str = DEFAULT_TIMEZONE):
    encoded_timezone = quote(timezone_name)

    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}"
        f"&longitude={longitude}"
        "&current=temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure,cloud_cover"
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
    precip = current.get("precipitation")
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

    return {
        "temperature": f"🌡 {city_name.upper()} {round(float(temp))}°C" if temp is not None else f"🌡 {city_name.upper()} --°C",
        "feels": f"🤒 Odczuwalna {round(float(feels))}°C" if feels is not None else "🤒 Odczuwalna --°C",
        "clouds": f"☁ Zachmurzenie {round(float(clouds))}%" if clouds is not None else "☁ Zachmurzenie --%",
        "air": air_quality_text(air_current.get("european_aqi")),
        "pollen": build_single_pollen_text(alder, birch, grass, mugwort, ragweed),
        "rain": "🌧 Opady brak" if precip is not None and float(precip) == 0 else (
            f"🌧 Opady {round(float(precip), 1)} mm" if precip is not None else "🌧 Opady --"
        ),
        "wind": f"💨 Wiatr {round(float(wind))} km/h" if wind is not None else "💨 Wiatr -- km/h",
        "pressure": f"⏱ Ciśnienie {round(float(pressure))} hPa" if pressure is not None else "⏱ Ciśnienie -- hPa",
        "sunrise": f"🌅 Wschód {sunrise_time}",
        "sunset": f"🌇 Zachód {sunset_time}",
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
        return

    new_name = trim_channel_name(new_name)

    if channel.name == new_name:
        return

    try:
        await channel.edit(
            name=new_name,
            reason="Kosmiczny Zegar: aktualizacja nazwy kanału"
        )
        await asyncio.sleep(CHANNEL_EDIT_DELAY)
    except Exception as e:
        logging.error(f"Błąd zmiany nazwy kanału {getattr(channel, 'id', 'brak_id')}: {e}")

# ================================
# AKTUALIZACJA KANAŁÓW
# ================================

async def update_weather_channels(guild: discord.Guild, cfg: dict, weather: dict):
    for key in [
        "temperature",
        "feels",
        "clouds",
        "air",
        "pollen",
        "rain",
        "wind",
        "pressure"
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
        format_part_of_day(now.hour)
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

    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "members"),
        f"👥 Wszyscy {members_count}"
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


async def refresh_stats_only(guild: discord.Guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        return

    await update_stats_channels(guild, cfg)

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

    embed = discord.Embed(
        title="🕐 Aktualny czas",
        color=discord.Color.orange()
    )
    embed.add_field(name="Miasto", value=city_name, inline=False)
    embed.add_field(name="Godzina", value=now.strftime("%H:%M:%S"), inline=False)
    embed.add_field(name="Data", value=now.strftime("%d.%m.%Y"), inline=False)
    embed.add_field(name="Pora dnia", value=format_part_of_day(now.hour), inline=False)
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

        cfg = get_guild_config(guild.id)
        if not cfg:
            cfg = build_default_guild_config(guild.id)

        cfg["city_name"] = city["name"] or DEFAULT_CITY_NAME
        cfg["latitude"] = city["latitude"] if city["latitude"] is not None else DEFAULT_LATITUDE
        cfg["longitude"] = city["longitude"] if city["longitude"] is not None else DEFAULT_LONGITUDE
        cfg["country"] = city.get("country", DEFAULT_COUNTRY)
        cfg["timezone"] = city.get("timezone", DEFAULT_TIMEZONE)

        save_guild_config(guild.id, cfg)

        await setup_categories_and_channels(guild)
        await refresh_existing_panel(guild)

        extra = ""
        if city.get("admin1"):
            extra = f", {city['admin1']}"

        await interaction.followup.send(
            f"✅ Ustawiono miasto: **{city['name']}{extra}, {city['country']}**",
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
    logging.info(f"Zalogowano jako {bot.user}")

    try:
        synced = await bot.tree.sync()
        logging.info(f"Zsynchronizowano {len(synced)} komend")
    except Exception as e:
        logging.error(f"Błąd synchronizacji komend: {e}")

    if not auto_refresh.is_running():
        auto_refresh.start()


init_db()
bot.run(TOKEN)
