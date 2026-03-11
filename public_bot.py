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
TIMEZONE = "Europe/Warsaw"
EDIT_DELAY_SECONDS = 15

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

warsaw_tz = ZoneInfo(TIMEZONE)

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


def get_guild_config(guild_id):
    config = load_config()
    return config.get(str(guild_id))


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


def now():
    return datetime.now(warsaw_tz)


# =========================
# FORMATY
# =========================

def format_date(dt):
    dni = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]
    return f"🗓️・{dni[dt.weekday()]} {dt.strftime('%d.%m.%Y')}"


def part_of_day(hour):
    if 5 <= hour < 12:
        return "☀️・Poranek"
    if 12 <= hour < 18:
        return "🌞・Popołudnie"
    if 18 <= hour < 22:
        return "🌆・Wieczór"
    return "🌙・Noc"


def moon_phase(dt):
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
# SAFE EDIT
# =========================

async def edit_channel(channel, name):
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
# WEATHER
# =========================

async def fetch_weather(lat, lon, tz):
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&current=temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure"
        "&daily=sunrise,sunset"
        f"&timezone={tz}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            r.raise_for_status()
            return await r.json()


def parse_weather(data, city):
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
        "temp": f"🌡️・{city} {round(current['temperature_2m'])}°C",
        "feels": f"🥵・Odczuwalna {round(current['apparent_temperature'])}°C",
        "wind": f"💨・Wiatr {round(current['wind_speed_10m'])} km/h",
        "pressure": f"🧭・Ciśnienie {round(current['surface_pressure'])} hPa",
        "sunrise": f"🌅・Wschód {sunrise}",
        "sunset": f"🌇・Zachód {sunset}",
        "rain": rain_text,
    }


# =========================
# UPDATE
# =========================

async def update_guild(guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        logging.warning(f"[{guild.name}] Brak konfiguracji")
        return

    dt = now()

    await edit_channel(get_channel(guild, cfg, "date"), format_date(dt))
    await edit_channel(get_channel(guild, cfg, "day"), part_of_day(dt.hour))
    await edit_channel(get_channel(guild, cfg, "moon"), moon_phase(dt))

    try:
        weather = await fetch_weather(
            cfg["latitude"],
            cfg["longitude"],
            cfg["timezone"],
        )
        w = parse_weather(weather, cfg["city"])
    except Exception as e:
        logging.error(f"[{guild.name}] Błąd pogody: {e}")
        w = None

    if w:
        await edit_channel(get_channel(guild, cfg, "temp"), w["temp"])
        await edit_channel(get_channel(guild, cfg, "feels"), w["feels"])
        await edit_channel(get_channel(guild, cfg, "wind"), w["wind"])
        await edit_channel(get_channel(guild, cfg, "pressure"), w["pressure"])
        await edit_channel(get_channel(guild, cfg, "sunrise"), w["sunrise"])
        await edit_channel(get_channel(guild, cfg, "sunset"), w["sunset"])

        rain_channel = get_channel(guild, cfg, "rain")
        if rain_channel is not None:
            await edit_channel(rain_channel, w["rain"])

    members = len([m for m in guild.members if not m.bot])
    online = len([m for m in guild.members if m.status != discord.Status.offline and not m.bot])
    vc = len([m for m in guild.members if m.voice and not m.bot])

    await edit_channel(get_channel(guild, cfg, "members"), f"👥・Członkowie {members}")
    await edit_channel(get_channel(guild, cfg, "online"), f"🟢・Online {online}")
    await edit_channel(get_channel(guild, cfg, "vc"), f"🎤・Na VC {vc}")


# =========================
# LOOPS
# =========================

@tasks.loop(minutes=20)
async def update_loop():
    logging.info("Uruchomiono update_loop")
    config = load_config()

    for gid in config:
        guild = bot.get_guild(int(gid))
        if guild:
            await update_guild(guild)
        else:
            logging.warning(f"Bot nie widzi serwera o ID: {gid}")


@update_loop.before_loop
async def before_update_loop():
    await bot.wait_until_ready()


@tasks.loop(seconds=10)
async def presence_loop():
    try:
        await bot.change_presence(
            activity=discord.CustomActivity(
                name=f"🕒 {now().strftime('%H:%M:%S')}"
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

@bot.tree.command(name="ping")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Publiczny bot działa!", ephemeral=True)


@bot.tree.command(name="setup")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    category_id, channels = await create_channels(guild)

    config = load_config()
    config[str(guild.id)] = {
        "city": "Rzeszów",
        "latitude": 50.0413,
        "longitude": 21.9990,
        "timezone": "Europe/Warsaw",
        "category": category_id,
        "channels": channels,
    }

    save_config(config)

    await interaction.response.send_message("✅ Kosmiczny Zegar został utworzony!", ephemeral=True)


@bot.tree.command(name="setcity")
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

    config = load_config()

    if str(guild.id) not in config:
        await interaction.response.send_message("Najpierw użyj `/setup`", ephemeral=True)
        return

    config[str(guild.id)]["city"] = city
    config[str(guild.id)]["latitude"] = latitude
    config[str(guild.id)]["longitude"] = longitude

    save_config(config)

    await interaction.response.send_message(f"✅ Miasto ustawione na **{city}**", ephemeral=True)


@bot.tree.command(name="status")
async def status(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)

    if not cfg:
        await interaction.response.send_message("❌ Zegar nie jest ustawiony", ephemeral=True)
        return

    embed = discord.Embed(
        title="Kosmiczny Zegar",
        description=f"Miasto: {cfg['city']}",
        color=discord.Color.blue()
    )

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

    if not update_loop.is_running():
        update_loop.start()

    if not presence_loop.is_running():
        presence_loop.start()


# =========================
# START
# =========================

if not TOKEN:
    raise ValueError("Brak PUBLIC_DISCORD_TOKEN")

bot.run(TOKEN)
