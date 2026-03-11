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
EDIT_DELAY_SECONDS = 6

GEOCODING_API = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

warsaw_tz = ZoneInfo(TIMEZONE)

# =========================
# INTENTS
# =========================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True
intents.message_content = True

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


def set_guild_config(guild_id, data):
    config = load_config()
    config[str(guild_id)] = data
    save_config(config)


def now():
    return datetime.now(warsaw_tz)


def get_channel(guild, cfg, key):
    channels = cfg.get("channels", {})
    cid = channels.get(key)

    if not cid:
        return None

    return guild.get_channel(cid)


# =========================
# FORMAT
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

    phases = [
        "🌑・Nów",
        "🌒・Młody księżyc",
        "🌓・I kwadra",
        "🌔・Przybywa",
        "🌕・Pełnia",
        "🌖・Ubywa",
        "🌗・III kwadra",
        "🌘・Stary księżyc",
    ]

    return phases[phase]


def country_code_to_flag(code):
    if not code:
        return ""

    code = code.upper()

    return "".join(chr(ord(c) + 127397) for c in code)


# =========================
# HTTP
# =========================

async def fetch_json(url, params):

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as r:

            r.raise_for_status()

            return await r.json()


async def geocode_city(name):

    params = {
        "name": name,
        "count": 1,
        "language": "pl",
        "format": "json",
    }

    data = await fetch_json(GEOCODING_API, params)

    if not data.get("results"):
        return None

    r = data["results"][0]

    return {
        "name": r["name"],
        "country": r.get("country"),
        "country_code": r.get("country_code"),
        "latitude": r["latitude"],
        "longitude": r["longitude"],
        "timezone": r.get("timezone", TIMEZONE),
    }


async def fetch_weather(lat, lon, tz):

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure",
        "daily": "sunrise,sunset",
        "timezone": tz,
    }

    return await fetch_json(WEATHER_API, params)


# =========================
# CHANNEL EDIT
# =========================

async def edit_channel(channel, name):

    if not channel:
        return

    if channel.name == name:
        return

    try:
        await channel.edit(name=name)

        await asyncio.sleep(EDIT_DELAY_SECONDS)

    except:
        pass


# =========================
# UPDATE
# =========================

async def update_astronomy(guild):

    cfg = get_guild_config(guild.id)

    if not cfg:
        return

    dt = now()

    await edit_channel(get_channel(guild, cfg, "date"), format_date(dt))
    await edit_channel(get_channel(guild, cfg, "day"), part_of_day(dt.hour))
    await edit_channel(get_channel(guild, cfg, "moon"), moon_phase(dt))


async def update_weather(guild):

    cfg = get_guild_config(guild.id)

    if not cfg:
        return

    data = await fetch_weather(
        cfg["latitude"],
        cfg["longitude"],
        cfg["timezone"],
    )

    flag = country_code_to_flag(cfg.get("country_code"))

    city = cfg["city"]

    if flag:
        city = f"{city} {flag}"

    current = data["current"]
    daily = data["daily"]

    sunrise = daily["sunrise"][0].split("T")[1][:5]
    sunset = daily["sunset"][0].split("T")[1][:5]

    await edit_channel(
        get_channel(guild, cfg, "temp"),
        f"🌡️・{city} {round(current['temperature_2m'])}°C"
    )

    await edit_channel(
        get_channel(guild, cfg, "feels"),
        f"🥵・Odczuwalna {round(current['apparent_temperature'])}°C"
    )

    rain = current.get("precipitation", 0)

    if rain > 0:
        rain_text = f"🌧️・Opady {rain} mm"
    else:
        rain_text = "☁️・Bez opadów"

    await edit_channel(get_channel(guild, cfg, "rain"), rain_text)

    await edit_channel(
        get_channel(guild, cfg, "wind"),
        f"💨・Wiatr {round(current['wind_speed_10m'])} km/h"
    )

    await edit_channel(
        get_channel(guild, cfg, "pressure"),
        f"🧭・Ciśnienie {round(current['surface_pressure'])} hPa"
    )

    await edit_channel(
        get_channel(guild, cfg, "sunrise"),
        f"🌅・Wschód {sunrise}"
    )

    await edit_channel(
        get_channel(guild, cfg, "sunset"),
        f"🌇・Zachód {sunset}"
    )


async def update_stats(guild):

    cfg = get_guild_config(guild.id)

    if not cfg:
        return

    all_members = guild.member_count or len(guild.members)

    users = len([m for m in guild.members if not m.bot])

    bots = len([m for m in guild.members if m.bot])

    online = len([
        m for m in guild.members
        if m.status != discord.Status.offline
    ])

    vc = len([
        m for m in guild.members
        if m.voice
    ])

    await edit_channel(
        get_channel(guild, cfg, "members_all"),
        f"👥・Wszyscy {all_members}"
    )

    await edit_channel(
        get_channel(guild, cfg, "members_users"),
        f"🙂・Użytkownicy {users}"
    )

    await edit_channel(
        get_channel(guild, cfg, "members_bots"),
        f"🤖・Boty {bots}"
    )

    await edit_channel(
        get_channel(guild, cfg, "online"),
        f"🟢・Online {online}"
    )

    await edit_channel(
        get_channel(guild, cfg, "vc"),
        f"🎤・Na VC {vc}"
    )


async def update_all(guild):

    await update_astronomy(guild)
    await update_weather(guild)
    await update_stats(guild)


# =========================
# LOOPS
# =========================

@tasks.loop(minutes=10)
async def update_loop():

    config = load_config()

    for gid in config:

        guild = bot.get_guild(int(gid))

        if guild:
            await update_all(guild)


@update_loop.before_loop
async def before_loop():

    await bot.wait_until_ready()


@tasks.loop(seconds=10)
async def presence_loop():

    await bot.change_presence(
        activity=discord.CustomActivity(
            name=f"🕒 {now().strftime('%H:%M:%S')}"
        )
    )


@presence_loop.before_loop
async def before_presence():

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

        "members_all": "👥・Wszyscy",
        "members_users": "🙂・Użytkownicy",
        "members_bots": "🤖・Boty",
        "online": "🟢・Online",
        "vc": "🎤・Na VC",
    }

    for key, name in names.items():

        c = await guild.create_voice_channel(name, category=category)

        channels[key] = c.id

    return category.id, channels


# =========================
# COMMANDS
# =========================

@bot.tree.command(name="setup")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction):

    guild = interaction.guild

    await interaction.response.defer(ephemeral=True)

    category_id, channels = await create_channels(guild)

    data = {
        "city": "Rzeszów",
        "country": "Polska",
        "country_code": "PL",
        "latitude": 50.0413,
        "longitude": 21.9990,
        "timezone": TIMEZONE,
        "category": category_id,
        "channels": channels,
    }

    set_guild_config(guild.id, data)

    await update_all(guild)

    await interaction.followup.send(
        "✅ Kosmiczny Zegar utworzony!",
        ephemeral=True
    )


@bot.tree.command(name="miasto")
async def miasto(interaction: discord.Interaction, nazwa: str):

    guild = interaction.guild

    await interaction.response.defer(ephemeral=True)

    result = await geocode_city(nazwa)

    if not result:
        await interaction.followup.send(
            "❌ Nie znaleziono miasta",
            ephemeral=True
        )
        return

    cfg = get_guild_config(guild.id)

    cfg.update(result)

    set_guild_config(guild.id, cfg)

    await update_all(guild)

    await interaction.followup.send(
        f"🌍 Ustawiono miasto: {result['name']}",
        ephemeral=True
    )


@bot.tree.command(name="refresh")
async def refresh(interaction: discord.Interaction):

    guild = interaction.guild

    await interaction.response.defer(ephemeral=True)

    await update_all(guild)

    await interaction.followup.send(
        "🔄 Kanały odświeżone",
        ephemeral=True
    )


# =========================
# READY
# =========================

@bot.event
async def on_ready():

    logging.info(f"Zalogowano jako {bot.user}")

    try:
        synced = await bot.tree.sync()

        logging.info(f"Slash commands: {len(synced)}")

    except:
        pass

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
