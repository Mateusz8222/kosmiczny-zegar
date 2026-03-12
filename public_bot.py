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
EDIT_DELAY_SECONDS = 1.0

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
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)
last_channel_names = {}
guild_refresh_locks = {}
guild_refresh_tasks = {}


# =========================================================
# POMOCNICZE
# =========================================================

def now_warsaw() -> datetime:
    return datetime.now(warsaw_tz)


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logging.error(f"Błąd odczytu {CONFIG_FILE}: {e}")
        return {}


def save_config(data: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Błąd zapisu {CONFIG_FILE}: {e}")


def get_guild_config(guild_id: int):
    config = load_config()
    return config.get(str(guild_id))


def get_channel_from_config(guild: discord.Guild, guild_cfg: dict, key: str):
    channel_id = guild_cfg.get("channels", {}).get(key)
    if not channel_id:
        return None
    return guild.get_channel(channel_id)


def format_polish_date(dt: datetime) -> str:
    dni = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]
    return f"🗓️・{dni[dt.weekday()]} {dt.strftime('%d.%m.%Y')}"


def get_part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "🌅・Poranek"
    elif 12 <= hour < 18:
        return "🌞・Popołudnie"
    elif 18 <= hour < 22:
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
        1: "🌒・Młody księżyc",
        2: "🌓・I kwadra",
        3: "🌔・Przybywa",
        4: "🌕・Pełnia",
        5: "🌖・Ubywa",
        6: "🌗・III kwadra",
        7: "🌘・Stary księżyc",
    }
    return phases.get(phase_index, "🌙・Księżyc")


def get_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in guild_refresh_locks:
        guild_refresh_locks[guild_id] = asyncio.Lock()
    return guild_refresh_locks[guild_id]


async def safe_edit_channel_name(channel: discord.abc.GuildChannel, new_name: str):
    if channel is None:
        return

    current_name = channel.name

    if current_name == new_name:
        logging.info(f"[SKIP] {channel.id}: bez zmian ('{new_name}')")
        last_channel_names[channel.id] = current_name
        return

    if last_channel_names.get(channel.id) == new_name:
        logging.info(f"[CACHE SKIP] {channel.id}: już ustawione ('{new_name}')")
        return

    try:
        await channel.edit(name=new_name)
        last_channel_names[channel.id] = new_name
        logging.info(f"[EDIT] {channel.id}: '{new_name}'")
        await asyncio.sleep(EDIT_DELAY_SECONDS)
    except discord.Forbidden:
        logging.error(f"Brak uprawnień do zmiany kanału {channel.id}")
    except discord.HTTPException as e:
        logging.error(f"HTTPException dla kanału {channel.id}: {e}")
    except Exception as e:
        logging.error(f"Nieznany błąd dla kanału {channel.id}: {e}")


# =========================================================
# GEO + POGODA
# =========================================================

async def geocode_city(city_name: str):
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={city_name}&count=1&language=pl&format=json"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=20) as response:
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
        "timezone": result.get("timezone", "Europe/Warsaw"),
    }


async def fetch_weather(latitude: float, longitude: float, timezone_name: str):
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}"
        f"&longitude={longitude}"
        "&current=temperature_2m,apparent_temperature,precipitation,wind_speed_10m,surface_pressure"
        "&daily=sunrise,sunset"
        f"&timezone={timezone_name}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=20) as response:
            response.raise_for_status()
            return await response.json()


def parse_weather(data: dict, city_name: str) -> dict:
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
        precip_text = "☁️・Opady --"
    elif float(precip) <= 0:
        precip_text = "☁️・Bez opadów"
    else:
        precip_text = f"🌧️・Opady {round(float(precip), 1)} mm"

    return {
        "temp": f"🌡️・{city_name} {round(float(temp))}°C" if temp is not None else f"🌡️・{city_name} --°C",
        "feels_like": f"🥵・Odczuwalna {round(float(feels))}°C" if feels is not None else "🥵・Odczuwalna --°C",
        "precip": precip_text,
        "wind": f"💨・Wiatr {round(float(wind))} km/h" if wind is not None else "💨・Wiatr -- km/h",
        "pressure": f"⏰・Ciśnienie {round(float(pressure))} hPa" if pressure is not None else "⏰・Ciśnienie -- hPa",
        "sunrise": f"🌄・Wschód {sunrise_text}",
        "sunset": f"🌇・Zachód {sunset_text}",
    }


# =========================================================
# SETUP
# =========================================================

async def create_or_get_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str
) -> discord.VoiceChannel:
    for channel in category.voice_channels:
        if channel.name == name:
            return channel

    return await guild.create_voice_channel(name=name, category=category)


async def create_setup_for_guild(guild: discord.Guild) -> dict:
    config = load_config()
    guild_key = str(guild.id)

    existing_cfg = config.get(guild_key, {})
    category_id = existing_cfg.get("category_id")

    category = None
    if category_id:
        found = guild.get_channel(category_id)
        if isinstance(found, discord.CategoryChannel):
            category = found

    if category is None:
        category = await guild.create_category("🛰️ Kosmiczny Zegar")

    channels = {}
    channels["date"] = (await create_or_get_voice_channel(guild, category, "🗓️・Data")).id
    channels["part_of_day"] = (await create_or_get_voice_channel(guild, category, "🌆・Pora dnia")).id
    channels["moon_phase"] = (await create_or_get_voice_channel(guild, category, "🌙・Faza księżyca")).id

    channels["temp"] = (await create_or_get_voice_channel(guild, category, "🌡️・Temperatura")).id
    channels["feels_like"] = (await create_or_get_voice_channel(guild, category, "🥵・Odczuwalna")).id
    channels["precip"] = (await create_or_get_voice_channel(guild, category, "☁️・Opady")).id
    channels["wind"] = (await create_or_get_voice_channel(guild, category, "💨・Wiatr")).id
    channels["pressure"] = (await create_or_get_voice_channel(guild, category, "⏰・Ciśnienie")).id
    channels["sunrise"] = (await create_or_get_voice_channel(guild, category, "🌄・Wschód")).id
    channels["sunset"] = (await create_or_get_voice_channel(guild, category, "🌇・Zachód")).id

    channels["all_members"] = (await create_or_get_voice_channel(guild, category, "👥・Wszyscy")).id
    channels["members"] = (await create_or_get_voice_channel(guild, category, "👤・Członkowie")).id
    channels["users"] = (await create_or_get_voice_channel(guild, category, "🙂・Użytkownicy")).id
    channels["bots"] = (await create_or_get_voice_channel(guild, category, "🤖・Boty")).id
    channels["online"] = (await create_or_get_voice_channel(guild, category, "🟢・Online")).id
    channels["voice"] = (await create_or_get_voice_channel(guild, category, "🎤・Na VC")).id

    config[guild_key] = {
        "city_name": existing_cfg.get("city_name", "Rzeszów"),
        "latitude": existing_cfg.get("latitude", 50.0413),
        "longitude": existing_cfg.get("longitude", 21.9990),
        "timezone": existing_cfg.get("timezone", "Europe/Warsaw"),
        "category_id": category.id,
        "panel_message_channel_id": existing_cfg.get("panel_message_channel_id"),
        "panel_message_id": existing_cfg.get("panel_message_id"),
        "channels": channels
    }

    save_config(config)
    return config[guild_key]


# =========================================================
# AKTUALIZACJE
# =========================================================

async def update_time_channels_for_guild(guild: discord.Guild, guild_cfg: dict):
    dt = now_warsaw()

    updates = {
        "date": format_polish_date(dt),
        "part_of_day": get_part_of_day(dt.hour),
        "moon_phase": get_moon_phase(dt),
    }

    for key, new_name in updates.items():
        channel = get_channel_from_config(guild, guild_cfg, key)
        await safe_edit_channel_name(channel, new_name)


async def update_weather_channels_for_guild(guild: discord.Guild, guild_cfg: dict):
    latitude = float(guild_cfg.get("latitude", 50.0413))
    longitude = float(guild_cfg.get("longitude", 21.9990))
    timezone_name = guild_cfg.get("timezone", "Europe/Warsaw")
    city_name = guild_cfg.get("city_name", "Rzeszów")

    data = await fetch_weather(latitude, longitude, timezone_name)
    weather = parse_weather(data, city_name)

    for key in ["temp", "feels_like", "precip", "wind", "pressure", "sunrise", "sunset"]:
        channel = get_channel_from_config(guild, guild_cfg, key)
        await safe_edit_channel_name(channel, weather[key])


async def update_server_stats_for_guild(guild: discord.Guild, guild_cfg: dict):
    all_members_count = guild.member_count or 0
    members_count = 0
    users_count = 0
    bots_count = 0
    online_count = 0
    voice_count = 0

    for member in guild.members:
        members_count += 1

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
        "members": f"👤・Członkowie {members_count}",
        "users": f"🙂・Użytkownicy {users_count}",
        "bots": f"🤖・Boty {bots_count}",
        "online": f"🟢・Online {online_count}",
        "voice": f"🎤・Na VC {voice_count}",
    }

    for key, new_name in updates.items():
        channel = get_channel_from_config(guild, guild_cfg, key)
        await safe_edit_channel_name(channel, new_name)


async def update_one_guild(guild: discord.Guild):
    guild_cfg = get_guild_config(guild.id)
    if not guild_cfg:
        return

    lock = get_lock(guild.id)
    async with lock:
        await update_time_channels_for_guild(guild, guild_cfg)
        await update_weather_channels_for_guild(guild, guild_cfg)
        await update_server_stats_for_guild(guild, guild_cfg)


async def schedule_quick_refresh(guild: discord.Guild, delay: float = 3.0):
    if guild is None:
        return

    old_task = guild_refresh_tasks.get(guild.id)
    if old_task and not old_task.done():
        old_task.cancel()

    async def delayed():
        try:
            await asyncio.sleep(delay)
            await update_one_guild(guild)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"Błąd szybkiego refresh dla {guild.id}: {e}")

    guild_refresh_tasks[guild.id] = asyncio.create_task(delayed())


# =========================================================
# PANEL
# =========================================================

def build_panel_embed(guild: discord.Guild, guild_cfg: dict):
    embed = discord.Embed(
        title="🛰️ Kosmiczny Zegar — Panel",
        description="Panel zarządzania i podglądu konfiguracji bota.",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="📍 Miasto",
        value=guild_cfg.get("city_name", "Rzeszów"),
        inline=True
    )
    embed.add_field(
        name="🕒 Strefa",
        value=guild_cfg.get("timezone", "Europe/Warsaw"),
        inline=True
    )
    embed.add_field(
        name="📡 Serwerów bota",
        value=str(len(bot.guilds)),
        inline=True
    )

    embed.add_field(
        name="🧩 Kanały",
        value=str(len(guild_cfg.get("channels", {}))),
        inline=True
    )
    embed.add_field(
        name="👥 Użytkownicy łącznie",
        value=str(sum(g.member_count or 0 for g in bot.guilds)),
        inline=True
    )
    embed.add_field(
        name="🗓️ Ostatni odczyt",
        value=now_warsaw().strftime("%d.%m.%Y %H:%M:%S"),
        inline=True
    )

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    embed.set_footer(text=f"Serwer: {guild.name}")
    return embed


class RefreshPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Odśwież teraz", emoji="🔄", style=discord.ButtonStyle.blurple, custom_id="kosmiczny_refresh_button")
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
            embed = build_panel_embed(guild, cfg)
            try:
                await interaction.message.edit(embed=embed, view=self)
            except Exception:
                pass
            await interaction.followup.send("✅ Kanały zostały odświeżone.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Błąd odświeżania: {e}", ephemeral=True)


# =========================================================
# PĘTLE AUTO
# =========================================================

@tasks.loop(seconds=60)
async def time_loop():
    config = load_config()
    for guild_id, guild_cfg in config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_time_channels_for_guild(guild, guild_cfg)


@tasks.loop(minutes=2)
async def weather_loop():
    config = load_config()
    for guild_id, guild_cfg in config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_weather_channels_for_guild(guild, guild_cfg)


@tasks.loop(seconds=30)
async def stats_loop():
    config = load_config()
    for guild_id, guild_cfg in config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_server_stats_for_guild(guild, guild_cfg)


@tasks.loop(seconds=10)
async def presence_loop():
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.CustomActivity(name=f"🕒 {now_warsaw().strftime('%H:%M:%S')}")
    )


@time_loop.before_loop
async def before_time_loop():
    await bot.wait_until_ready()


@weather_loop.before_loop
async def before_weather_loop():
    await bot.wait_until_ready()


@stats_loop.before_loop
async def before_stats_loop():
    await bot.wait_until_ready()


@presence_loop.before_loop
async def before_presence_loop():
    await bot.wait_until_ready()


# =========================================================
# SLASH COMMANDS
# =========================================================

@bot.tree.command(name="ping", description="Sprawdza czy publiczny bot działa")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Publiczny bot działa!", ephemeral=True)


@bot.tree.command(name="setup", description="Tworzy kategorię i kanały Kosmicznego Zegara")
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
        await interaction.followup.send(
            "✅ Kosmiczny Zegar został utworzony i skonfigurowany.",
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
        await interaction.response.send_message(
            "ℹ️ Ten serwer nie ma jeszcze konfiguracji. Użyj `/setup`.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🛰️ Status Kosmicznego Zegara",
        color=discord.Color.blue()
    )
    embed.add_field(name="Miasto", value=cfg.get("city_name", "Rzeszów"), inline=True)
    embed.add_field(name="Strefa czasowa", value=cfg.get("timezone", "Europe/Warsaw"), inline=True)
    embed.add_field(name="Kanały", value=str(len(cfg.get("channels", {}))), inline=True)

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
        await interaction.followup.send("✅ Wszystkie kanały zostały odświeżone.", ephemeral=True)
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

        config = load_config()
        guild_key = str(guild.id)

        city_display = result["name"]
        if result.get("country"):
            city_display = f'{result["name"]}, {result["country"]}'

        config[guild_key]["city_name"] = city_display
        config[guild_key]["latitude"] = result["latitude"]
        config[guild_key]["longitude"] = result["longitude"]
        config[guild_key]["timezone"] = result["timezone"]
        save_config(config)

        await update_one_guild(guild)

        await interaction.followup.send(
            f"✅ Ustawiono miasto: **{city_display}**",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd ustawiania miasta: {e}", ephemeral=True)


@bot.tree.command(name="botstats", description="Pokazuje statystyki publicznego bota")
async def botstats(interaction: discord.Interaction):
    servers = len(bot.guilds)
    users = sum(g.member_count or 0 for g in bot.guilds)

    embed = discord.Embed(
        title="📊 Statystyki bota",
        color=discord.Color.blue()
    )
    embed.add_field(name="🌍 Serwery", value=str(servers), inline=False)
    embed.add_field(name="👥 Łącznie użytkowników", value=str(users), inline=False)
    embed.add_field(name="🤖 Bot", value=str(bot.user), inline=False)
    embed.add_field(name="🕒 Czas", value=now_warsaw().strftime("%H:%M:%S"), inline=False)

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="invite", description="Link do dodania bota")
async def invite_bot(interaction: discord.Interaction):
    link = "https://discord.com/oauth2/authorize?client_id=1481070169077055548&permissions=2147568640&scope=bot%20applications.commands"

    embed = discord.Embed(
        title="➕ Dodaj Kosmiczny Zegar",
        description=f"[Kliknij tutaj, aby dodać bota]({link})",
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="panel", description="Pokazuje panel Kosmicznego Zegara")
@app_commands.checks.has_permissions(manage_guild=True)
async def panel_clock(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    embed = build_panel_embed(guild, cfg)
    view = RefreshPanelView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)


# =========================================================
# BŁĘDY
# =========================================================

@setup_clock.error
@refresh_clock.error
@city_clock.error
@panel_clock.error
async def common_manage_guild_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("❌ Musisz mieć uprawnienie `Manage Server`.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Musisz mieć uprawnienie `Manage Server`.", ephemeral=True)
    else:
        logging.error(f"Błąd komendy: {error}")


# =========================================================
# EVENTY NATYCHMIASTOWEGO ODŚWIEŻANIA
# =========================================================

@bot.event
async def on_member_join(member: discord.Member):
    await schedule_quick_refresh(member.guild)


@bot.event
async def on_member_remove(member: discord.Member):
    await schedule_quick_refresh(member.guild)


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    await schedule_quick_refresh(member.guild)


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    if before.status != after.status:
        await schedule_quick_refresh(after.guild)


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():
    logging.info(f"Zalogowano jako {bot.user}")

    try:
        synced = await bot.tree.sync()
        logging.info(f"Zsynchronizowano {len(synced)} komend slash")
    except Exception as e:
        logging.error(f"Błąd sync komend: {e}")

    try:
        bot.add_view(RefreshPanelView())
        logging.info("Zarejestrowano persistent view")
    except Exception as e:
        logging.error(f"Błąd rejestracji view: {e}")

    if not time_loop.is_running():
        time_loop.start()

    if not weather_loop.is_running():
        weather_loop.start()

    if not stats_loop.is_running():
        stats_loop.start()

    if not presence_loop.is_running():
        presence_loop.start()

    for guild in bot.guilds:
        if get_guild_config(guild.id):
            try:
                await update_one_guild(guild)
            except Exception as e:
                logging.error(f"Błąd startowego odświeżenia dla {guild.id}: {e}")


# =========================================================
# PREFIX
# =========================================================

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Publiczny bot działa!")


# =========================================================
# START
# =========================================================

if not TOKEN:
    raise ValueError("Brak PUBLIC_DISCORD_TOKEN w Railway Variables")

bot.run(TOKEN)
