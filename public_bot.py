# ================================
# KOSMICZNY ZEGAR 24 - BOT
# WERSJA Z DOKŁADNYM PYLENIEM
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
intents.presences = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "bot_data.db"

CHANNEL_TEMPLATES = {
    # POGODA
    "temperature": ("weather", "🌡️ • Temperatura"),
    "feels": ("weather", "🥵 • Odczuwalna"),
    "clouds": ("weather", "☁️ • Zachmurzenie"),
    "air": ("weather", "🟢 • Powietrze"),
    "pollen_summary": ("weather", "🌾 • Alergen dnia"),
    "pollen_alder": ("weather", "🌳 • Olsza"),
    "pollen_birch": ("weather", "🌿 • Brzoza"),
    "pollen_grass": ("weather", "🌱 • Trawy"),
    "pollen_mugwort": ("weather", "🌼 • Bylica"),
    "pollen_ragweed": ("weather", "🤧 • Ambrozja"),
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
# POGODA / POWIETRZE / PYLENIE
# ================================

async def fetch_json(url: str):
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()


def air_quality_text(eaqi):
    if eaqi is None:
        return "⚪ • Powietrze brak danych"

    value = float(eaqi)

    if value <= 20:
        return "🟢 • Powietrze bardzo dobre"
    if value <= 40:
        return "🟢 • Powietrze dobre"
    if value <= 60:
        return "🟡 • Powietrze umiarkowane"
    if value <= 80:
        return "🟠 • Powietrze dostateczne"
    if value <= 100:
        return "🔴 • Powietrze złe"

    return "☠️ • Powietrze bardzo złe"


def format_part_of_day(hour: int) -> str:
    if 5 <= hour < 8:
        return "🌅 • Świt"
    if 8 <= hour < 12:
        return "🌄 • Poranek"
    if 12 <= hour < 17:
        return "☀️ • Dzień"
    if 17 <= hour < 21:
        return "🌆 • Wieczór"
    return "🌙 • Noc"


def day_length_text(sunrise_str, sunset_str):
    try:
        sunrise = datetime.strptime(sunrise_str, "%H:%M")
        sunset = datetime.strptime(sunset_str, "%H:%M")

        diff = sunset - sunrise
        minutes = int(diff.total_seconds() // 60)

        hours = minutes // 60
        mins = minutes % 60

        return f"☀️ • Długość dnia {hours}h {mins}m"

    except Exception:
        return "☀️ • Długość dnia --"


def pollen_level_name(value: float) -> str:
    if value <= 0:
        return "brak"
    if value <= 10:
        return "niskie"
    if value <= 50:
        return "umiarkowane"
    if value <= 100:
        return "wysokie"
    return "bardzo wysokie"


def pollen_level_emoji(value: float) -> str:
    if value <= 0:
        return "⚪"
    if value <= 10:
        return "🟢"
    if value <= 50:
        return "🟡"
    if value <= 100:
        return "🟠"
    return "🔴"


def pollen_channel_text(label: str, value) -> str:
    try:
        v = float(value or 0)
    except Exception:
        v = 0.0

    emoji = pollen_level_emoji(v)
    level = pollen_level_name(v)
    return f"{emoji} • {label} {level}"


def build_pollen_summary(alder, birch, grass, mugwort, ragweed) -> str:
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
        return "⚪ • Alergen dnia brak"

    return f"{pollen_level_emoji(top_value)} • Alergen dnia {top_name}"


async def get_weather_data():
    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}"
        f"&longitude={LONGITUDE}"
        "&current=temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure,cloud_cover"
        "&daily=sunrise,sunset"
        "&timezone=Europe%2FWarsaw"
    )

    air_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={LATITUDE}"
        f"&longitude={LONGITUDE}"
        "&current=european_aqi,alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,ragweed_pollen"
        "&timezone=Europe%2FWarsaw"
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
        "temperature": f"🌡️ • {CITY_NAME} {round(float(temp))}°C" if temp is not None else f"🌡️ • {CITY_NAME} --°C",
        "feels": f"🥵 • Odczuwalna {round(float(feels))}°C" if feels is not None else "🥵 • Odczuwalna --°C",
        "clouds": f"☁️ • Zachmurzenie {round(float(clouds))}%" if clouds is not None else "☁️ • Zachmurzenie --%",
        "air": air_quality_text(air_current.get("european_aqi")),
        "pollen_summary": build_pollen_summary(alder, birch, grass, mugwort, ragweed),
        "pollen_alder": pollen_channel_text("Olsza", alder),
        "pollen_birch": pollen_channel_text("Brzoza", birch),
        "pollen_grass": pollen_channel_text("Trawy", grass),
        "pollen_mugwort": pollen_channel_text("Bylica", mugwort),
        "pollen_ragweed": pollen_channel_text("Ambrozja", ragweed),
        "rain": "🌧️ • Brak opadów" if precip is not None and float(precip) == 0 else (
            f"🌧️ • Opady {round(float(precip), 1)} mm" if precip is not None else "🌧️ • Opady --"
        ),
        "wind": f"💨 • Wiatr {round(float(wind))} km/h" if wind is not None else "💨 • Wiatr -- km/h",
        "pressure": f"🧭 • Ciśnienie {round(float(pressure))} hPa" if pressure is not None else "🧭 • Ciśnienie -- hPa",
        "sunrise": f"🌅 • Wschód {sunrise_time}",
        "sunset": f"🌇 • Zachód {sunset_time}",
        "day_length": day_length_text(sunrise_time, sunset_time),

        # surowe wartości do /pogoda
        "raw_pollen": {
            "Olsza": float(alder or 0),
            "Brzoza": float(birch or 0),
            "Trawy": float(grass or 0),
            "Bylica": float(mugwort or 0),
            "Ambrozja": float(ragweed or 0),
        }
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
    for channel in category.voice_channels:
        if channel.name == name:
            return channel

    return await guild.create_voice_channel(
        name=name,
        category=category,
        reason="Kosmiczny Zegar: tworzenie kanału"
    )


async def ensure_categories_and_channels(guild: discord.Guild):
    cfg = get_guild_config(guild.id)

    if not cfg:
        cfg = {
            "guild_id": guild.id,
            "weather_category_id": None,
            "clock_category_id": None,
            "stats_category_id": None,
            "channels": {}
        }

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
        current_channel = None
        channel_id = channels.get(key)

        if channel_id:
            current_channel = guild.get_channel(channel_id)

        if current_channel is None:
            category = category_map[group_name]
            created = await create_or_get_voice_channel(guild, category, fallback_name)
            channels[key] = created.id

    cfg["channels"] = channels
    save_guild_config(guild.id, cfg)

    return cfg


async def safe_edit_channel_name(channel: discord.abc.GuildChannel | None, new_name: str):
    if channel is None:
        return

    if channel.name == new_name:
        return

    try:
        await channel.edit(
            name=new_name,
            reason="Kosmiczny Zegar: aktualizacja nazwy kanału"
        )
        await asyncio.sleep(1.3)
    except Exception as e:
        logging.error(f"Błąd zmiany nazwy kanału {getattr(channel, 'id', 'brak_id')}: {e}")


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
        0: "🌑 • Nów",
        1: "🌒 • Sierp przybywający",
        2: "🌓 • Pierwsza kwadra",
        3: "🌔 • Garb przybywający",
        4: "🌕 • Pełnia",
        5: "🌖 • Garb ubywający",
        6: "🌗 • Ostatnia kwadra",
        7: "🌘 • Sierp ubywający",
    }

    return phases.get(phase_index, "🌙 • Księżyc")


# ================================
# AKTUALIZACJA KANAŁÓW
# ================================

async def update_weather_channels(guild: discord.Guild, cfg: dict, weather: dict):
    for key in [
        "temperature",
        "feels",
        "clouds",
        "air",
        "pollen_summary",
        "pollen_alder",
        "pollen_birch",
        "pollen_grass",
        "pollen_mugwort",
        "pollen_ragweed",
        "rain",
        "wind",
        "pressure"
    ]:
        await safe_edit_channel_name(
            get_channel_from_config(guild, cfg, key),
            weather[key]
        )


async def update_clock_channels(guild: discord.Guild, cfg: dict, weather: dict):
    now = datetime.now(TIMEZONE)
    weekdays = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]

    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "date"),
        f"📅 • {weekdays[now.weekday()]} {now.strftime('%d.%m.%Y')}"
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
        f"👥 • Wszyscy {members_count}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "online"),
        f"🟢 • Online {online_count}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "bots"),
        f"🤖 • Boty {bots_count}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "vc"),
        f"🔊 • Na VC {vc_count}"
    )


async def refresh_all(guild: discord.Guild):
    cfg = await ensure_categories_and_channels(guild)
    weather = await get_weather_data()

    await update_weather_channels(guild, cfg, weather)
    await update_clock_channels(guild, cfg, weather)
    await update_stats_channels(guild, cfg)


@tasks.loop(minutes=10)
async def auto_refresh():
    for guild in bot.guilds:
        try:
            await refresh_all(guild)
        except Exception as e:
            logging.error(f"Błąd auto_refresh dla {guild.id}: {e}")


@auto_refresh.before_loop
async def before_auto_refresh():
    await bot.wait_until_ready()


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
        await ensure_categories_and_channels(guild)
        await refresh_all(guild)
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
        await refresh_all(guild)
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

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="pogoda", description="Pokazuje aktualną pogodę i dokładne pylenie")
async def weather_command(interaction: discord.Interaction):
    try:
        weather = await get_weather_data()

        embed = discord.Embed(
            title="🌤️ Pogoda i pylenie",
            color=discord.Color.teal()
        )
        embed.add_field(name="Temperatura", value=weather["temperature"], inline=False)
        embed.add_field(name="Odczuwalna", value=weather["feels"], inline=False)
        embed.add_field(name="Zachmurzenie", value=weather["clouds"], inline=False)
        embed.add_field(name="Powietrze", value=weather["air"], inline=False)
        embed.add_field(name="Alergen dnia", value=weather["pollen_summary"], inline=False)
        embed.add_field(name="Olsza", value=weather["pollen_alder"], inline=False)
        embed.add_field(name="Brzoza", value=weather["pollen_birch"], inline=False)
        embed.add_field(name="Trawy", value=weather["pollen_grass"], inline=False)
        embed.add_field(name="Bylica", value=weather["pollen_mugwort"], inline=False)
        embed.add_field(name="Ambrozja", value=weather["pollen_ragweed"], inline=False)
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
    now = datetime.now(TIMEZONE)

    embed = discord.Embed(
        title="🕐 Aktualny czas",
        color=discord.Color.orange()
    )
    embed.add_field(name="Godzina", value=now.strftime("%H:%M:%S"), inline=False)
    embed.add_field(name="Data", value=now.strftime("%d.%m.%Y"), inline=False)
    embed.add_field(name="Pora dnia", value=format_part_of_day(now.hour), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ksiezyc", description="Pokazuje aktualną fazę księżyca")
async def moon_command(interaction: discord.Interaction):
    now = datetime.now(TIMEZONE)
    await interaction.response.send_message(
        moon_phase_name(now),
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
