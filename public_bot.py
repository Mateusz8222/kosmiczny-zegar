import os
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("PUBLIC_DISCORD_TOKEN")
CONFIG_FILE = "guilds.json"
DEFAULT_TIMEZONE = "Europe/Warsaw"
EDIT_DELAY_SECONDS = 6

GEOCODING_API = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"

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


# =========================
# CONFIG
# =========================

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_guild_config(guild_id: int):
    config = load_config()
    return config.get(str(guild_id))


def set_guild_config(guild_id: int, guild_data: dict):
    config = load_config()
    config[str(guild_id)] = guild_data
    save_config(config)


def get_guild_timezone(guild_id: int) -> ZoneInfo:
    cfg = get_guild_config(guild_id)
    tz_name = DEFAULT_TIMEZONE

    if cfg:
        tz_name = cfg.get("timezone", DEFAULT_TIMEZONE)

    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def now_for_guild(guild_id: int) -> datetime:
    return datetime.now(get_guild_timezone(guild_id))


def get_channel(guild, guild_cfg, key):
    channels = guild_cfg.get("channels", {})
    cid = channels.get(key)

    if not cid:
        logging.warning(f"[{guild.name}] Brak ID kanału dla klucza: {key}")
        return None

    channel = guild.get_channel(cid)
    if channel is None:
        logging.warning(f"[{guild.name}] Nie znaleziono kanału dla klucza: {key}, id: {cid}")

    return channel


# =========================
# FORMATY
# =========================

def format_date(dt: datetime):
    dni = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]
    return f"🗓️・{dni[dt.weekday()]} {dt.strftime('%d.%m.%Y')}"


def part_of_day(hour: int):
    if 5 <= hour < 12:
        return "☀️・Poranek"
    if 12 <= hour < 18:
        return "🌞・Popołudnie"
    if 18 <= hour < 22:
        return "🌆・Wieczór"
    return "🌙・Noc"


def moon_phase(dt: datetime):
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
    phase = round(jd * 8)

    if phase >= 8:
        phase = 0

    phases = {
        0: "🌑・Nów",
        1: "🌒・Młody księżyc",
        2: "🌓・I kwadra",
        3: "🌔・Przybywa",
        4: "🌕・Pełnia",
        5: "🌖・Ubywa",
        6: "🌗・III kwadra",
        7: "🌘・Stary księżyc",
    }

    return phases.get(phase, "🌙・Księżyc")


# =========================
# HTTP
# =========================

async def fetch_json(url: str, params: dict):
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url,
            params=params,
            headers={"User-Agent": "KosmicznyZegarPublic/1.0"}
        ) as response:
            response.raise_for_status()
            return await response.json()


async def geocode_city(name: str, country_code: str | None = None):
    params = {
        "name": name,
        "count": 1,
        "language": "pl",
        "format": "json",
    }

    if country_code:
        params["countryCode"] = country_code.upper()

    data = await fetch_json(GEOCODING_API, params)
    results = data.get("results", [])

    if not results:
        return None

    result = results[0]
    return {
        "name": result.get("name", name),
        "country": result.get("country", ""),
        "country_code": result.get("country_code", ""),
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "timezone": result.get("timezone", DEFAULT_TIMEZONE),
    }


async def fetch_weather(lat: float, lon: float, tz: str):
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure",
        "daily": "sunrise,sunset",
        "timezone": tz,
    }
    return await fetch_json(WEATHER_API, params)


def parse_weather(data, city_display: str):
    current = data["current"]
    daily = data["daily"]

    sunrise = daily["sunrise"][0].split("T")[1][:5]
    sunset = daily["sunset"][0].split("T")[1][:5]

    precipitation = current.get("precipitation", 0)
    if precipitation and precipitation > 0:
        rain_text = f"🌧️・Opady {precipitation} mm"
    else:
        rain_text = "☁️・Bez opadów"

    return {
        "temp": f"🌡️・{city_display} {round(current['temperature_2m'])}°C",
        "feels": f"🥵・Odczuwalna {round(current['apparent_temperature'])}°C",
        "rain": rain_text,
        "wind": f"💨・Wiatr {round(current['wind_speed_10m'])} km/h",
        "pressure": f"🧭・Ciśnienie {round(current['surface_pressure'])} hPa",
        "sunrise": f"🌅・Wschód {sunrise}",
        "sunset": f"🌇・Zachód {sunset}",
    }


# =========================
# SAFE EDIT
# =========================

async def edit_channel(channel, name: str):
    if channel is None:
        return

    if channel.name == name:
        logging.info(f"SKIP: {channel.name}")
        return

    try:
        old_name = channel.name
        await channel.edit(name=name)
        logging.info(f"EDIT: '{old_name}' -> '{name}'")
        await asyncio.sleep(EDIT_DELAY_SECONDS)
    except discord.Forbidden:
        logging.error(f"Brak uprawnień do zmiany kanału: {channel.name}")
    except discord.HTTPException as e:
        logging.error(f"Błąd HTTP przy zmianie kanału '{channel.name}': {e}")
    except Exception as e:
        logging.error(f"Inny błąd przy zmianie kanału '{channel.name}': {e}")


# =========================
# UPDATE HELPERS
# =========================

def get_city_display(cfg: dict) -> str:
    city = cfg.get("city", "Miasto")
    country = cfg.get("country", "")
    if country:
        return f"{city}, {country}"
    return city


async def update_astronomy_channels(guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        logging.warning(f"[{guild.name}] Brak konfiguracji")
        return

    dt = now_for_guild(guild.id)

    await edit_channel(get_channel(guild, cfg, "date"), format_date(dt))
    await edit_channel(get_channel(guild, cfg, "day"), part_of_day(dt.hour))
    await edit_channel(get_channel(guild, cfg, "moon"), moon_phase(dt))


async def update_weather_channels(guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        logging.warning(f"[{guild.name}] Brak konfiguracji")
        return

    try:
        weather = await fetch_weather(
            cfg["latitude"],
            cfg["longitude"],
            cfg.get("timezone", DEFAULT_TIMEZONE),
        )
        w = parse_weather(weather, get_city_display(cfg))
    except Exception as e:
        logging.error(f"[{guild.name}] Błąd pogody: {e}")
        return

    await edit_channel(get_channel(guild, cfg, "temp"), w["temp"])
    await edit_channel(get_channel(guild, cfg, "feels"), w["feels"])
    await edit_channel(get_channel(guild, cfg, "rain"), w["rain"])
    await edit_channel(get_channel(guild, cfg, "wind"), w["wind"])
    await edit_channel(get_channel(guild, cfg, "pressure"), w["pressure"])
    await edit_channel(get_channel(guild, cfg, "sunrise"), w["sunrise"])
    await edit_channel(get_channel(guild, cfg, "sunset"), w["sunset"])


async def update_stats_channels(guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        logging.warning(f"[{guild.name}] Brak konfiguracji")
        return

    members = len([m for m in guild.members if not m.bot])
    online = len([m for m in guild.members if m.status != discord.Status.offline and not m.bot])
    vc = len([m for m in guild.members if m.voice and not m.bot])

    await edit_channel(get_channel(guild, cfg, "members"), f"👥・Członkowie {members}")
    await edit_channel(get_channel(guild, cfg, "online"), f"🟢・Online {online}")
    await edit_channel(get_channel(guild, cfg, "vc"), f"🎤・Na VC {vc}")


async def update_all_channels(guild):
    await update_astronomy_channels(guild)
    await update_weather_channels(guild)
    await update_stats_channels(guild)


# =========================
# LOOPS
# =========================

@tasks.loop(minutes=10)
async def astronomy_loop():
    logging.info("Uruchomiono astronomy_loop")
    config = load_config()

    for gid in config:
        guild = bot.get_guild(int(gid))
        if guild:
            await update_astronomy_channels(guild)


@astronomy_loop.before_loop
async def before_astronomy_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=10)
async def weather_loop():
    logging.info("Uruchomiono weather_loop")
    config = load_config()

    for gid in config:
        guild = bot.get_guild(int(gid))
        if guild:
            await update_weather_channels(guild)


@weather_loop.before_loop
async def before_weather_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=2)
async def stats_loop():
    logging.info("Uruchomiono stats_loop")
    config = load_config()

    for gid in config:
        guild = bot.get_guild(int(gid))
        if guild:
            await update_stats_channels(guild)


@stats_loop.before_loop
async def before_stats_loop():
    await bot.wait_until_ready()


@tasks.loop(seconds=10)
async def presence_loop():
    try:
        now_local = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        await bot.change_presence(
            activity=discord.CustomActivity(
                name=f"🕒 {now_local.strftime('%H:%M:%S')}"
            )
        )
    except Exception as e:
        logging.error(f"Błąd presence_loop: {e}")


@presence_loop.before_loop
async def before_presence_loop():
    await bot.wait_until_ready()


# =========================
# SETUP
# =========================

async def create_channels(guild):
    category = await guild.create_category("🛰️ Kosmiczny Zegar")

    channels = {}

    names = {
        "date": "🗓️・Data",
        "day": "🌞・Pora dnia",
        "moon": "🌙・Faza księżyca",
        "temp": "🌡️・Temperatura",
        "feels": "🥵・Odczuwalna",
        "rain": "☁️・Opady",
        "wind": "💨・Wiatr",
        "pressure": "🧭・Ciśnienie",
        "sunrise": "🌅・Wschód",
        "sunset": "🌇・Zachód",
        "members": "👥・Członkowie",
        "online": "🟢・Online",
        "vc": "🎤・Na VC",
    }

    for key, channel_name in names.items():
        c = await guild.create_voice_channel(channel_name, category=category)
        channels[key] = c.id

    return category.id, channels


# =========================
# COMMANDS
# =========================

@bot.tree.command(name="ping", description="Sprawdza, czy publiczny bot działa")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Publiczny bot działa!", ephemeral=True)


@bot.tree.command(name="setup", description="Tworzy kategorię i kanały Kosmicznego Zegara")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    if get_guild_config(guild.id):
        await interaction.response.send_message(
            "⚠️ Zegar jest już ustawiony. Użyj `/refresh` albo zmień miasto komendą `/miasto`.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        category_id, channels = await create_channels(guild)

        guild_data = {
            "city": "Rzeszów",
            "country": "Polska",
            "country_code": "PL",
            "latitude": 50.0413,
            "longitude": 21.9990,
            "timezone": "Europe/Warsaw",
            "category": category_id,
            "channels": channels,
        }

        set_guild_config(guild.id, guild_data)

        await update_all_channels(guild)

        await interaction.followup.send(
            "✅ Kosmiczny Zegar został utworzony i uzupełniony!",
            ephemeral=True
        )

    except Exception as e:
        logging.error(f"Błąd /setup: {e}")
        await interaction.followup.send(
            f"❌ Błąd podczas tworzenia zegara: {e}",
            ephemeral=True
        )


@bot.tree.command(name="miasto", description="Ustawia miasto z całego świata")
@app_commands.describe(
    nazwa="Np. Warszawa, Berlin, Tokyo, New York",
    country_code="Opcjonalnie kod kraju, np. PL, DE, JP, US"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def miasto(interaction: discord.Interaction, nazwa: str, country_code: str | None = None):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        cfg = get_guild_config(guild.id)
        if not cfg:
            await interaction.followup.send("❌ Najpierw użyj `/setup`", ephemeral=True)
            return

        result = await geocode_city(nazwa, country_code)

        if not result:
            await interaction.followup.send("❌ Nie znalazłem takiego miasta.", ephemeral=True)
            return

        cfg["city"] = result["name"]
        cfg["country"] = result["country"]
        cfg["country_code"] = result["country_code"]
        cfg["latitude"] = result["latitude"]
        cfg["longitude"] = result["longitude"]
        cfg["timezone"] = result["timezone"]

        set_guild_config(guild.id, cfg)

        await update_astronomy_channels(guild)
        await update_weather_channels(guild)

        country_part = f", {result['country']}" if result["country"] else ""
        await interaction.followup.send(
            f"🌍 Ustawiono miasto: **{result['name']}{country_part}**",
            ephemeral=True
        )

    except Exception as e:
        logging.error(f"Błąd /miasto: {e}")
        await interaction.followup.send(
            f"❌ Błąd przy ustawianiu miasta: {e}",
            ephemeral=True
        )


@bot.tree.command(name="setcity", description="Ręcznie ustawia miasto po współrzędnych")
@app_commands.checks.has_permissions(manage_guild=True)
async def setcity(
    interaction: discord.Interaction,
    city: str,
    latitude: float,
    longitude: float
):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        cfg = get_guild_config(guild.id)
        if not cfg:
            await interaction.followup.send("❌ Najpierw użyj `/setup`", ephemeral=True)
            return

        cfg["city"] = city
        cfg["country"] = ""
        cfg["country_code"] = ""
        cfg["latitude"] = latitude
        cfg["longitude"] = longitude
        cfg["timezone"] = DEFAULT_TIMEZONE

        set_guild_config(guild.id, cfg)

        await update_weather_channels(guild)

        await interaction.followup.send(
            f"✅ Miasto ustawione ręcznie na **{city}**",
            ephemeral=True
        )

    except Exception as e:
        logging.error(f"Błąd /setcity: {e}")
        await interaction.followup.send(
            f"❌ Błąd przy zmianie miasta: {e}",
            ephemeral=True
        )


@bot.tree.command(name="refresh", description="Natychmiast odświeża wszystkie kanały")
@app_commands.checks.has_permissions(manage_guild=True)
async def refresh(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        cfg = get_guild_config(guild.id)
        if not cfg:
            await interaction.followup.send("❌ Najpierw użyj `/setup`", ephemeral=True)
            return

        await update_all_channels(guild)
        await interaction.followup.send("🔄 Kanały zostały odświeżone.", ephemeral=True)

    except Exception as e:
        logging.error(f"Błąd /refresh: {e}")
        await interaction.followup.send(
            f"❌ Błąd podczas odświeżania: {e}",
            ephemeral=True
        )


@bot.tree.command(name="status", description="Pokazuje aktualne ustawienia zegara")
async def status(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)

    if not cfg:
        await interaction.response.send_message("❌ Zegar nie jest ustawiony", ephemeral=True)
        return

    country = cfg.get("country", "")
    country_part = f", {country}" if country else ""

    embed = discord.Embed(
        title="Kosmiczny Zegar",
        description=f"Miasto: {cfg['city']}{country_part}",
        color=discord.Color.blue()
    )
    embed.add_field(name="Szerokość", value=str(cfg["latitude"]), inline=True)
    embed.add_field(name="Długość", value=str(cfg["longitude"]), inline=True)
    embed.add_field(name="Strefa", value=cfg.get("timezone", DEFAULT_TIMEZONE), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# READY
# =========================

@bot.event
async def on_ready():
    logging.info(f"Zalogowano jako {bot.user}")

    try:
        synced = await bot.tree.sync()
        logging.info(f"Slash commands: {len(synced)}")
    except Exception as e:
        logging.error(f"Błąd synchronizacji slash commands: {e}")

    if not astronomy_loop.is_running():
        astronomy_loop.start()

    if not weather_loop.is_running():
        weather_loop.start()

    if not stats_loop.is_running():
        stats_loop.start()

    if not presence_loop.is_running():
        presence_loop.start()


# =========================
# START
# =========================

if not TOKEN:
    raise ValueError("Brak PUBLIC_DISCORD_TOKEN")

bot.run(TOKEN)
