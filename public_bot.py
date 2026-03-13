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
EDIT_DELAY_SECONDS = 5.0
ALERT_CHECK_MINUTES = 10

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

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

last_channel_names = {}
guild_refresh_locks = {}
guild_refresh_tasks = {}
last_alert_signatures = {}
bot_started_at = datetime.now(warsaw_tz)


def now_warsaw() -> datetime:
    return datetime.now(warsaw_tz)


def uptime_text() -> str:
    delta = now_warsaw() - bot_started_at
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m"


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


def get_alert_channel_from_config(guild: discord.Guild, guild_cfg: dict):
    channel_id = guild_cfg.get("alert_text_channel_id")
    if not channel_id:
        return None
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


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
    elif 6 <= hour < 10:
        return "🌄・Poranek"
    elif 10 <= hour < 14:
        return "☀️・Południe"
    elif 14 <= hour < 18:
        return "🌤・Popołudnie"
    elif 18 <= hour < 22:
        return "🌆・Wieczór"
    else:
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
        "&current="
        "temperature_2m,"
        "apparent_temperature,"
        "precipitation,"
        "rain,"
        "showers,"
        "snowfall,"
        "weather_code,"
        "wind_speed_10m,"
        "wind_gusts_10m,"
        "surface_pressure,"
        "visibility"
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
    rain = current.get("rain")
    showers = current.get("showers")
    snowfall = current.get("snowfall")
    wind = current.get("wind_speed_10m")
    wind_gusts = current.get("wind_gusts_10m")
    pressure = current.get("surface_pressure")
    weather_code = current.get("weather_code")
    visibility = current.get("visibility")

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
        "pressure": f"🧭・Ciśnienie {round(float(pressure))} hPa" if pressure is not None else "🧭・Ciśnienie -- hPa",
        "sunrise": f"🌅・Wschód {sunrise_text}",
        "sunset": f"🌇・Zachód {sunset_text}",
        "raw_temp": float(temp) if temp is not None else None,
        "raw_feels_like": float(feels) if feels is not None else None,
        "raw_precipitation": float(precip) if precip is not None else 0.0,
        "raw_rain": float(rain) if rain is not None else 0.0,
        "raw_showers": float(showers) if showers is not None else 0.0,
        "raw_snowfall": float(snowfall) if snowfall is not None else 0.0,
        "raw_wind_speed": float(wind) if wind is not None else 0.0,
        "raw_wind_gusts": float(wind_gusts) if wind_gusts is not None else 0.0,
        "raw_pressure": float(pressure) if pressure is not None else None,
        "raw_weather_code": int(weather_code) if weather_code is not None else None,
        "raw_visibility": float(visibility) if visibility is not None else None,
    }


def detect_weather_alerts(weather: dict) -> list[str]:
    alerts = []

    code = weather.get("raw_weather_code")
    precip = weather.get("raw_precipitation", 0.0) or 0.0
    rain = weather.get("raw_rain", 0.0) or 0.0
    showers = weather.get("raw_showers", 0.0) or 0.0
    snowfall = weather.get("raw_snowfall", 0.0) or 0.0
    gusts = weather.get("raw_wind_gusts", 0.0) or 0.0
    visibility = weather.get("raw_visibility")
    visibility = 999999.0 if visibility is None else visibility

    snow_codes = {71, 73, 75, 77, 85, 86}
    fog_codes = {45, 48}
    thunder_codes = {95, 96, 99}
    hail_codes = {96, 99}
    freezing_codes = {56, 57, 66, 67}
    heavy_rain_codes = {65, 82}

    if code in fog_codes or visibility <= 1000:
        alerts.append("🌫️ Mgła")

    if code in thunder_codes:
        alerts.append("⛈️ Burza")

    if code in hail_codes:
        alerts.append("🧊 Grad")

    if code in freezing_codes:
        alerts.append("🧊 Gołoledź")

    if code in heavy_rain_codes or precip >= 10 or rain >= 10 or showers >= 10:
        alerts.append("🌧️ Ulewa")

    if gusts >= 118:
        alerts.append("🌀 Orkan")
    elif gusts >= 75:
        alerts.append("💨 Wichura")

    if (code in snow_codes or snowfall >= 2.0) and gusts >= 50 and visibility <= 1000:
        alerts.append("🌨️ Zamieć śnieżna")

    if (code in snow_codes or snowfall >= 1.0) and gusts >= 35 and visibility <= 2000:
        alerts.append("❄️ Zawieja śnieżna")

    unique_alerts = []
    seen = set()
    for alert in alerts:
        if alert not in seen:
            unique_alerts.append(alert)
            seen.add(alert)

    return unique_alerts


def build_alert_embed(guild: discord.Guild, guild_cfg: dict, alerts: list[str], weather: dict) -> discord.Embed:
    embed = discord.Embed(
        title="🚨 Alert pogodowy",
        description=f"Miasto: **{guild_cfg.get('city_name', 'Rzeszów')}**",
        color=discord.Color.red()
    )
    embed.add_field(name="Wykryte alerty", value="\n".join(f"• {a}" for a in alerts), inline=False)
    embed.add_field(name="Temperatura", value=weather["temp"], inline=True)
    embed.add_field(name="Odczuwalna", value=weather["feels_like"], inline=True)
    embed.add_field(name="Wiatr", value=weather["wind"], inline=True)
    embed.add_field(name="Opady", value=weather["precip"], inline=True)
    embed.add_field(
        name="Porywy wiatru",
        value=f"🌬️・{round(weather.get('raw_wind_gusts', 0.0))} km/h",
        inline=True
    )
    visibility = weather.get("raw_visibility")
    visibility_text = f"👁️・{round(visibility)} m" if visibility is not None else "👁️・-- m"
    embed.add_field(name="Widzialność", value=visibility_text, inline=True)
    embed.set_footer(text=f"Serwer: {guild.name} • {now_warsaw().strftime('%d.%m.%Y %H:%M:%S')}")
    return embed


def build_alerts_status_embed(guild_cfg: dict, alerts: list[str], weather: dict):
    color = discord.Color.red() if alerts else discord.Color.green()
    embed = discord.Embed(
        title="🚨 Status alertów pogodowych",
        description=f"Miasto: **{guild_cfg.get('city_name', 'Rzeszów')}**",
        color=color
    )

    if alerts:
        embed.add_field(name="Aktywne alerty", value="\n".join(f"• {a}" for a in alerts), inline=False)
    else:
        embed.add_field(name="Aktywne alerty", value="✅ Brak aktywnych alertów", inline=False)

    embed.add_field(name="Temperatura", value=weather["temp"], inline=False)
    embed.add_field(name="Odczuwalna", value=weather["feels_like"], inline=False)
    embed.add_field(name="Opady", value=weather["precip"], inline=False)
    embed.add_field(name="Wiatr", value=weather["wind"], inline=False)
    return embed


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
        overwrites=category.overwrites if category else None
    )


async def create_or_get_text_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str
) -> discord.TextChannel:
    for channel in category.text_channels:
        if channel.name == name:
            return channel

    return await guild.create_text_channel(
        name=name,
        category=category,
        overwrites=category.overwrites if category else None
    )


async def create_setup_for_guild(guild: discord.Guild) -> dict:
    config = load_config()
    guild_key = str(guild.id)

    existing_cfg = config.get(guild_key, {})

    clock_category_id = existing_cfg.get("clock_category_id")
    weather_category_id = existing_cfg.get("weather_category_id")
    stats_category_id = existing_cfg.get("stats_category_id")

    clock_category = None
    weather_category = None
    stats_category = None

    if clock_category_id:
        found = guild.get_channel(clock_category_id)
        if isinstance(found, discord.CategoryChannel):
            clock_category = found

    if weather_category_id:
        found = guild.get_channel(weather_category_id)
        if isinstance(found, discord.CategoryChannel):
            weather_category = found

    if stats_category_id:
        found = guild.get_channel(stats_category_id)
        if isinstance(found, discord.CategoryChannel):
            stats_category = found

    if clock_category is None:
        clock_category = discord.utils.get(guild.categories, name="🛰️ Kosmiczny Zegar")
        if clock_category is None:
            clock_category = await guild.create_category("🛰️ Kosmiczny Zegar")

    if weather_category is None:
        weather_category = discord.utils.get(guild.categories, name="🌤️ Pogoda")
        if weather_category is None:
            weather_category = await guild.create_category("🌤️ Pogoda")

    if stats_category is None:
        stats_category = discord.utils.get(guild.categories, name="📊 Statystyki")
        if stats_category is None:
            stats_category = await guild.create_category("📊 Statystyki")

    channels = {}

    channels["date"] = (await create_or_get_voice_channel(guild, clock_category, "📅・Data")).id
    channels["part_of_day"] = (await create_or_get_voice_channel(guild, clock_category, "🌆・Pora dnia")).id
    channels["moon_phase"] = (await create_or_get_voice_channel(guild, clock_category, "🌙・Faza księżyca")).id
    channels["sunrise"] = (await create_or_get_voice_channel(guild, clock_category, "🌅・Wschód")).id
    channels["sunset"] = (await create_or_get_voice_channel(guild, clock_category, "🌇・Zachód")).id

    channels["temp"] = (await create_or_get_voice_channel(guild, weather_category, "🌡️・Temperatura")).id
    channels["feels_like"] = (await create_or_get_voice_channel(guild, weather_category, "🥵・Odczuwalna")).id
    channels["precip"] = (await create_or_get_voice_channel(guild, weather_category, "☁️・Opady")).id
    channels["wind"] = (await create_or_get_voice_channel(guild, weather_category, "💨・Wiatr")).id
    channels["pressure"] = (await create_or_get_voice_channel(guild, weather_category, "🧭・Ciśnienie")).id

    channels["all_members"] = (await create_or_get_voice_channel(guild, stats_category, "👥・Wszyscy")).id
    channels["users"] = (await create_or_get_voice_channel(guild, stats_category, "🙂・Użytkownicy")).id
    channels["bots"] = (await create_or_get_voice_channel(guild, stats_category, "🤖・Boty")).id
    channels["online"] = (await create_or_get_voice_channel(guild, stats_category, "🟢・Online")).id
    channels["voice"] = (await create_or_get_voice_channel(guild, stats_category, "🎤・Na VC")).id

    alert_text_channel = await create_or_get_text_channel(guild, weather_category, "🚨・alerty-pogodowe")

    config[guild_key] = {
        "city_name": existing_cfg.get("city_name", "Rzeszów"),
        "latitude": existing_cfg.get("latitude", 50.0413),
        "longitude": existing_cfg.get("longitude", 21.9990),
        "timezone": existing_cfg.get("timezone", "Europe/Warsaw"),
        "clock_category_id": clock_category.id,
        "weather_category_id": weather_category.id,
        "stats_category_id": stats_category.id,
        "alert_text_channel_id": alert_text_channel.id,
        "channels": channels
    }

    save_config(config)
    return config[guild_key]


async def update_time_channels_for_guild(guild: discord.Guild, guild_cfg: dict):
    dt = now_warsaw()

    updates = {
        "date": format_polish_date(dt),
        "part_of_day": get_part_of_day(dt.hour),
        "moon_phase": get_moon_phase(dt),
        "sunrise": None,
        "sunset": None,
    }

    latitude = float(guild_cfg.get("latitude", 50.0413))
    longitude = float(guild_cfg.get("longitude", 21.9990))
    timezone_name = guild_cfg.get("timezone", "Europe/Warsaw")

    try:
        data = await fetch_weather(latitude, longitude, timezone_name)
        weather = parse_weather(data, guild_cfg.get("city_name", "Rzeszów"))
        updates["sunrise"] = weather["sunrise"]
        updates["sunset"] = weather["sunset"]
    except Exception as e:
        logging.error(f"Błąd pobierania wschodu/zachodu dla {guild.id}: {e}")

    for key, new_name in updates.items():
        if new_name is None:
            continue
        channel = get_channel_from_config(guild, guild_cfg, key)
        await safe_edit_channel_name(channel, new_name)


async def update_weather_channels_for_guild(guild: discord.Guild, guild_cfg: dict):
    latitude = float(guild_cfg.get("latitude", 50.0413))
    longitude = float(guild_cfg.get("longitude", 21.9990))
    timezone_name = guild_cfg.get("timezone", "Europe/Warsaw")
    city_name = guild_cfg.get("city_name", "Rzeszów")

    data = await fetch_weather(latitude, longitude, timezone_name)
    weather = parse_weather(data, city_name)

    for key in ["temp", "feels_like", "precip", "wind", "pressure"]:
        channel = get_channel_from_config(guild, guild_cfg, key)
        await safe_edit_channel_name(channel, weather[key])


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


async def update_alerts_for_guild(guild: discord.Guild, guild_cfg: dict, force_send: bool = False):
    alert_channel = get_alert_channel_from_config(guild, guild_cfg)
    if alert_channel is None:
        return

    latitude = float(guild_cfg.get("latitude", 50.0413))
    longitude = float(guild_cfg.get("longitude", 21.9990))
    timezone_name = guild_cfg.get("timezone", "Europe/Warsaw")
    city_name = guild_cfg.get("city_name", "Rzeszów")

    try:
        data = await fetch_weather(latitude, longitude, timezone_name)
        weather = parse_weather(data, city_name)
        alerts = detect_weather_alerts(weather)
        signature = "|".join(sorted(alerts))

        previous_signature = last_alert_signatures.get(guild.id, "")

        if alerts:
            if force_send or signature != previous_signature:
                embed = build_alert_embed(guild, guild_cfg, alerts, weather)
                await alert_channel.send(content="🚨 **Wykryto alert pogodowy!**", embed=embed)
                last_alert_signatures[guild.id] = signature
                logging.info(f"[ALERT] {guild.id}: {signature}")
        else:
            if previous_signature:
                await alert_channel.send("✅ Alerty pogodowe wygasły. Obecnie brak aktywnych alertów.")
            last_alert_signatures[guild.id] = ""
    except discord.Forbidden:
        logging.error(f"Brak uprawnień do wysyłania alertów na serwerze {guild.id}")
    except Exception as e:
        logging.error(f"Błąd aktualizacji alertów pogodowych dla {guild.id}: {e}")


async def update_one_guild(guild: discord.Guild):
    guild_cfg = get_guild_config(guild.id)
    if not guild_cfg:
        return

    lock = get_lock(guild.id)
    async with lock:
        await update_time_channels_for_guild(guild, guild_cfg)
        await update_weather_channels_for_guild(guild, guild_cfg)
        await update_server_stats_for_guild(guild, guild_cfg)
        await update_alerts_for_guild(guild, guild_cfg)


async def schedule_quick_refresh(guild: discord.Guild, delay: float = 15.0):
    if guild is None:
        return

    old_task = guild_refresh_tasks.get(guild.id)
    if old_task and not old_task.done():
        old_task.cancel()

    async def delayed():
        try:
            await asyncio.sleep(delay)
            guild_cfg = get_guild_config(guild.id)
            if guild_cfg:
                await update_server_stats_for_guild(guild, guild_cfg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"Błąd szybkiego refresh statystyk dla {guild.id}: {e}")

    guild_refresh_tasks[guild.id] = asyncio.create_task(delayed())


def build_panel_embed(guild: discord.Guild, guild_cfg: dict):
    embed = discord.Embed(
        title="🛰️ Kosmiczny Zegar — Panel",
        description="Panel konfiguracji i szybkiego odświeżania.",
        color=discord.Color.blurple()
    )
    embed.add_field(name="📍 Miasto", value=guild_cfg.get("city_name", "Rzeszów"), inline=True)
    embed.add_field(name="🕒 Strefa", value=guild_cfg.get("timezone", "Europe/Warsaw"), inline=True)
    embed.add_field(name="📡 Serwery bota", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="🧩 Kanały", value=str(len(guild_cfg.get("channels", {}))), inline=True)
    embed.add_field(name="👥 Global users", value=str(sum(g.member_count or 0 for g in bot.guilds)), inline=True)
    embed.add_field(name="⏱ Uptime", value=uptime_text(), inline=True)

    alert_channel = guild.get_channel(guild_cfg.get("alert_text_channel_id", 0))
    embed.add_field(
        name="🚨 Kanał alertów",
        value=alert_channel.mention if isinstance(alert_channel, discord.TextChannel) else "Brak",
        inline=False
    )

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    embed.set_footer(text=f"Serwer: {guild.name}")
    return embed


def build_help_embed():
    embed = discord.Embed(
        title="📘 Kosmiczny Zegar — Pomoc",
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
            "`/panel` — panel z przyciskiem"
        ),
        inline=False
    )
    embed.add_field(
        name="🌍 Informacje",
        value=(
            "`/pogoda` — aktualna pogoda\n"
            "`/czas` — aktualny czas\n"
            "`/ksiezyc` — faza księżyca\n"
            "`/alerty` — aktywne alerty pogodowe"
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
        description=f"Miasto: **{guild_cfg.get('city_name', 'Rzeszów')}**",
        color=discord.Color.teal()
    )
    embed.add_field(name="Temperatura", value=weather["temp"], inline=False)
    embed.add_field(name="Odczuwalna", value=weather["feels_like"], inline=False)
    embed.add_field(name="Opady", value=weather["precip"], inline=False)
    embed.add_field(name="Wiatr", value=weather["wind"], inline=False)
    embed.add_field(name="Ciśnienie", value=weather["pressure"], inline=False)
    embed.add_field(name="Wschód", value=weather["sunrise"], inline=False)
    embed.add_field(name="Zachód", value=weather["sunset"], inline=False)
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


@tasks.loop(minutes=10)
async def time_loop():
    config = load_config()
    for guild_id, guild_cfg in config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_time_channels_for_guild(guild, guild_cfg)


@tasks.loop(minutes=15)
async def weather_loop():
    config = load_config()
    for guild_id, guild_cfg in config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_weather_channels_for_guild(guild, guild_cfg)


@tasks.loop(minutes=3)
async def stats_loop():
    config = load_config()
    for guild_id, guild_cfg in config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_server_stats_for_guild(guild, guild_cfg)


@tasks.loop(minutes=ALERT_CHECK_MINUTES)
async def alerts_loop():
    config = load_config()
    for guild_id, guild_cfg in config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            await update_alerts_for_guild(guild, guild_cfg)


@tasks.loop(seconds=30)
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


@alerts_loop.before_loop
async def before_alerts_loop():
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
        cfg = await create_setup_for_guild(guild)
        await update_one_guild(guild)
        await interaction.followup.send(
            "✅ Utworzono i uporządkowano kategorie:\n"
            "🛰️ Kosmiczny Zegar\n"
            "🌤️ Pogoda\n"
            "📊 Statystyki\n"
            f"🚨 Kanał alertów: <#{cfg['alert_text_channel_id']}>",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send("❌ Bot nie ma wymaganych uprawnień. Potrzebuje `Manage Channels`.", ephemeral=True)
    except Exception as e:
        logging.error(f"Błąd /setup na serwerze {guild.id}: {e}")
        await interaction.followup.send(f"❌ Wystąpił błąd podczas setupu: {e}", ephemeral=True)


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

    alert_channel = guild.get_channel(cfg.get("alert_text_channel_id", 0))

    embed = discord.Embed(title="🛰️ Status Kosmicznego Zegara", color=discord.Color.blue())
    embed.add_field(name="Miasto", value=cfg.get("city_name", "Rzeszów"), inline=True)
    embed.add_field(name="Strefa czasowa", value=cfg.get("timezone", "Europe/Warsaw"), inline=True)
    embed.add_field(name="Kanały", value=str(len(cfg.get("channels", {}))), inline=True)
    embed.add_field(
        name="Kanał alertów",
        value=alert_channel.mention if isinstance(alert_channel, discord.TextChannel) else "Brak",
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

        last_alert_signatures[guild.id] = ""
        await update_one_guild(guild)
        await interaction.followup.send(f"✅ Ustawiono miasto: **{city_display}**", ephemeral=True)
    except Exception as e:
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
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    embed = build_panel_embed(guild, cfg)
    view = RefreshPanelView()
    await interaction.response.send_message(embed=embed, view=view)


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
        data = await fetch_weather(
            float(cfg.get("latitude", 50.0413)),
            float(cfg.get("longitude", 21.9990)),
            cfg.get("timezone", "Europe/Warsaw"),
        )
        weather = parse_weather(data, cfg.get("city_name", "Rzeszów"))
        await interaction.response.send_message(embed=build_weather_embed(cfg, weather), ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd pobierania pogody: {e}", ephemeral=True)


@bot.tree.command(name="alerty", description="Pokazuje aktualne alerty pogodowe")
async def alerts_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Najpierw użyj `/setup`.", ephemeral=True)
        return

    try:
        data = await fetch_weather(
            float(cfg.get("latitude", 50.0413)),
            float(cfg.get("longitude", 21.9990)),
            cfg.get("timezone", "Europe/Warsaw"),
        )
        weather = parse_weather(data, cfg.get("city_name", "Rzeszów"))
        alerts = detect_weather_alerts(weather)
        embed = build_alerts_status_embed(cfg, alerts, weather)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd pobierania alertów: {e}", ephemeral=True)


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
async def common_manage_guild_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("❌ Musisz mieć uprawnienie `Manage Server`.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Musisz mieć uprawnienie `Manage Server`.", ephemeral=True)
    else:
        logging.error(f"Błąd komendy: {error}")


@bot.event
async def on_member_join(member: discord.Member):
    await schedule_quick_refresh(member.guild, delay=15.0)


@bot.event
async def on_member_remove(member: discord.Member):
    await schedule_quick_refresh(member.guild, delay=15.0)


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    if before.channel != after.channel:
        await schedule_quick_refresh(member.guild, delay=12.0)


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    if before.status != after.status:
        await schedule_quick_refresh(after.guild, delay=15.0)


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

    if not alerts_loop.is_running():
        alerts_loop.start()

    if not presence_loop.is_running():
        presence_loop.start()

    for guild in bot.guilds:
        if get_guild_config(guild.id):
            try:
                await update_one_guild(guild)
            except Exception as e:
                logging.error(f"Błąd startowego odświeżenia dla {guild.id}: {e}")


if not TOKEN:
    raise ValueError("Brak PUBLIC_DISCORD_TOKEN w Railway Variables")

bot.run(TOKEN)
