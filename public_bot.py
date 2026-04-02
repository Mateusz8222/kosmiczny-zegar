import asyncio
import json
import logging
import os
import re
from datetime import UTC, date, datetime
from typing import Any

import aiohttp
import discord
import pytz
from discord import app_commands
from discord.ext import commands


# =========================================================
# KOSMICZNY ZEGAR PUBLIC - CLEAN PRO REWRITE
# Stabilna wersja: pogoda, zegar, alergie, statystyki
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
CONFIG_FILE = os.getenv("CONFIG_FILE", "bot_config_public.json")
LOG_FILE = os.getenv("LOG_FILE", "bot.log")

DEFAULT_CITY_NAME = "Rzeszów"
DEFAULT_COUNTRY = "Polska"
DEFAULT_LATITUDE = 50.0413
DEFAULT_LONGITUDE = 21.9990
DEFAULT_TIMEZONE = "Europe/Warsaw"
DEFAULT_LANGUAGE = "pl"

WEATHER_REFRESH_SECONDS = 120
CLOCK_REFRESH_SECONDS = 15
STATS_REFRESH_SECONDS = 60
PRESENCE_REFRESH_SECONDS = 2
CHANNEL_EDIT_DELAY = 0.65
CHANNEL_CREATE_DELAY = 0.18
CHANNEL_DELETE_DELAY = 0.30
MAX_CHANNEL_NAME_LENGTH = 100

CONFIG_LOCK = asyncio.Lock()
EDIT_QUEUE: asyncio.Queue[tuple[int, int, str]] = asyncio.Queue()
LAST_DESIRED_NAMES: dict[int, str] = {}
EDIT_WORKER: asyncio.Task | None = None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("kosmiczny_zegar")


LANG = {
    "pl": {
        "cat_weather": "🌤️ Pogoda",
        "cat_clock": "🪐 Kosmiczny Zegar",
        "cat_stats": "📊 Statystyki",
        "cat_allergy": "⚠️ Ostrzeżenia dla alergików",
        "only_server": "❌ Tej komendy można użyć tylko na serwerze.",
        "setup_ok": "✅ Utworzono lub odświeżono wszystkie kategorie i kanały.",
        "refresh_ok": "✅ Wszystkie kanały zostały odświeżone.",
        "refresh_err": "❌ Nie udało się odświeżyć: {error}",
        "status_title": "📋 Status bota",
        "city_updated": "✅ Ustawiono miasto: {city}. Rozpoczynam odświeżanie.",
        "city_not_found": "❌ Nie udało się znaleźć miasta: {city}",
        "city_error": "❌ Błąd ustawiania miasta: {error}",
        "lang_set": "✅ Ustawiono język: polski.",
        "lang_invalid": "❌ Dostępne języki: pl, en",
        "reset_ok": "✅ Usunięto kategorie bota i wyczyszczono konfigurację. Użyj /setup.",
        "weather_title": "🌤️ Pogoda",
        "time_title": "🕒 Aktualny czas",
    },
    "en": {
        "cat_weather": "🌤️ Weather",
        "cat_clock": "🪐 Cosmic Clock",
        "cat_stats": "📊 Statistics",
        "cat_allergy": "⚠️ Allergy warnings",
        "only_server": "❌ This command can only be used in a server.",
        "setup_ok": "✅ Categories and channels were created or refreshed.",
        "refresh_ok": "✅ All channels were refreshed.",
        "refresh_err": "❌ Refresh failed: {error}",
        "status_title": "📋 Bot status",
        "city_updated": "✅ City set to: {city}. Refresh started.",
        "city_not_found": "❌ City not found: {city}",
        "city_error": "❌ City update error: {error}",
        "lang_set": "✅ Language set to: English.",
        "lang_invalid": "❌ Available languages: pl, en",
        "reset_ok": "✅ Bot categories removed and configuration cleared. Use /setup.",
        "weather_title": "🌤️ Weather",
        "time_title": "🕒 Current time",
    },
}

CATEGORY_KEYS = ("weather", "clock", "stats", "allergy")

WEATHER_CHANNEL_ORDER = [
    "temperature",
    "feels",
    "clouds",
    "rain",
    "wind",
    "pressure",
    "air",
    "alerts",
]
CLOCK_CHANNEL_ORDER = [
    "date",
    "sunrise",
    "sunset",
    "day_length",
    "part_of_day",
    "moon",
]
STATS_CHANNEL_ORDER = [
    "members",
    "humans",
    "online",
    "bots",
    "vc",
    "joined_today",
    "bans",
]
ALLERGY_CHANNEL_ORDER = [
    "allergy_alert",
    "allergy_live",
    "allergy_advice",
]

CHANNEL_SPECS = {
    "weather": {
        "temperature": "🌡️ Temperatura --°C",
        "feels": "🥵 Odczuwalna --°C",
        "clouds": "☁️ Zachmurzenie --%",
        "rain": "🌧️ Opady --",
        "wind": "🌬️ Wiatr -- km/h",
        "pressure": "🧭 Ciśnienie ---- hPa",
        "air": "🟡 Powietrze --",
        "alerts": "🟢 ALERT brak",
    },
    "clock": {
        "date": "📅 Data --.--.----",
        "sunrise": "🌅 Wschód --:--",
        "sunset": "🌇 Zachód --:--",
        "day_length": "☀️ Dzień --h --m",
        "part_of_day": "🌓 Pora dnia --",
        "moon": "🌕 Faza księżyca --",
    },
    "stats": {
        "members": "👥 Wszyscy 0",
        "humans": "🧑 Ludzie 0",
        "online": "🟢 Online 0",
        "bots": "🤖 Boty 0",
        "vc": "🔊 Na VC 0",
        "joined_today": "📥 Dzisiaj weszło 0",
        "bans": "🔨 Bany 0",
    },
    "allergy": {
        "allergy_alert": "🟢 Alert brak",
        "allergy_live": "🌿 Pylenie live",
        "allergy_advice": "💊 Brak specjalnych zaleceń",
    },
}

STATUS_ROLE_GROUPS = {
    "status": [
        "Dostępny",
        "Zaraz wracam",
        "AFK",
        "Nocny tryb",
        "Nie przeszkadzać",
        "Poza kompem",
        "Poza domem",
        "W pracy",
        "W szkole",
        "Idę spać",
        "Nowy tutaj",
        "Chcę poznać nowych ludzi",
    ],
    "mood": [
        "Na luzie",
        "Full energia",
        "W dobrym humorze",
        "Wkurzony",
        "Chory",
        "Zmęczony",
    ],
    "activity": [
        "Słucham muzyki",
        "Czatuję",
        "Gram",
        "Uczę się",
        "Na VC",
        "Streamuję",
        "Oglądam streama",
    ],
}

STATUS_GROUP_LABELS = {
    "status": "🟢 Status",
    "mood": "😎 Nastrój",
    "activity": "🎮 Aktywność",
}

STATUS_GROUP_EMOJIS = {
    "status": "🟢",
    "mood": "😎",
    "activity": "🎮",
}


def sanitize_channel_name(name: str) -> str:
    name = unicodedata_normalize(name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > MAX_CHANNEL_NAME_LENGTH:
        name = name[:MAX_CHANNEL_NAME_LENGTH].rstrip()
    return name


def unicodedata_normalize(value: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKC", value)


def load_config() -> dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return {"guilds": {}}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "guilds" not in data:
            data["guilds"] = {}
        return data
    except Exception:
        logger.exception("Nie udało się wczytać konfiguracji")
        return {"guilds": {}}


def save_config(data: dict[str, Any]) -> None:
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


async def get_guild_config(guild_id: int) -> dict[str, Any]:
    async with CONFIG_LOCK:
        data = load_config()
        gkey = str(guild_id)
        if gkey not in data["guilds"]:
            data["guilds"][gkey] = {
                "language": DEFAULT_LANGUAGE,
                "city_name": DEFAULT_CITY_NAME,
                "country": DEFAULT_COUNTRY,
                "latitude": DEFAULT_LATITUDE,
                "longitude": DEFAULT_LONGITUDE,
                "timezone": DEFAULT_TIMEZONE,
                "categories": {},
                "channels": {},
                "status_panel": {},
            }
            save_config(data)
        return data["guilds"][gkey]


async def update_guild_config(guild_id: int, updater: dict[str, Any]) -> dict[str, Any]:
    async with CONFIG_LOCK:
        data = load_config()
        gkey = str(guild_id)
        current = data["guilds"].setdefault(
            gkey,
            {
                "language": DEFAULT_LANGUAGE,
                "city_name": DEFAULT_CITY_NAME,
                "country": DEFAULT_COUNTRY,
                "latitude": DEFAULT_LATITUDE,
                "longitude": DEFAULT_LONGITUDE,
                "timezone": DEFAULT_TIMEZONE,
                "categories": {},
                "channels": {},
            },
        )
        current.update(updater)
        save_config(data)
        return current


async def clear_guild_config(guild_id: int) -> None:
    async with CONFIG_LOCK:
        data = load_config()
        data["guilds"].pop(str(guild_id), None)
        save_config(data)


def get_lang(cfg: dict[str, Any]) -> dict[str, str]:
    return LANG.get(cfg.get("language", DEFAULT_LANGUAGE), LANG["pl"])




def get_status_group_for_role_name(role_name: str) -> str | None:
    for group, names in STATUS_ROLE_GROUPS.items():
        if role_name in names:
            return group
    return None


def build_status_stats_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Ile osób ma jakie role",
        description="Poniżej widzisz dokładnie, ile osób ma każdą rolę statusową, nastroju i aktywności.",
        color=discord.Color.green(),
    )
    for group, names in STATUS_ROLE_GROUPS.items():
        lines = []
        total = 0
        for role_name in names:
            role = discord.utils.get(guild.roles, name=role_name)
            count = len(role.members) if role else 0
            total += count
            lines.append(f"• {role_name} — **{count}**")
        embed.add_field(
            name=f"{STATUS_GROUP_LABELS[group]} · razem przypisań: {total}",
            value="\n".join(lines),
            inline=False,
        )
    embed.set_footer(text="Kosmiczny Zegar 24 • Statystyki ról")
    return embed


async def ensure_status_roles(guild: discord.Guild) -> None:
    me = guild.me
    for names in STATUS_ROLE_GROUPS.values():
        for role_name in names:
            role = discord.utils.get(guild.roles, name=role_name)
            if role is None:
                try:
                    await guild.create_role(name=role_name, mentionable=False, reason="Role statusów bota")
                    await asyncio.sleep(0.25)
                except discord.HTTPException:
                    logger.exception("Nie udało się utworzyć roli %s", role_name)
    if me and me.guild_permissions.manage_roles:
        try:
            status_roles = [r for names in STATUS_ROLE_GROUPS.values() for r in (discord.utils.get(guild.roles, name=n) for n in names) if r]
            status_roles.sort(key=lambda r: r.position)
        except Exception:
            pass


async def refresh_status_panel(guild: discord.Guild) -> None:
    cfg = await get_guild_config(guild.id)
    panel = cfg.get("status_panel") or {}
    channel_id = panel.get("channel_id")
    message_id = panel.get("message_id")
    if not channel_id or not message_id:
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(message_id)
    except discord.HTTPException:
        return
    embed = build_status_stats_embed(guild)
    try:
        await message.edit(embed=embed)
    except discord.HTTPException:
        logger.exception("Nie udało się odświeżyć panelu statusów")


async def apply_single_status_role(member: discord.Member, selected_role_name: str) -> tuple[str, str]:
    group = get_status_group_for_role_name(selected_role_name)
    if not group:
        raise ValueError("Nieznana rola statusowa")
    selected_role = discord.utils.get(member.guild.roles, name=selected_role_name)
    if not selected_role:
        raise ValueError(f"Brak roli: {selected_role_name}")
    to_remove = []
    for role_name in STATUS_ROLE_GROUPS[group]:
        role = discord.utils.get(member.guild.roles, name=role_name)
        if role and role in member.roles and role != selected_role:
            to_remove.append(role)
    if to_remove:
        await member.remove_roles(*to_remove, reason="Zmiana statusu przez bota")
    if selected_role not in member.roles:
        await member.add_roles(selected_role, reason="Ustawienie statusu przez bota")
    await refresh_status_panel(member.guild)
    return group, selected_role.name


class StatusSelect(discord.ui.Select):
    def __init__(self, group: str):
        options = [discord.SelectOption(label=name, value=name, emoji=STATUS_GROUP_EMOJIS[group]) for name in STATUS_ROLE_GROUPS[group]]
        super().__init__(placeholder=f"Wybierz: {STATUS_GROUP_LABELS[group]}", min_values=1, max_values=1, options=options)
        self.group = group

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
            return
        try:
            _, role_name = await apply_single_status_role(interaction.user, self.values[0])
            await interaction.response.send_message(f"✅ Ustawiono {STATUS_GROUP_LABELS[self.group]}: **{role_name}**", ephemeral=True)
        except Exception as e:
            logger.exception("Błąd ustawiania statusu")
            await interaction.response.send_message(f"❌ Nie udało się ustawić statusu: {e}", ephemeral=True)


class ClearStatusesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Wyczyść wszystko", style=discord.ButtonStyle.danger, emoji="🧹")

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Tej komendy można użyć tylko na serwerze.", ephemeral=True)
            return
        roles_to_remove = []
        for names in STATUS_ROLE_GROUPS.values():
            for role_name in names:
                role = discord.utils.get(interaction.guild.roles, name=role_name) if interaction.guild else None
                if role and role in interaction.user.roles:
                    roles_to_remove.append(role)
        if roles_to_remove:
            try:
                await interaction.user.remove_roles(*roles_to_remove, reason="Wyczyszczenie statusów przez bota")
                await refresh_status_panel(interaction.guild)
            except Exception as e:
                await interaction.response.send_message(f"❌ Nie udało się wyczyścić statusów: {e}", ephemeral=True)
                return
        await interaction.response.send_message("✅ Wyczyszczono wszystkie Twoje statusy.", ephemeral=True)


class StatusPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(StatusSelect("status"))
        self.add_item(StatusSelect("mood"))
        self.add_item(StatusSelect("activity"))
        self.add_item(ClearStatusesButton())


class KosmicznyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.presences = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
        await ensure_edit_worker()
        self.loop.create_task(weather_loop())
        self.loop.create_task(clock_loop())
        self.loop.create_task(stats_loop())
        self.loop.create_task(presence_loop())

    async def close(self) -> None:
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()


bot = KosmicznyBot()


async def ensure_edit_worker() -> None:
    global EDIT_WORKER
    if EDIT_WORKER and not EDIT_WORKER.done():
        return
    EDIT_WORKER = asyncio.create_task(channel_edit_worker())


async def channel_edit_worker() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            channel_id, position, desired_name = await EDIT_QUEUE.get()
            LAST_DESIRED_NAMES[channel_id] = desired_name
            await asyncio.sleep(0.05)
            # coalesce pending edits for same channel
            while not EDIT_QUEUE.empty():
                try:
                    next_id, next_pos, next_name = EDIT_QUEUE.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if next_id == channel_id:
                    position = next_pos
                    desired_name = next_name
                    LAST_DESIRED_NAMES[channel_id] = next_name
                else:
                    await EDIT_QUEUE.put((next_id, next_pos, next_name))
                    break

            channel = bot.get_channel(channel_id)
            if not isinstance(channel, discord.abc.GuildChannel):
                continue

            edits: dict[str, Any] = {}
            if sanitize_channel_name(channel.name) != sanitize_channel_name(desired_name):
                edits["name"] = sanitize_channel_name(desired_name)
            if channel.position != position:
                edits["position"] = position

            if edits:
                try:
                    await channel.edit(**edits)
                    await asyncio.sleep(CHANNEL_EDIT_DELAY)
                except discord.HTTPException:
                    logger.exception("Błąd edycji kanału %s", channel_id)
        except Exception:
            logger.exception("Błąd worker edycji kanałów")
            await asyncio.sleep(1)


async def queue_channel_update(channel: discord.abc.GuildChannel, name: str, position: int) -> None:
    await EDIT_QUEUE.put((channel.id, position, sanitize_channel_name(name)))


async def api_get_json(url: str) -> dict[str, Any]:
    if not bot.http_session:
        raise RuntimeError("Brak sesji HTTP")
    async with bot.http_session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json()


async def geocode_city(city: str) -> dict[str, Any] | None:
    q = aiohttp.helpers.quote(city, safe="")
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=pl&format=json"
    data = await api_get_json(url)
    results = data.get("results") or []
    if not results:
        return None
    return results[0]


def weather_code_text(code: int) -> str:
    mapping = {
        0: "bezchmurnie",
        1: "prawie bezchmurnie",
        2: "małe zachmurzenie",
        3: "pochmurno",
        45: "mgła",
        48: "osadzająca się mgła",
        51: "mżawka",
        53: "mżawka",
        55: "silna mżawka",
        61: "lekki deszcz",
        63: "deszcz",
        65: "silny deszcz",
        71: "lekki śnieg",
        73: "śnieg",
        75: "silny śnieg",
        80: "przelotne opady",
        81: "przelotny deszcz",
        82: "silne przelotne opady",
        95: "burza",
        96: "burza z gradem",
        99: "silna burza z gradem",
    }
    return mapping.get(code, f"kod {code}")


def part_of_day(now_hour: int) -> str:
    if 5 <= now_hour < 12:
        return "rano"
    if 12 <= now_hour < 18:
        return "po południu"
    if 18 <= now_hour < 22:
        return "wieczór"
    return "noc"


def moon_phase_name(phase: float) -> str:
    phases = [
        (0.03, "nów"),
        (0.22, "przybywający sierp"),
        (0.28, "pierwsza kwadra"),
        (0.47, "przybywający garb"),
        (0.53, "pełnia"),
        (0.72, "ubywający garb"),
        (0.78, "ostatnia kwadra"),
        (0.97, "ubywający sierp"),
        (1.01, "nów"),
    ]
    for limit, name in phases:
        if phase <= limit:
            return name
    return "nów"


def moon_phase_fraction_for_date(day: date) -> float:
    # Przybliżenie wieku Księżyca w cyklu synodycznym (29.53058867 dnia).
    # Wystarczy do nazwy fazy w kanale Discord.
    reference_new_moon = datetime(2000, 1, 6, 18, 14, tzinfo=UTC)
    current = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
    synodic_month = 29.53058867
    days_since = (current - reference_new_moon).total_seconds() / 86400.0
    fraction = (days_since % synodic_month) / synodic_month
    return max(0.0, min(1.0, fraction))


def pollen_level_name(value: float) -> str:
    if value <= 0:
        return "brak"
    if value < 10:
        return "niskie"
    if value < 30:
        return "umiarkowane"
    if value < 60:
        return "wysokie"
    return "bardzo wysokie"


def pollen_alert_rank(level: str) -> int:
    order = {
        "brak": 0,
        "niskie": 1,
        "umiarkowane": 2,
        "wysokie": 3,
        "bardzo wysokie": 4,
    }
    return order.get(level, 0)


def build_allergy_channels(current: dict[str, float]) -> tuple[str, str, str]:
    plants = [
        ("olsza", current.get("alder_pollen", 0)),
        ("brzoza", current.get("birch_pollen", 0)),
        ("trawy", current.get("grass_pollen", 0)),
        ("bylica", current.get("mugwort_pollen", 0)),
        ("oliwka", current.get("olive_pollen", 0)),
        ("ambrozja", current.get("ragweed_pollen", 0)),
    ]
    classified = [(name, pollen_level_name(value)) for name, value in plants]
    active = [(name, lvl) for name, lvl in classified if lvl != "brak"]
    active.sort(key=lambda x: pollen_alert_rank(x[1]), reverse=True)

    if active:
        top = active[:2]
        live_text = " | ".join(f"{n} {lvl}" for n, lvl in top)
        live_channel = f"🌿 {live_text}"
        top_name, top_level = active[0]
        if pollen_alert_rank(top_level) >= 3:
            alert_channel = f"🚨 alert {top_name} {top_level}"
            advice = "💊 unikaj spacerów rano"
        elif pollen_alert_rank(top_level) == 2:
            alert_channel = "🟡 alert umiarkowane pylenie"
            advice = "💊 warto zamknąć okna"
        else:
            alert_channel = "🟢 alert niski"
            advice = "💊 brak specjalnych zaleceń"
    else:
        live_channel = "🌿 pylenie brak"
        alert_channel = "🟢 alert brak"
        advice = "💊 brak specjalnych zaleceń"

    return (
        sanitize_channel_name(alert_channel),
        sanitize_channel_name(live_channel),
        sanitize_channel_name(advice),
    )


async def fetch_weather_bundle(cfg: dict[str, Any]) -> dict[str, Any]:
    lat = cfg["latitude"]
    lon = cfg["longitude"]
    tz = aiohttp.helpers.quote(cfg.get("timezone", DEFAULT_TIMEZONE), safe="")

    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&timezone={tz}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,pressure_msl,cloud_cover,wind_speed_10m,weather_code"
        "&daily=sunrise,sunset,daylight_duration,precipitation_sum"
        "&forecast_days=1"
    )
    air_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lon}&timezone={tz}"
        "&current=european_aqi,alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,ragweed_pollen"
    )

    weather, air = await asyncio.gather(api_get_json(weather_url), api_get_json(air_url))
    return {"weather": weather, "air": air}


def aqi_text(aqi: float | int | None) -> str:
    if aqi is None:
        return "brak danych"
    try:
        value = float(aqi)
    except Exception:
        return "brak danych"
    if value < 20:
        return "dobre"
    if value < 40:
        return "umiarkowane"
    if value < 60:
        return "średnie"
    if value < 80:
        return "słabe"
    return "bardzo słabe"


async def ensure_category(guild: discord.Guild, name: str, saved_id: int | None) -> discord.CategoryChannel:
    category = guild.get_channel(saved_id) if saved_id else None
    if not isinstance(category, discord.CategoryChannel):
        category = discord.utils.get(guild.categories, name=name)
    if not category:
        category = await guild.create_category(name)
        await asyncio.sleep(CHANNEL_CREATE_DELAY)
    elif category.name != name:
        await category.edit(name=name)
        await asyncio.sleep(CHANNEL_EDIT_DELAY)
    return category


async def ensure_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    saved_id: int | None,
    fallback_name: str,
) -> discord.VoiceChannel:
    channel = guild.get_channel(saved_id) if saved_id else None
    if not isinstance(channel, discord.VoiceChannel):
        channel = discord.utils.get(category.channels, name=fallback_name)
        if channel and not isinstance(channel, discord.VoiceChannel):
            channel = None
    if not channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False, speak=False),
            guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True, view_channel=True),
        }
        channel = await guild.create_voice_channel(fallback_name, category=category, overwrites=overwrites)
        await asyncio.sleep(CHANNEL_CREATE_DELAY)
    elif channel.category_id != category.id:
        await channel.edit(category=category)
        await asyncio.sleep(CHANNEL_EDIT_DELAY)
    return channel


async def create_or_sync_structure(guild: discord.Guild) -> dict[str, Any]:
    cfg = await get_guild_config(guild.id)
    lang = get_lang(cfg)

    categories = dict(cfg.get("categories", {}))
    channels = dict(cfg.get("channels", {}))

    weather_cat = await ensure_category(guild, lang["cat_weather"], categories.get("weather"))
    clock_cat = await ensure_category(guild, lang["cat_clock"], categories.get("clock"))
    stats_cat = await ensure_category(guild, lang["cat_stats"], categories.get("stats"))
    allergy_cat = await ensure_category(guild, lang["cat_allergy"], categories.get("allergy"))

    categories = {
        "weather": weather_cat.id,
        "clock": clock_cat.id,
        "stats": stats_cat.id,
        "allergy": allergy_cat.id,
    }

    # weather
    for key in WEATHER_CHANNEL_ORDER:
        ch = await ensure_voice_channel(guild, weather_cat, channels.get(key), CHANNEL_SPECS["weather"][key])
        channels[key] = ch.id

    # clock
    for key in CLOCK_CHANNEL_ORDER:
        ch = await ensure_voice_channel(guild, clock_cat, channels.get(key), CHANNEL_SPECS["clock"][key])
        channels[key] = ch.id

    # stats
    for key in STATS_CHANNEL_ORDER:
        ch = await ensure_voice_channel(guild, stats_cat, channels.get(key), CHANNEL_SPECS["stats"][key])
        channels[key] = ch.id

    # allergy
    for key in ALLERGY_CHANNEL_ORDER:
        ch = await ensure_voice_channel(guild, allergy_cat, channels.get(key), CHANNEL_SPECS["allergy"][key])
        channels[key] = ch.id

    # remove old weather pollen channel if exists from older versions
    old_pollen_channel_id = channels.pop("pollen", None)
    if old_pollen_channel_id:
        old_channel = guild.get_channel(old_pollen_channel_id)
        if old_channel:
            try:
                await old_channel.delete()
                await asyncio.sleep(CHANNEL_DELETE_DELAY)
            except discord.HTTPException:
                logger.exception("Nie udało się usunąć starego kanału pylenia")

    cfg = await update_guild_config(
        guild.id,
        {
            "categories": categories,
            "channels": channels,
        },
    )
    return cfg


async def sort_channels(guild: discord.Guild, cfg: dict[str, Any]) -> None:
    channels = cfg.get("channels", {})
    ordered = WEATHER_CHANNEL_ORDER + CLOCK_CHANNEL_ORDER + STATS_CHANNEL_ORDER + ALLERGY_CHANNEL_ORDER

    for key in ordered:
        ch_id = channels.get(key)
        ch = guild.get_channel(ch_id) if ch_id else None
        if not isinstance(ch, discord.VoiceChannel):
            continue
        if key in WEATHER_CHANNEL_ORDER:
            idx = WEATHER_CHANNEL_ORDER.index(key)
        elif key in CLOCK_CHANNEL_ORDER:
            idx = CLOCK_CHANNEL_ORDER.index(key)
        elif key in STATS_CHANNEL_ORDER:
            idx = STATS_CHANNEL_ORDER.index(key)
        else:
            idx = ALLERGY_CHANNEL_ORDER.index(key)

        await queue_channel_update(ch, ch.name, idx)


async def refresh_weather_channels(guild: discord.Guild, cfg: dict[str, Any]) -> None:
    channels = cfg.get("channels", {})
    bundle = await fetch_weather_bundle(cfg)
    current = bundle["weather"].get("current", {})
    daily = bundle["weather"].get("daily", {})
    air = bundle["air"].get("current", {})

    temp = round(current.get("temperature_2m", 0))
    feels = round(current.get("apparent_temperature", 0))
    clouds = round(current.get("cloud_cover", 0))
    wind = round(current.get("wind_speed_10m", 0))
    pressure = round(current.get("pressure_msl", 0))
    weather_code = int(current.get("weather_code", 0))
    rain_sum = daily.get("precipitation_sum", [0])[0]
    aqi = air.get("european_aqi")

    names = {
        "temperature": f"🌡️ Temperatura {temp}°C",
        "feels": f"🥵 Odczuwalna {feels}°C",
        "clouds": f"☁️ Zachmurzenie {clouds}%",
        "rain": f"🌧️ Opady {round(rain_sum, 1)} mm" if rain_sum else "🌧️ Opady brak",
        "wind": f"🌬️ Wiatr {wind} km/h",
        "pressure": f"🧭 Ciśnienie {pressure} hPa",
        "air": f"🟡 Powietrze {aqi_text(aqi)}",
        "alerts": "🟢 ALERT brak" if weather_code in {0,1,2,3} else f"⚠️ ALERT {weather_code_text(weather_code)}",
    }

    allergy_alert, allergy_live, allergy_advice = build_allergy_channels(air)
    names["allergy_alert"] = allergy_alert
    names["allergy_live"] = allergy_live
    names["allergy_advice"] = allergy_advice

    for key in WEATHER_CHANNEL_ORDER + ALLERGY_CHANNEL_ORDER:
        ch_id = channels.get(key)
        ch = guild.get_channel(ch_id) if ch_id else None
        if isinstance(ch, discord.VoiceChannel):
            if key in WEATHER_CHANNEL_ORDER:
                pos = WEATHER_CHANNEL_ORDER.index(key)
            else:
                pos = ALLERGY_CHANNEL_ORDER.index(key)
            await queue_channel_update(ch, names[key], pos)


async def refresh_clock_channels(guild: discord.Guild, cfg: dict[str, Any]) -> None:
    channels = cfg.get("channels", {})
    tz_name = cfg.get("timezone", DEFAULT_TIMEZONE)
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)

    bundle = await fetch_weather_bundle(cfg)
    daily = bundle["weather"].get("daily", {})
    sunrise_raw = daily.get("sunrise", ["--:--"])[0]
    sunset_raw = daily.get("sunset", ["--:--"])[0]
    sunrise = sunrise_raw[-5:]
    sunset = sunset_raw[-5:]
    daylight_seconds = int(float(daily.get("daylight_duration", [0])[0] or 0))
    hours = daylight_seconds // 3600
    minutes = (daylight_seconds % 3600) // 60
    moon = moon_phase_name(moon_phase_fraction_for_date(now.date()))

    names = {
        "date": f"📅 Data {now.strftime('%d.%m.%Y')}",
        "sunrise": f"🌅 Wschód {sunrise}",
        "sunset": f"🌇 Zachód {sunset}",
        "day_length": f"☀️ Dzień {hours}h {minutes}m",
        "part_of_day": f"🌓 Pora dnia {part_of_day(now.hour)}",
        "moon": f"🌕 Faza księżyca {moon}",
    }

    for key in CLOCK_CHANNEL_ORDER:
        ch_id = channels.get(key)
        ch = guild.get_channel(ch_id) if ch_id else None
        if isinstance(ch, discord.VoiceChannel):
            pos = CLOCK_CHANNEL_ORDER.index(key)
            await queue_channel_update(ch, names[key], pos)


async def refresh_stats_channels(guild: discord.Guild, cfg: dict[str, Any]) -> None:
    channels = cfg.get("channels", {})
    members = guild.member_count or len(guild.members)
    bots_count = len([m for m in guild.members if m.bot])
    humans = max(0, members - bots_count)
    online = len([m for m in guild.members if m.status != discord.Status.offline])
    vc = len([m for m in guild.members if getattr(m.voice, "channel", None)])
    today = datetime.now().date()
    joined_today = len([m for m in guild.members if m.joined_at and m.joined_at.date() == today])
    try:
        bans = len([b async for b in guild.bans(limit=None)])
    except discord.Forbidden:
        bans = 0

    names = {
        "members": f"👥 Wszyscy {members}",
        "humans": f"🧑 Ludzie {humans}",
        "online": f"🟢 Online {online}",
        "bots": f"🤖 Boty {bots_count}",
        "vc": f"🔊 Na VC {vc}",
        "joined_today": f"📥 Dzisiaj weszło {joined_today}",
        "bans": f"🔨 Bany {bans}",
    }

    for key in STATS_CHANNEL_ORDER:
        ch_id = channels.get(key)
        ch = guild.get_channel(ch_id) if ch_id else None
        if isinstance(ch, discord.VoiceChannel):
            pos = STATS_CHANNEL_ORDER.index(key)
            await queue_channel_update(ch, names[key], pos)


async def full_refresh_guild(guild: discord.Guild) -> None:
    await ensure_status_roles(guild)
    cfg = await create_or_sync_structure(guild)
    await asyncio.gather(
        refresh_weather_channels(guild, cfg),
        refresh_clock_channels(guild, cfg),
        refresh_stats_channels(guild, cfg),
    )
    await sort_channels(guild, cfg)
    await refresh_status_panel(guild)


async def weather_loop() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        for guild in bot.guilds:
            try:
                cfg = await get_guild_config(guild.id)
                if cfg.get("categories"):
                    await refresh_weather_channels(guild, cfg)
            except Exception:
                logger.exception("Błąd pętli pogody dla %s", guild.id)
        await asyncio.sleep(WEATHER_REFRESH_SECONDS)


async def clock_loop() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        for guild in bot.guilds:
            try:
                cfg = await get_guild_config(guild.id)
                if cfg.get("categories"):
                    await refresh_clock_channels(guild, cfg)
            except Exception:
                logger.exception("Błąd pętli zegara dla %s", guild.id)
        await asyncio.sleep(CLOCK_REFRESH_SECONDS)


async def stats_loop() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        for guild in bot.guilds:
            try:
                cfg = await get_guild_config(guild.id)
                if cfg.get("categories"):
                    await refresh_stats_channels(guild, cfg)
            except Exception:
                logger.exception("Błąd pętli statystyk dla %s", guild.id)
        await asyncio.sleep(STATS_REFRESH_SECONDS)


async def presence_loop() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = datetime.now(pytz.timezone(DEFAULT_TIMEZONE))
            text = f"🕒 {now.strftime('%H:%M:%S')}"
            await bot.change_presence(activity=discord.CustomActivity(name=text))
        except Exception:
            logger.exception("Błąd ustawiania presence")
        await asyncio.sleep(PRESENCE_REFRESH_SECONDS)




@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    try:
        before_names = {r.name for r in before.roles}
        after_names = {r.name for r in after.roles}
        tracked = {name for names in STATUS_ROLE_GROUPS.values() for name in names}
        if (before_names & tracked) != (after_names & tracked):
            await refresh_status_panel(after.guild)
    except Exception:
        logger.exception("Błąd on_member_update dla panelu statusów")

@bot.event
async def on_ready() -> None:
    try:
        synced = await bot.tree.sync()
        logger.info("Zsynchronizowano %s komend", len(synced))
    except Exception:
        logger.exception("Błąd synchronizacji komend")
    logger.info("Zalogowano jako %s (%s)", bot.user, bot.user.id if bot.user else "?")


def require_guild(interaction: discord.Interaction) -> discord.Guild | None:
    return interaction.guild


@bot.tree.command(name="setup", description="Tworzy lub odświeża wszystkie kategorie i kanały bota")
async def setup_cmd(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    cfg = await get_guild_config(guild.id)
    lang = get_lang(cfg)
    try:
        await full_refresh_guild(guild)
        await interaction.followup.send(lang["setup_ok"], ephemeral=True)
    except Exception as e:
        logger.exception("Błąd /setup")
        await interaction.followup.send(lang["refresh_err"].format(error=str(e)), ephemeral=True)


@bot.tree.command(name="refresh", description="Ręcznie odświeża kanały bota")
async def refresh_cmd(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    cfg = await get_guild_config(guild.id)
    lang = get_lang(cfg)
    try:
        await full_refresh_guild(guild)
        await interaction.followup.send(lang["refresh_ok"], ephemeral=True)
    except Exception as e:
        logger.exception("Błąd /refresh")
        await interaction.followup.send(lang["refresh_err"].format(error=str(e)), ephemeral=True)


@bot.tree.command(name="miasto", description="Ustawia miasto dla pogody, alergii i zegara")
@app_commands.describe(nazwa="Np. Warszawa")
async def city_cmd(interaction: discord.Interaction, nazwa: str) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    cfg = await get_guild_config(guild.id)
    lang = get_lang(cfg)
    try:
        result = await geocode_city(nazwa)
        if not result:
            await interaction.followup.send(lang["city_not_found"].format(city=nazwa), ephemeral=True)
            return

        city_name = result.get("name", nazwa)
        country = result.get("country", "")
        latitude = result.get("latitude", DEFAULT_LATITUDE)
        longitude = result.get("longitude", DEFAULT_LONGITUDE)
        timezone = result.get("timezone", DEFAULT_TIMEZONE)

        cfg = await update_guild_config(
            guild.id,
            {
                "city_name": city_name,
                "country": country,
                "latitude": latitude,
                "longitude": longitude,
                "timezone": timezone,
            },
        )

        await full_refresh_guild(guild)
        await interaction.followup.send(
            lang["city_updated"].format(city=f"{city_name}, {country}" if country else city_name),
            ephemeral=True,
        )
    except Exception as e:
        logger.exception("Błąd /miasto")
        await interaction.followup.send(lang["city_error"].format(error=str(e)), ephemeral=True)


@bot.tree.command(name="language", description="Ustawia język bota")
@app_commands.describe(kod="pl albo en")
async def language_cmd(interaction: discord.Interaction, kod: str) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    kod = kod.lower().strip()
    cfg = await get_guild_config(guild.id)
    lang = get_lang(cfg)
    if kod not in LANG:
        await interaction.response.send_message(lang["lang_invalid"], ephemeral=True)
        return

    await update_guild_config(guild.id, {"language": kod})
    await interaction.response.send_message(LANG[kod]["lang_set"], ephemeral=True)
    await full_refresh_guild(guild)


@bot.tree.command(name="status", description="Pokazuje status konfiguracji bota")
async def status_cmd(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    cfg = await get_guild_config(guild.id)
    lang = get_lang(cfg)

    embed = discord.Embed(title=lang["status_title"], color=discord.Color.blurple())
    embed.add_field(name="Miasto", value=cfg.get("city_name", DEFAULT_CITY_NAME), inline=True)
    embed.add_field(name="Kraj", value=cfg.get("country", DEFAULT_COUNTRY), inline=True)
    embed.add_field(name="Strefa", value=cfg.get("timezone", DEFAULT_TIMEZONE), inline=True)
    embed.add_field(name="Szerokość", value=str(cfg.get("latitude", DEFAULT_LATITUDE)), inline=True)
    embed.add_field(name="Długość", value=str(cfg.get("longitude", DEFAULT_LONGITUDE)), inline=True)
    embed.add_field(name="Język", value=cfg.get("language", DEFAULT_LANGUAGE), inline=True)
    embed.add_field(name="Kategorie", value="\n".join(CATEGORY_KEYS), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="pogoda", description="Pokazuje aktualną pogodę i alergeny")
async def weather_cmd(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    cfg = await get_guild_config(guild.id)
    lang = get_lang(cfg)
    bundle = await fetch_weather_bundle(cfg)
    current = bundle["weather"].get("current", {})
    daily = bundle["weather"].get("daily", {})
    air = bundle["air"].get("current", {})

    embed = discord.Embed(
        title=f"{lang['weather_title']} — {cfg['city_name']}, {cfg['country']}",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Temperatura", value=f"{round(current.get('temperature_2m', 0))}°C", inline=True)
    embed.add_field(name="Odczuwalna", value=f"{round(current.get('apparent_temperature', 0))}°C", inline=True)
    embed.add_field(name="Zachmurzenie", value=f"{round(current.get('cloud_cover', 0))}%", inline=True)
    embed.add_field(name="Wiatr", value=f"{round(current.get('wind_speed_10m', 0))} km/h", inline=True)
    embed.add_field(name="Ciśnienie", value=f"{round(current.get('pressure_msl', 0))} hPa", inline=True)
    embed.add_field(name="Powietrze", value=aqi_text(air.get("european_aqi")), inline=True)

    plants = [
        ("Olsza", pollen_level_name(air.get("alder_pollen", 0))),
        ("Brzoza", pollen_level_name(air.get("birch_pollen", 0))),
        ("Trawy", pollen_level_name(air.get("grass_pollen", 0))),
        ("Bylica", pollen_level_name(air.get("mugwort_pollen", 0))),
        ("Oliwka", pollen_level_name(air.get("olive_pollen", 0))),
        ("Ambrozja", pollen_level_name(air.get("ragweed_pollen", 0))),
    ]
    embed.add_field(name="Pylenie", value="\n".join(f"• {n}: {lvl}" for n, lvl in plants), inline=False)
    embed.add_field(name="Wschód", value=daily.get("sunrise", ["--"])[0][-5:], inline=True)
    embed.add_field(name="Zachód", value=daily.get("sunset", ["--"])[0][-5:], inline=True)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="czas", description="Pokazuje aktualny czas dla ustawionego miasta")
async def time_cmd(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    cfg = await get_guild_config(guild.id)
    tz = pytz.timezone(cfg.get("timezone", DEFAULT_TIMEZONE))
    now = datetime.now(tz)

    embed = discord.Embed(title=LANG[cfg.get("language", "pl")]["time_title"], color=discord.Color.gold())
    embed.add_field(name="Miasto", value=f"{cfg['city_name']}, {cfg['country']}", inline=False)
    embed.add_field(name="Godzina", value=now.strftime("%H:%M:%S"), inline=True)
    embed.add_field(name="Data", value=now.strftime("%d.%m.%Y"), inline=True)
    embed.add_field(name="Pora dnia", value=part_of_day(now.hour), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)




@bot.tree.command(name="statusy", description="Otwiera prywatne okienko do ustawiania statusów")
async def statuses_cmd(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    await ensure_status_roles(guild)
    embed = discord.Embed(
        title="🎛️ Ustaw swoje statusy",
        description="Wybierz po jednej roli z każdej kategorii: status, nastrój i aktywność.",
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, view=StatusPanelView(), ephemeral=True)


@bot.tree.command(name="panel_statusow", description="Tworzy lub odświeża publiczny panel statystyk ról")
async def status_panel_cmd(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ Tylko administrator może utworzyć panel statusów.", ephemeral=True)
        return
    await ensure_status_roles(guild)
    embed = build_status_stats_embed(guild)
    cfg = await get_guild_config(guild.id)
    panel = cfg.get("status_panel") or {}
    old_channel_id = panel.get("channel_id")
    old_message_id = panel.get("message_id")
    if old_channel_id == interaction.channel_id and old_message_id:
        try:
            old_message = await interaction.channel.fetch_message(old_message_id)
            await old_message.edit(embed=embed)
            await interaction.response.send_message("✅ Panel statusów został odświeżony.", ephemeral=True)
            return
        except discord.HTTPException:
            pass
    message = await interaction.channel.send(embed=embed)
    await update_guild_config(guild.id, {"status_panel": {"channel_id": interaction.channel_id, "message_id": message.id}})
    await interaction.response.send_message("✅ Panel statusów został utworzony.", ephemeral=True)


@bot.tree.command(name="usun_wszystko", description="Usuwa kategorie bota i czyści konfigurację")
async def reset_cmd(interaction: discord.Interaction) -> None:
    guild = require_guild(interaction)
    if not guild:
        await interaction.response.send_message("❌ Tylko na serwerze.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    cfg = await get_guild_config(guild.id)
    lang = get_lang(cfg)

    # Usuń zapisane kategorie
    for cat_key in CATEGORY_KEYS:
        cat_id = cfg.get("categories", {}).get(cat_key)
        category = guild.get_channel(cat_id) if cat_id else None
        if isinstance(category, discord.CategoryChannel):
            for ch in list(category.channels):
                try:
                    await ch.delete()
                    await asyncio.sleep(CHANNEL_DELETE_DELAY)
                except discord.HTTPException:
                    logger.exception("Błąd usuwania kanału %s", ch.id)
            try:
                await category.delete()
                await asyncio.sleep(CHANNEL_DELETE_DELAY)
            except discord.HTTPException:
                logger.exception("Błąd usuwania kategorii %s", category.id)

    await clear_guild_config(guild.id)
    await interaction.followup.send(lang["reset_ok"], ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Brak DISCORD_TOKEN w zmiennych środowiskowych")
    bot.run(TOKEN)
