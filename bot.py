import os
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import commands, tasks
from discord.ui import View
from dotenv import load_dotenv


# =========================
# ŁADOWANIE .ENV / VARIABLES
# =========================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "").strip()
TIMEZONE = os.getenv("TIMEZONE", "Europe/Warsaw").strip()

PANEL_CHANNEL_ID_RAW = os.getenv("PANEL_CHANNEL_ID", "").strip()
PANEL_CHANNEL_ID = int(PANEL_CHANNEL_ID_RAW) if PANEL_CHANNEL_ID_RAW.isdigit() else 0

CHANNEL_DATE_ID = int(os.getenv("CHANNEL_DATE_ID", "0"))
CHANNEL_GREETING_ID = int(os.getenv("CHANNEL_GREETING_ID", "0"))
CHANNEL_MOON_ID = int(os.getenv("CHANNEL_MOON_ID", "0"))
CHANNEL_TEMP_ID = int(os.getenv("CHANNEL_TEMP_ID", "0"))
CHANNEL_FEELS_LIKE_ID = int(os.getenv("CHANNEL_FEELS_LIKE_ID", "0"))
CHANNEL_PRECIP_ID = int(os.getenv("CHANNEL_PRECIP_ID", "0"))
CHANNEL_WIND_ID = int(os.getenv("CHANNEL_WIND_ID", "0"))
CHANNEL_PRESSURE_ID = int(os.getenv("CHANNEL_PRESSURE_ID", "0"))
CHANNEL_SUNRISE_ID = int(os.getenv("CHANNEL_SUNRISE_ID", "0"))
CHANNEL_SUNSET_ID = int(os.getenv("CHANNEL_SUNSET_ID", "0"))

CHANNEL_MEMBERS_ID = int(os.getenv("CHANNEL_MEMBERS_ID", "0"))
CHANNEL_ONLINE_ID = int(os.getenv("CHANNEL_ONLINE_ID", "0"))
CHANNEL_VC_ID = int(os.getenv("CHANNEL_VC_ID", "0"))

CITY_NAME = "Rzeszów"
LAT = 50.0413
LON = 21.9990

if not DISCORD_TOKEN:
    raise ValueError("Brakuje DISCORD_TOKEN w zmiennych środowiskowych")


# =========================
# BOT
# =========================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

panel_message = None

weather_cache = {
    "temp": None,
    "feels_like": None,
    "wind": None,
    "pressure": None,
    "sunrise": "--:--",
    "sunset": "--:--",
    "rain_1h": 0.0,
    "snow_1h": 0.0,
    "weather_id": None,
    "precip_text": "🌤️ | Bez opadów",
}


# =========================
# FUNKCJE POMOCNICZE
# =========================
def get_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def get_polish_weekday(dt: datetime) -> str:
    dni = ["Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Nie"]
    return dni[dt.weekday()]


def get_greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "🌅 | Poranek"
    if 12 <= hour < 18:
        return "☀️ | Popołudnie"
    if 18 <= hour < 22:
        return "🌇 | Wieczór"
    return "🌙 | Noc"


def get_moon_phase_name(dt: datetime) -> str:
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
    frac = jd - int(jd)
    phase_index = round(frac * 8) % 8

    phases = {
        0: "🌑 | Nów",
        1: "🌒 | Przybywający",
        2: "🌓 | I kwadra",
        3: "🌔 | Garbaty przybywający",
        4: "🌕 | Pełnia",
        5: "🌖 | Garbaty ubywający",
        6: "🌗 | III kwadra",
        7: "🌘 | Ubywający",
    }
    return phases.get(phase_index, "🌙 | Faza Księżyca")


def build_precip_text(
    weather_id: int | None,
    rain_1h: float,
    snow_1h: float,
    wind_kmh: float,
    temp_c: float | None,
) -> str:
    rain_1h = float(rain_1h or 0.0)
    snow_1h = float(snow_1h or 0.0)
    wind_kmh = float(wind_kmh or 0.0)
    total = round(rain_1h + snow_1h, 1)

    if weather_id == 511:
        return "🚨 | Oblodzenie"

    if temp_c is not None and temp_c <= 0 and (rain_1h > 0 or snow_1h > 0):
        return "🚨 | Oblodzenie"

    if weather_id in [202, 212, 221, 232]:
        return f"🚨 | Silna burza {total:.1f} mm"

    if weather_id is not None and 200 <= weather_id <= 299:
        return f"⚠️ | Burza {total:.1f} mm"

    if weather_id == 781:
        return "🚨 | Alert: groźne zjawisko"

    if weather_id == 741:
        return "⚠️ | Alert: gęsta mgła"

    if wind_kmh >= 60:
        return "🚨 | Alert: bardzo silny wiatr"

    if wind_kmh >= 40:
        return "⚠️ | Alert: silny wiatr"

    if weather_id in [502, 503, 504, 522]:
        return f"⚠️ | Alert: ulewa {rain_1h:.1f} mm"

    if temp_c is not None and temp_c <= -5:
        return "❄️ | Silny mróz"

    if temp_c is not None and temp_c <= 0:
        return "⚠️ | Przymrozek"

    if snow_1h > 0:
        return f"❄️ | Śnieg {snow_1h:.1f} mm"

    if rain_1h > 0:
        return f"🌧️ | Deszcz {rain_1h:.1f} mm"

    if weather_id in [701, 711, 721]:
        return "🌫️ | Mgła"

    return "🌤️ | Bez opadów"


async def fetch_weather() -> dict:
    default_data = {
        "temp": None,
        "feels_like": None,
        "wind": None,
        "pressure": None,
        "sunrise": "--:--",
        "sunset": "--:--",
        "rain_1h": 0.0,
        "snow_1h": 0.0,
        "weather_id": None,
        "precip_text": "🌤️ | Bez opadów",
    }

    if not OPENWEATHER_API_KEY:
        print("[INFO] Brak OPENWEATHER_API_KEY - pogoda wyłączona.")
        return default_data

    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?lat={LAT}&lon={LON}&appid={OPENWEATHER_API_KEY}&units=metric&lang=pl"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                if response.status != 200:
                    text = await response.text()
                    print(f"[BŁĄD] OpenWeather HTTP {response.status}: {text}")
                    return default_data

                data = await response.json()

        main_data = data.get("main", {})
        wind_data = data.get("wind", {})
        sys_data = data.get("sys", {})
        rain_data = data.get("rain", {})
        snow_data = data.get("snow", {})
        weather_list = data.get("weather", [])

        temp = main_data.get("temp")
        feels_like = main_data.get("feels_like")
        pressure = main_data.get("pressure")
        wind_speed = wind_data.get("speed")

        temp_int = round(temp) if temp is not None else None
        feels_like_int = round(feels_like) if feels_like is not None else None
        pressure_int = round(pressure) if pressure is not None else None
        wind_kmh = round((wind_speed or 0) * 3.6) if wind_speed is not None else None

        rain_1h = float(rain_data.get("1h", 0.0) or 0.0)
        snow_1h = float(snow_data.get("1h", 0.0) or 0.0)
        weather_id = weather_list[0].get("id") if weather_list else None

        precip_text = build_precip_text(
            weather_id=weather_id,
            rain_1h=rain_1h,
            snow_1h=snow_1h,
            wind_kmh=wind_kmh or 0.0,
            temp_c=temp_int,
        )

        sunrise_ts = sys_data.get("sunrise")
        sunset_ts = sys_data.get("sunset")

        sunrise_str = "--:--"
        sunset_str = "--:--"

        if sunrise_ts:
            sunrise_dt = datetime.fromtimestamp(sunrise_ts, ZoneInfo(TIMEZONE))
            sunrise_str = sunrise_dt.strftime("%H:%M")

        if sunset_ts:
            sunset_dt = datetime.fromtimestamp(sunset_ts, ZoneInfo(TIMEZONE))
            sunset_str = sunset_dt.strftime("%H:%M")

        return {
            "temp": temp_int,
            "feels_like": feels_like_int,
            "wind": wind_kmh,
            "pressure": pressure_int,
            "sunrise": sunrise_str,
            "sunset": sunset_str,
            "rain_1h": rain_1h,
            "snow_1h": snow_1h,
            "weather_id": weather_id,
            "precip_text": precip_text,
        }

    except Exception as e:
        print(f"[BŁĄD] Nie udało się pobrać pogody: {e}")
        return default_data


async def refresh_weather_cache():
    global weather_cache
    weather_cache = await fetch_weather()


async def safe_edit_channel_name(channel_id: int, new_name: str):
    if not channel_id:
        return

    channel = bot.get_channel(channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None

    if channel is None:
        print(f"[BŁĄD] Nie znaleziono kanału o ID: {channel_id}")
        return

    try:
        if channel.name != new_name:
            old_name = channel.name
            await channel.edit(name=new_name)
            print(f"[EDIT] {channel_id}: '{old_name}' -> '{new_name}'")
        else:
            print(f"[SKIP] {channel_id}: bez zmian ('{new_name}')")
    except discord.Forbidden:
        print(f"[BŁĄD] Brak uprawnień do edycji kanału: {channel_id}")
    except discord.HTTPException as e:
        print(f"[BŁĄD] Nie udało się zmienić nazwy kanału {channel_id}: {e}")
    except Exception as e:
        print(f"[BŁĄD] Nieoczekiwany problem przy zmianie kanału {channel_id}: {e}")


# =========================
# PANEL TEKSTOWY
# =========================
def build_panel_embed(weather: dict) -> discord.Embed:
    now = get_now()
    weekday = get_polish_weekday(now)
    date_text = now.strftime("%d.%m.%Y")
    time_text = now.strftime("%H:%M:%S")

    temp_text = f"{weather['temp']}°C" if weather["temp"] is not None else "--°C"
    feels_like_text = f"{weather['feels_like']}°C" if weather["feels_like"] is not None else "--°C"
    wind_text = f"{weather['wind']} km/h" if weather["wind"] is not None else "-- km/h"
    pressure_text = f"{weather['pressure']} hPa" if weather["pressure"] is not None else "-- hPa"
    sunrise_text = weather["sunrise"]
    sunset_text = weather["sunset"]
    precip_text = weather.get("precip_text", "🌤️ | Bez opadów").replace("| ", "")

    embed = discord.Embed(
        title=f"📅 {weekday} • {date_text}",
        description=(
            f"🕒 **{time_text}**\n\n"
            f"🌍 **{TIMEZONE}**\n"
            f"🌤 **{CITY_NAME} • {temp_text}**\n"
            f"🌡 **Odczuwalna:** {feels_like_text}\n"
            f"🚨 **Stan:** {precip_text}\n"
            f"🧭 **Ciśnienie:** {pressure_text}\n"
            f"💨 **Wiatr:** {wind_text}\n"
            f"🌅 **Wschód:** {sunrise_text}\n"
            f"🌇 **Zachód:** {sunset_text}\n\n"
            f"Kosmiczny Zegar 24 • Mati"
        ),
        color=discord.Color.blue()
    )

    return embed


class RefreshView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Odśwież teraz",
        style=discord.ButtonStyle.primary,
        emoji="🔄",
        custom_id="refresh_panel_button"
    )
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await refresh_all(force_weather=True)

        if interaction.response.is_done():
            await interaction.followup.send(
                "✅ Panel i kanały zostały odświeżone.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "✅ Panel i kanały zostały odświeżone.",
                ephemeral=True
            )


async def find_existing_panel_message(channel: discord.TextChannel):
    try:
        async for msg in channel.history(limit=30):
            if msg.author == bot.user:
                return msg
    except discord.Forbidden:
        print("[BŁĄD] Brak uprawnień do czytania historii wiadomości na kanale panelu.")
    except discord.HTTPException as e:
        print(f"[BŁĄD] Nie udało się pobrać historii kanału panelu: {e}")
    return None


async def update_or_create_panel_message(weather: dict):
    global panel_message

    if PANEL_CHANNEL_ID == 0:
        return

    channel = bot.get_channel(PANEL_CHANNEL_ID)

    if channel is None:
        print("[BŁĄD] PANEL_CHANNEL_ID nie wskazuje na istniejący kanał.")
        return

    if not isinstance(channel, discord.TextChannel):
        print("[BŁĄD] PANEL_CHANNEL_ID nie wskazuje na kanał tekstowy.")
        return

    embed = build_panel_embed(weather)
    view = RefreshView()

    try:
        if panel_message is None:
            panel_message = await find_existing_panel_message(channel)

        if panel_message:
            await panel_message.edit(embed=embed, view=view)
        else:
            panel_message = await channel.send(embed=embed, view=view)

    except discord.Forbidden:
        print("[BŁĄD] Bot nie ma uprawnień do wysyłania lub edycji wiadomości na kanale panelu.")
    except discord.HTTPException as e:
        print(f"[BŁĄD] Nie udało się zaktualizować panelu: {e}")


# =========================
# KANAŁY GŁOSOWE INFORMACYJNE
# =========================
async def update_voice_channels(weather: dict):
    now = get_now()
    weekday = get_polish_weekday(now)

    date_name = f"📅 | {weekday} • {now.strftime('%d.%m.%Y')}"
    greeting_name = get_greeting(now.hour)
    moon_name = get_moon_phase_name(now)

    temp_name = (
        f"🌤️ | {CITY_NAME} {weather['temp']}°C"
        if weather["temp"] is not None
        else f"🌤️ | {CITY_NAME} --°C"
    )

    feels_like_name = (
        f"🌡️ | Odczuwalna {weather['feels_like']}°C"
        if weather["feels_like"] is not None
        else "🌡️ | Odczuwalna --°C"
    )

    precip_name = weather.get("precip_text", "🌤️ | Bez opadów")

    wind_name = (
        f"🌬️ | Wiatr {weather['wind']} km/h"
        if weather["wind"] is not None
        else "🌬️ | Wiatr -- km/h"
    )

    pressure_name = (
        f"🧭 | Ciśnienie {weather['pressure']} hPa"
        if weather["pressure"] is not None
        else "🧭 | Ciśnienie -- hPa"
    )

    sunrise_name = f"🌅 | Wschód {weather['sunrise']}"
    sunset_name = f"🌇 | Zachód {weather['sunset']}"

    await safe_edit_channel_name(CHANNEL_DATE_ID, date_name)
    await safe_edit_channel_name(CHANNEL_GREETING_ID, greeting_name)
    await safe_edit_channel_name(CHANNEL_MOON_ID, moon_name)
    await safe_edit_channel_name(CHANNEL_TEMP_ID, temp_name)
    await safe_edit_channel_name(CHANNEL_FEELS_LIKE_ID, feels_like_name)
    await safe_edit_channel_name(CHANNEL_PRECIP_ID, precip_name)
    await safe_edit_channel_name(CHANNEL_WIND_ID, wind_name)
    await safe_edit_channel_name(CHANNEL_PRESSURE_ID, pressure_name)
    await safe_edit_channel_name(CHANNEL_SUNRISE_ID, sunrise_name)
    await safe_edit_channel_name(CHANNEL_SUNSET_ID, sunset_name)

    print("[INFO] Kanały głosowe zostały odświeżone.")


# =========================
# STATYSTYKI SERWERA
# =========================
async def update_server_stats():
    for guild in bot.guilds:
        members_total = guild.member_count or 0

        online_count = sum(
            1 for member in guild.members
            if not member.bot and member.status != discord.Status.offline
        )

        vc_count = sum(
            1 for member in guild.members
            if not member.bot and member.voice and member.voice.channel is not None
        )

        await safe_edit_channel_name(CHANNEL_MEMBERS_ID, f"👥 Członkowie • {members_total}")
        await safe_edit_channel_name(CHANNEL_ONLINE_ID, f"🟢 Online • {online_count}")
        await safe_edit_channel_name(CHANNEL_VC_ID, f"🎤 Na VC • {vc_count}")

        print("[INFO] Statystyki serwera zostały odświeżone.")
        break


# =========================
# STATUS BOTA NA PASKU
# =========================
async def update_bot_clock_status():
    now = get_now()
    time_text = now.strftime("%H:%M:%S")

    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"🕒 {time_text}"
            )
        )
    except Exception as e:
        print(f"[BŁĄD] Nie udało się ustawić statusu bota: {e}")


# =========================
# GŁÓWNE ODŚWIEŻANIE
# =========================
async def refresh_all(force_weather: bool = False):
    if force_weather:
        await refresh_weather_cache()

    await update_voice_channels(weather_cache)
    await update_server_stats()
    # await update_or_create_panel_message(weather_cache)


async def refresh_panel_only():
    await update_or_create_panel_message(weather_cache)


# =========================
# PĘTLE
# =========================
@tasks.loop(seconds=5)
async def panel_clock_loop():
    await refresh_panel_only()


@panel_clock_loop.before_loop
async def before_panel_clock_loop():
    await bot.wait_until_ready()


@tasks.loop(seconds=60)
async def channels_refresh_loop():
    try:
        print(f"[LOOP] Start odświeżania: {get_now().strftime('%d.%m.%Y %H:%M:%S')}")
        await refresh_weather_cache()
        await update_voice_channels(weather_cache)
        await update_server_stats()
        print("[LOOP] Odświeżanie zakończone poprawnie")
    except Exception as e:
        print(f"[BŁĄD LOOP] {e}")


@channels_refresh_loop.before_loop
async def before_channels_refresh_loop():
    await bot.wait_until_ready()


@channels_refresh_loop.error
async def channels_refresh_loop_error(error):
    print(f"[BŁĄD KRYTYCZNY LOOP] {error}")


@tasks.loop(seconds=15)
async def bot_status_loop():
    try:
        await update_bot_clock_status()
    except Exception as e:
        print(f"[BŁĄD STATUS LOOP] {e}")


@bot_status_loop.before_loop
async def before_bot_status_loop():
    await bot.wait_until_ready()


@bot_status_loop.error
async def bot_status_loop_error(error):
    print(f"[BŁĄD KRYTYCZNY STATUS LOOP] {error}")


# =========================
# READY
# =========================
@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")
    print("[READY] Bot gotowy")
    print(f"[READY] channels_refresh_loop działa? {channels_refresh_loop.is_running()}")
    print(f"[READY] bot_status_loop działa? {bot_status_loop.is_running()}")

    try:
        bot.add_view(RefreshView())
        print("[INFO] Zarejestrowano persistent view")
    except Exception as e:
        print(f"[BŁĄD] Nie udało się zarejestrować persistent view: {e}")

    try:
        await refresh_weather_cache()
        await update_voice_channels(weather_cache)
        await update_server_stats()
        await update_bot_clock_status()
        print("[READY] Pierwsze odświeżenie zakończone")
    except Exception as e:
        print(f"[BŁĄD READY] {e}")

    # if not panel_clock_loop.is_running():
    #     panel_clock_loop.start()

    if not channels_refresh_loop.is_running():
        channels_refresh_loop.start()
        print("[READY] Uruchomiono channels_refresh_loop")

    if not bot_status_loop.is_running():
        bot_status_loop.start()
        print("[READY] Uruchomiono bot_status_loop")


# =========================
# KOMENDY TESTOWE
# =========================
@bot.command()
async def testclock(ctx):
    channel = bot.get_channel(CHANNEL_DATE_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_DATE_ID)
        except Exception:
            channel = None

    if channel is None:
        await ctx.send("❌ Nie znaleziono kanału daty.")
        print("❌ Nie znaleziono kanału daty")
        return

    test_name = f"📅 | TEST • {get_now().strftime('%d.%m.%Y')}"
    await channel.edit(name=test_name)
    await ctx.send("✅ Zmieniono nazwę kanału daty")
    print(f"✅ Kanał daty zmieniony na: {test_name}")


@bot.command()
async def refreshnow(ctx):
    try:
        await refresh_weather_cache()
        await update_voice_channels(weather_cache)
        await update_server_stats()
        await update_bot_clock_status()
        await ctx.send("✅ Wymuszono ręczne odświeżenie.")
        print("[MANUAL] Wymuszono ręczne odświeżenie")
    except Exception as e:
        await ctx.send(f"❌ Błąd podczas odświeżania: {e}")
        print(f"[BŁĄD MANUAL] {e}")


# =========================
# START
# =========================
bot.run(DISCORD_TOKEN)
