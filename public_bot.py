# ================================
# KOSMICZNY ZEGAR PUBLIC - BOT v25 MAX
# MULTILANGUAGE: PL / EN
# + PANEL STATUSÓW / NASTROJU / AKTYWNOŚCI PRO MAX
# ================================

import asyncio
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
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

TOKEN = os.getenv("DISCORD_TOKEN_PUBLIC") or os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Brakuje DISCORD_TOKEN_PUBLIC lub DISCORD_TOKEN w zmiennych środowiskowych")

DEFAULT_CITY_NAME = "Warszawa"
DEFAULT_LATITUDE = 52.2297
DEFAULT_LONGITUDE = 21.0122
DEFAULT_COUNTRY = "Polska"
DEFAULT_TIMEZONE = "Europe/Warsaw"
DEFAULT_LANGUAGE = "pl"

WEATHER_REFRESH_MINUTES = 15
CHANNEL_EDIT_DELAY = 1.2
CATEGORY_DELETE_DELAY = 0.5
STATS_DEBOUNCE_SECONDS = 10
MAX_CHANNEL_NAME_LEN = 95

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "bot_data_public.db"
bot_start_time = datetime.now(UTC)
stats_update_tasks: dict[int, asyncio.Task] = {}

# ================================
# TŁUMACZENIA
# ================================

LANGUAGES = {
    "pl": {
        "lang_name": "Polski",
        "creator": "Mati",
        "bot_version": "v25 MAX",

        "cat_weather": "🌤️ Pogoda",
        "cat_clock": "🛰️ Kosmiczny Zegar",
        "cat_stats": "📊 Statystyki",

        "ch_temperature": "🌡 Temperatura",
        "ch_feels": "🥵 Odczuwalna",
        "ch_clouds": "☁ Zachmurzenie",
        "ch_air": "🌫 Powietrze",
        "ch_pollen": "🌿 Pylenie",
        "ch_rain": "🌧 Opady",
        "ch_wind": "💨 Wiatr",
        "ch_pressure": "⏱ Ciśnienie",
        "ch_alerts": "🟢 ALERT brak",

        "ch_date": "📅 Data",
        "ch_part_of_day": "🌓 Pora dnia",
        "ch_sunrise": "🌅 Wschód",
        "ch_sunset": "🌇 Zachód",
        "ch_day_length": "☀️ Dzień",
        "ch_moon": "🌙 Faza księżyca",

        "ch_members": "👥 Wszyscy",
        "ch_humans": "👤 Ludzie",
        "ch_online": "🟢 Online",
        "ch_bots": "🤖 Boty",
        "ch_vc": "🔊 Na VC",
        "ch_joined_today": "📥 Dzisiaj weszło 0",

        "only_server": "❌ Tej komendy można użyć tylko na serwerze.",
        "setup_ok": "✅ Utworzono i odświeżono wszystkie kategorie oraz kanały.",
        "setup_error": "❌ Błąd setupu: {error}",
        "refresh_no_config": "ℹ️ Brak konfiguracji. Najpierw użyj `/setup`.",
        "refresh_ok": "✅ Wszystkie kanały zostały odświeżone.",
        "refresh_error": "❌ Błąd refreshu: {error}",
        "no_config": "ℹ️ Brak konfiguracji. Użyj `/setup`.",
        "city_setup_first": "ℹ️ Najpierw użyj `/setup`, aby utworzyć kategorie i kanały.",
        "city_not_found": "❌ Nie znaleziono miasta: `{city}`",
        "city_updated": "✅ Ustawiono miasto: **{city}** i zaktualizowano pogodę oraz zegar.",
        "city_error": "❌ Błąd ustawiania miasta: {error}",
        "weather_error": "❌ Błąd pobierania pogody: {error}",

        "delete_only_server": "❌ Tylko na serwerze.",
        "delete_no_config": "ℹ️ Brak konfiguracji.",
        "delete_weather_ok": "✅ Usunięto kategorię Pogoda.",
        "delete_clock_ok": "✅ Usunięto kategorię Kosmiczny Zegar.",
        "delete_stats_ok": "✅ Usunięto kategorię Statystyki.",
        "delete_all_ok": "✅ Usunięto wszystkie kategorie bota.",

        "language_set": "✅ Ustawiono język bota na: **Polski**",
        "language_invalid": "❌ Nieobsługiwany język. Dostępne: `pl`, `en`",

        "help_title": "📘 Pomoc • Kosmiczny Zegar 25 MAX",
        "help_desc": "Lista dostępnych komend slash.\nBot tworzy kanały z czasem, pogodą, fazą księżyca i statystykami serwera.",
        "help_general": "🌍 Komendy ogólne",
        "help_admin": "🛠️ Komendy administracyjne",
        "help_delete": "🗑️ Komendy usuwania",
        "help_start": "ℹ️ Jak zacząć",
        "help_footer": "Kosmiczny Zegar 25 MAX • Pomoc",

        "help_general_value": (
            "`/help` — pokazuje pomoc\n"
            "`/info` — informacje o bocie\n"
            "`/pogoda` — aktualna pogoda\n"
            "`/czas` — aktualny czas\n"
            "`/ksiezyc` — aktualna faza księżyca\n"
            "`/panel_statusow` — wysyła panel statusów\n"
            "`/wyczysc_moje_statusy` — czyści Twoje role z panelu"
        ),
        "help_admin_value": (
            "`/setup` — tworzy kategorie i kanały bota\n"
            "`/refresh` — odświeża wszystkie kanały bota\n"
            "`/status` — pokazuje status konfiguracji\n"
            "`/miasto` — ustawia miasto dla pogody i zegara\n"
            "`/language` — zmienia język bota"
        ),
        "help_delete_value": (
            "`/usun_pogoda` — usuwa kategorię Pogoda\n"
            "`/usun_kosmiczny_zegar` — usuwa kategorię Kosmiczny Zegar\n"
            "`/usun_statystyki` — usuwa kategorię Statystyki\n"
            "`/usun_wszystko` — usuwa wszystkie kategorie bota"
        ),
        "help_start_value": (
            "1. Użyj `/setup`\n"
            "2. Ustaw `/miasto` dla swojego serwera\n"
            "3. Użyj `/refresh`, aby ręcznie odświeżyć dane\n"
            "4. Użyj `/panel_statusow`, aby wysłać panel statusów"
        ),

        "status_title": "📊 Status Kosmicznego Zegara",
        "status_weather_cat": "Kategoria Pogoda",
        "status_clock_cat": "Kategoria Kosmiczny Zegar",
        "status_stats_cat": "Kategoria Statystyki",
        "status_saved_channels": "Zapisane kanały",
        "status_city": "Miasto",
        "status_lat": "Szerokość",
        "status_lon": "Długość",
        "status_timezone": "Strefa czasowa",
        "status_language": "Język",

        "info_title": "🌌 Kosmiczny Zegar 25 MAX",
        "info_desc": (
            "Nowoczesny bot Discord 24/7 do automatycznej prezentacji "
            "czasu, pogody, fazy księżyca, statystyk serwera oraz panelu "
            "statusów, nastroju i aktywności."
        ),
        "info_features": "✨ Najważniejsze funkcje",
        "info_status": "📈 Status bota",
        "info_modules": "🧩 Dostępne moduły",
        "info_author": "👨‍💻 Twórca",
        "info_version": "🤖 Wersja",
        "info_stability": "🛡️ Stabilność",
        "info_footer": "Kosmiczny Zegar 25 MAX • Bot Discord działający 24/7",
        "info_features_value": (
            "• 🛰️ Kosmiczny zegar w kanałach\n"
            "• 🌤️ Pogoda dla wybranego miasta\n"
            "• 🌙 Faza księżyca i długość dnia\n"
            "• 📊 Statystyki członków serwera\n"
            "• 🎭 Panel statusów, nastroju i aktywności\n"
            "• 🧹 Czyszczenie ról jednym kliknięciem\n"
            "• ⚡ Automatyczne aktualizacje 24/7\n"
            "• 🛠️ Wygodne komendy administracyjne"
        ),
        "info_status_value": (
            "**Uptime:** `{uptime}`\n"
            "**Serwery:** `{guilds}`\n"
            "**Użytkownicy:** `{users}`\n"
            "**Tryb pracy:** `Online 24/7`"
        ),
        "info_modules_value": (
            "`/help` `/setup` `/refresh` `/status` `/info`\n"
            "`/pogoda` `/czas` `/ksiezyc` `/miasto` `/language`\n"
            "`/panel_statusow` `/wyczysc_moje_statusy`\n"
            "`/usun_pogoda` `/usun_kosmiczny_zegar`\n"
            "`/usun_statystyki` `/usun_wszystko`"
        ),
        "info_stability_value": "Zoptymalizowany pod Railway i limity Discord API",

        "weather_title": "🌤️ Pogoda - {city}, {country}",
        "field_temperature": "Temperatura",
        "field_feels": "Odczuwalna",
        "field_clouds": "Zachmurzenie",
        "field_air": "Powietrze",
        "field_pollen": "Pylenie",
        "field_rain": "Opady",
        "field_wind": "Wiatr",
        "field_pressure": "Ciśnienie",
        "field_alerts": "Alerty",
        "field_alert_level": "Poziom alertu",
        "field_sunrise": "Wschód",
        "field_sunset": "Zachód",
        "field_day_length": "Długość dnia",
        "field_date": "Data",
        "none": "brak",

        "time_title": "🕐 Aktualny czas",
        "time_city": "Miasto",
        "time_clock": "Godzina",
        "time_date": "Data",
        "time_part_of_day": "Pora dnia",
        "time_timezone": "Strefa czasowa",

        "moon_new": "🌑 Faza księżyca nów",
        "moon_waxing_crescent": "🌒 Faza księżyca sierp przybywający",
        "moon_first_quarter": "🌓 Faza księżyca pierwsza kwadra",
        "moon_waxing_gibbous": "🌔 Faza księżyca garb przybywający",
        "moon_full": "🌕 Faza księżyca pełnia",
        "moon_waning_gibbous": "🌖 Faza księżyca garb ubywający",
        "moon_last_quarter": "🌗 Faza księżyca ostatnia kwadra",
        "moon_waning_crescent": "🌘 Faza księżyca sierp ubywający",
        "moon_unknown": "🌙 Faza księżyca --",

        "air_no_data": "⚪ Powietrze brak danych",
        "air_very_good": "🟢 Powietrze bardzo dobre",
        "air_good": "🟡 Powietrze dobre",
        "air_moderate": "🟠 Powietrze umiarkowane",
        "air_fair": "🔴 Powietrze dostateczne",
        "air_bad": "🟣 Powietrze złe",
        "air_very_bad": "⚫ Powietrze bardzo złe",

        "pollen_none": "brak",
        "pollen_low": "niskie",
        "pollen_medium": "średnie",
        "pollen_high": "wysokie",
        "pollen_very_high": "bardzo wysokie",

        "pollen_alder": "Olsza",
        "pollen_birch": "Brzoza",
        "pollen_grass": "Trawy",
        "pollen_mugwort": "Bylica",
        "pollen_ragweed": "Ambrozja",

        "weather_rain_none": "🌧 Opady brak",
        "weather_rain_text": "Opady",
        "weather_rain": "deszcz",
        "weather_snow": "śnieg",
        "weather_hail": "grad",
        "weather_precip": "opad",

        "part_dawn": "🌓 Pora dnia świt",
        "part_before_noon": "🌓 Pora dnia przed południem",
        "part_noon": "🌓 Pora dnia południe",
        "part_afternoon": "🌓 Pora dnia po południu",
        "part_dusk": "🌓 Pora dnia zmierzch",
        "part_night": "🌓 Pora dnia noc",

        "day_length_prefix": "☀️ Dzień",

        "alert_none": "🟢 ALERT brak",
        "alert_l1": "🟡 ALERT 1° ",
        "alert_l2": "🟠 ALERT 2° ",
        "alert_l3": "🔴 ALERT 3° ",

        "weekday_short": ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."],

        "stats_members": "👥 Wszyscy {count}",
        "stats_humans": "👤 Ludzie {count}",
        "stats_online": "🟢 Online {count}",
        "stats_bots": "🤖 Boty {count}",
        "stats_vc": "🔊 Na VC {count}",
        "stats_joined_today": "📥 Dzisiaj weszło {count}",
    },

    "en": {
        "lang_name": "English",
        "creator": "Mati",
        "bot_version": "v25 MAX",

        "cat_weather": "🌤️ Weather",
        "cat_clock": "🛰️ Cosmic Clock",
        "cat_stats": "📊 Statistics",

        "ch_temperature": "🌡 Temperature",
        "ch_feels": "🥵 Feels like",
        "ch_clouds": "☁ Clouds",
        "ch_air": "🌫 Air quality",
        "ch_pollen": "🌿 Pollen",
        "ch_rain": "🌧 Precipitation",
        "ch_wind": "💨 Wind",
        "ch_pressure": "⏱ Pressure",
        "ch_alerts": "🟢 ALERT none",

        "ch_date": "📅 Date",
        "ch_part_of_day": "🌓 Part of day",
        "ch_sunrise": "🌅 Sunrise",
        "ch_sunset": "🌇 Sunset",
        "ch_day_length": "☀️ Day length",
        "ch_moon": "🌙 Moon phase",

        "ch_members": "👥 Members",
        "ch_humans": "👤 Humans",
        "ch_online": "🟢 Online",
        "ch_bots": "🤖 Bots",
        "ch_vc": "🔊 In VC",
        "ch_joined_today": "📥 Joined today 0",

        "only_server": "❌ This command can only be used in a server.",
        "setup_ok": "✅ All categories and channels have been created and refreshed.",
        "setup_error": "❌ Setup error: {error}",
        "refresh_no_config": "ℹ️ No configuration found. Use `/setup` first.",
        "refresh_ok": "✅ All channels have been refreshed.",
        "refresh_error": "❌ Refresh error: {error}",
        "no_config": "ℹ️ No configuration found. Use `/setup`.",
        "city_setup_first": "ℹ️ Use `/setup` first to create categories and channels.",
        "city_not_found": "❌ City not found: `{city}`",
        "city_updated": "✅ City set to: **{city}** and weather plus clock were updated.",
        "city_error": "❌ Error while setting city: {error}",
        "weather_error": "❌ Weather fetch error: {error}",

        "delete_only_server": "❌ Server only.",
        "delete_no_config": "ℹ️ No configuration found.",
        "delete_weather_ok": "✅ Weather category deleted.",
        "delete_clock_ok": "✅ Cosmic Clock category deleted.",
        "delete_stats_ok": "✅ Statistics category deleted.",
        "delete_all_ok": "✅ All bot categories deleted.",

        "language_set": "✅ Bot language set to: **English**",
        "language_invalid": "❌ Unsupported language. Available: `pl`, `en`",

        "help_title": "📘 Help • Cosmic Clock 25 MAX",
        "help_desc": "List of available slash commands.\nThis bot creates channels with time, weather, moon phase and server statistics.",
        "help_general": "🌍 General commands",
        "help_admin": "🛠️ Admin commands",
        "help_delete": "🗑️ Delete commands",
        "help_start": "ℹ️ Getting started",
        "help_footer": "Cosmic Clock 25 MAX • Help",

        "help_general_value": (
            "`/help` — show help\n"
            "`/info` — bot information\n"
            "`/pogoda` — current weather\n"
            "`/czas` — current time\n"
            "`/ksiezyc` — current moon phase\n"
            "`/panel_statusow` — sends status panel\n"
            "`/wyczysc_moje_statusy` — clears your panel roles"
        ),
        "help_admin_value": (
            "`/setup` — create bot categories and channels\n"
            "`/refresh` — refresh all bot channels\n"
            "`/status` — show configuration status\n"
            "`/miasto` — set city for weather and clock\n"
            "`/language` — change bot language"
        ),
        "help_delete_value": (
            "`/usun_pogoda` — delete Weather category\n"
            "`/usun_kosmiczny_zegar` — delete Cosmic Clock category\n"
            "`/usun_statystyki` — delete Statistics category\n"
            "`/usun_wszystko` — delete all bot categories"
        ),
        "help_start_value": (
            "1. Use `/setup`\n"
            "2. Set `/miasto` for your server\n"
            "3. Use `/refresh` to manually refresh data\n"
            "4. Use `/panel_statusow` to send the status panel"
        ),

        "status_title": "📊 Cosmic Clock Status",
        "status_weather_cat": "Weather category",
        "status_clock_cat": "Cosmic Clock category",
        "status_stats_cat": "Statistics category",
        "status_saved_channels": "Saved channels",
        "status_city": "City",
        "status_lat": "Latitude",
        "status_lon": "Longitude",
        "status_timezone": "Timezone",
        "status_language": "Language",

        "info_title": "🌌 Cosmic Clock 25 MAX",
        "info_desc": (
            "A modern Discord bot running 24/7 for automatic display of "
            "time, weather, moon phase, server statistics and a status, "
            "mood and activity panel."
        ),
        "info_features": "✨ Main features",
        "info_status": "📈 Bot status",
        "info_modules": "🧩 Available modules",
        "info_author": "👨‍💻 Author",
        "info_version": "🤖 Version",
        "info_stability": "🛡️ Stability",
        "info_footer": "Cosmic Clock 25 MAX • Discord bot running 24/7",
        "info_features_value": (
            "• 🛰️ Cosmic clock channels\n"
            "• 🌤️ Weather for selected city\n"
            "• 🌙 Moon phase and day length\n"
            "• 📊 Server member statistics\n"
            "• 🎭 Status, mood and activity panel\n"
            "• 🧹 One-click role clearing\n"
            "• ⚡ Automatic 24/7 updates\n"
            "• 🛠️ Convenient admin commands"
        ),
        "info_status_value": (
            "**Uptime:** `{uptime}`\n"
            "**Servers:** `{guilds}`\n"
            "**Users:** `{users}`\n"
            "**Mode:** `Online 24/7`"
        ),
        "info_modules_value": (
            "`/help` `/setup` `/refresh` `/status` `/info`\n"
            "`/pogoda` `/czas` `/ksiezyc` `/miasto` `/language`\n"
            "`/panel_statusow` `/wyczysc_moje_statusy`\n"
            "`/usun_pogoda` `/usun_kosmiczny_zegar`\n"
            "`/usun_statystyki` `/usun_wszystko`"
        ),
        "info_stability_value": "Optimized for Railway and Discord API limits",

        "weather_title": "🌤️ Weather - {city}, {country}",
        "field_temperature": "Temperature",
        "field_feels": "Feels like",
        "field_clouds": "Cloud cover",
        "field_air": "Air quality",
        "field_pollen": "Pollen",
        "field_rain": "Precipitation",
        "field_wind": "Wind",
        "field_pressure": "Pressure",
        "field_alerts": "Alerts",
        "field_alert_level": "Alert level",
        "field_sunrise": "Sunrise",
        "field_sunset": "Sunset",
        "field_day_length": "Day length",
        "field_date": "Date",
        "none": "none",

        "time_title": "🕐 Current time",
        "time_city": "City",
        "time_clock": "Time",
        "time_date": "Date",
        "time_part_of_day": "Part of day",
        "time_timezone": "Timezone",

        "moon_new": "🌑 Moon phase new moon",
        "moon_waxing_crescent": "🌒 Moon phase waxing crescent",
        "moon_first_quarter": "🌓 Moon phase first quarter",
        "moon_waxing_gibbous": "🌔 Moon phase waxing gibbous",
        "moon_full": "🌕 Moon phase full moon",
        "moon_waning_gibbous": "🌖 Moon phase waning gibbous",
        "moon_last_quarter": "🌗 Moon phase last quarter",
        "moon_waning_crescent": "🌘 Moon phase waning crescent",
        "moon_unknown": "🌙 Moon phase --",

        "air_no_data": "⚪ Air quality no data",
        "air_very_good": "🟢 Air quality very good",
        "air_good": "🟡 Air quality good",
        "air_moderate": "🟠 Air quality moderate",
        "air_fair": "🔴 Air quality fair",
        "air_bad": "🟣 Air quality bad",
        "air_very_bad": "⚫ Air quality very bad",

        "pollen_none": "none",
        "pollen_low": "low",
        "pollen_medium": "medium",
        "pollen_high": "high",
        "pollen_very_high": "very high",

        "pollen_alder": "Alder",
        "pollen_birch": "Birch",
        "pollen_grass": "Grass",
        "pollen_mugwort": "Mugwort",
        "pollen_ragweed": "Ragweed",

        "weather_rain_none": "🌧 Precipitation none",
        "weather_rain_text": "Precipitation",
        "weather_rain": "rain",
        "weather_snow": "snow",
        "weather_hail": "hail",
        "weather_precip": "precip",

        "part_dawn": "🌓 Part of day dawn",
        "part_before_noon": "🌓 Part of day morning",
        "part_noon": "🌓 Part of day noon",
        "part_afternoon": "🌓 Part of day afternoon",
        "part_dusk": "🌓 Part of day dusk",
        "part_night": "🌓 Part of day night",

        "day_length_prefix": "☀️ Day",

        "alert_none": "🟢 ALERT none",
        "alert_l1": "🟡 ALERT 1° ",
        "alert_l2": "🟠 ALERT 2° ",
        "alert_l3": "🔴 ALERT 3° ",

        "weekday_short": ["Mon.", "Tue.", "Wed.", "Thu.", "Fri.", "Sat.", "Sun."],

        "stats_members": "👥 Members {count}",
        "stats_humans": "👤 Humans {count}",
        "stats_online": "🟢 Online {count}",
        "stats_bots": "🤖 Bots {count}",
        "stats_vc": "🔊 In VC {count}",
        "stats_joined_today": "📥 Joined today {count}",
    },
}

# ================================
# MAPY KLUCZY KANAŁÓW
# ================================

CHANNEL_TEMPLATE_KEYS = {
    "temperature": ("weather", "ch_temperature"),
    "feels": ("weather", "ch_feels"),
    "clouds": ("weather", "ch_clouds"),
    "air": ("weather", "ch_air"),
    "pollen": ("weather", "ch_pollen"),
    "rain": ("weather", "ch_rain"),
    "wind": ("weather", "ch_wind"),
    "pressure": ("weather", "ch_pressure"),
    "alerts": ("weather", "ch_alerts"),

    "date": ("clock", "ch_date"),
    "part_of_day": ("clock", "ch_part_of_day"),
    "sunrise": ("clock", "ch_sunrise"),
    "sunset": ("clock", "ch_sunset"),
    "day_length": ("clock", "ch_day_length"),
    "moon": ("clock", "ch_moon"),

    "members": ("stats", "ch_members"),
    "humans": ("stats", "ch_humans"),
    "online": ("stats", "ch_online"),
    "bots": ("stats", "ch_bots"),
    "vc": ("stats", "ch_vc"),
    "joined_today": ("stats", "ch_joined_today"),
}

# ================================
# PANEL STATUSÓW / NASTROJU / AKTYWNOŚCI MAX
# ================================

PANEL_ROLE_CONFIG = {
    "status": [
        {"key": "status_dostepny", "label": "Dostępny", "emoji": "🟢", "role_id": 1475627194582831184},
        {"key": "status_spie", "label": "Idę spać", "emoji": "🛌", "role_id": 1475627705188880547},
        {"key": "status_dnd", "label": "Nie przeszkadzać", "emoji": "🚫", "role_id": 1475627340494278727},
        {"key": "status_afk", "label": "AFK", "emoji": "😴", "role_id": 1475592478286676160},
        {"key": "status_brb", "label": "Zaraz wracam", "emoji": "⏳", "role_id": 1475595615055511747},
        {"key": "status_offpc", "label": "Poza kompem", "emoji": "📵", "role_id": 1475627428217884764},
        {"key": "status_offhome", "label": "Poza domem", "emoji": "🚗", "role_id": 1475627463865270404},
        {"key": "status_work", "label": "W pracy", "emoji": "💼", "role_id": 1475627537022582804},
        {"key": "status_school", "label": "W szkole", "emoji": "🏫", "role_id": 1475627641582391457},
    ],
    "mood": [
        {"key": "mood_chill", "label": "Na luzie", "emoji": "😎", "role_id": 1475616916348604618},
        {"key": "mood_happy", "label": "W dobrym humorze", "emoji": "😄", "role_id": 1475625302641086504},
        {"key": "mood_angry", "label": "Wkurzony", "emoji": "😤", "role_id": 1475625886886662324},
        {"key": "mood_tired", "label": "Zmęczony", "emoji": "🥶", "role_id": 1475625667075768395},
        {"key": "mood_energy", "label": "Full energia", "emoji": "⚡", "role_id": 1475625987914858677},
        {"key": "mood_night", "label": "Nocny tryb", "emoji": "🌙", "role_id": 1475626089597374680},
        {"key": "mood_sick", "label": "Chory", "emoji": "🤒", "role_id": 1475645832702328884},
    ],
    "activity": [
        {"key": "act_music", "label": "Słucham muzyki", "emoji": "🎧", "role_id": 1475586115569324043},
        {"key": "act_chat", "label": "Czatuję", "emoji": "💬", "role_id": 1475591441085366273},
        {"key": "act_game", "label": "Gram", "emoji": "🎮", "role_id": 1475591583314477278},
        {"key": "act_watch", "label": "Oglądam streama", "emoji": "👀", "role_id": 1475596164026859745},
        {"key": "act_study", "label": "Uczę się", "emoji": "📚", "role_id": 1475594865860542554},
        {"key": "act_vc", "label": "Na VC", "emoji": "🗣️", "role_id": 1475595019770396932},
        {"key": "act_stream", "label": "Streamuję", "emoji": "🎥", "role_id": 1475595081200304259},
        {"key": "act_people", "label": "Chcę poznać nowych ludzi", "emoji": "🤝", "role_id": 1475595483899494492},
        {"key": "act_new", "label": "Nowy tutaj", "emoji": "🆕", "role_id": 1475592165227761704},
    ]
}

ROLE_IDS = {
    group: {item["key"]: item["role_id"] for item in items}
    for group, items in PANEL_ROLE_CONFIG.items()
}


def get_group_role_ids(group_key: str) -> set[int]:
    return set(ROLE_IDS.get(group_key, {}).values())


def get_role_for_key(guild: discord.Guild, group_key: str, selected_key: str) -> discord.Role | None:
    role_id = ROLE_IDS.get(group_key, {}).get(selected_key)
    if not role_id:
        return None
    return guild.get_role(role_id)


def get_bot_member(guild: discord.Guild) -> discord.Member | None:
    if bot.user is None:
        return None
    return guild.get_member(bot.user.id)


def bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> tuple[bool, str]:
    me = get_bot_member(guild)
    if me is None:
        return False, "Bot nie został poprawnie zainicjalizowany na tym serwerze."

    if not me.guild_permissions.manage_roles:
        return False, "Bot nie ma uprawnienia **Zarządzanie rolami**."

    if role.managed:
        return False, f"Rola **{role.name}** jest rolą zarządzaną i nie można jej nadać ręcznie."

    if role >= me.top_role:
        return False, (
            f"Rola **{role.name}** jest wyżej lub na tym samym poziomie co najwyższa rola bota.\n"
            f"Przenieś rolę bota wyżej niż role z panelu."
        )

    return True, ""


async def clear_role_group(member: discord.Member, group_key: str) -> tuple[bool, str]:
    group_role_ids = get_group_role_ids(group_key)
    roles_to_remove = [role for role in member.roles if role.id in group_role_ids]

    if not roles_to_remove:
        return True, "Brak ról do usunięcia."

    try:
        await member.remove_roles(*roles_to_remove, reason="Kosmiczny Zegar - czyszczenie grupy ról")
        return True, "Usunięto role z grupy."
    except discord.Forbidden:
        return False, "Bot nie ma uprawnień do usuwania ról."
    except discord.HTTPException as e:
        return False, f"Błąd Discord API: {e}"


async def clear_all_panel_roles(member: discord.Member) -> tuple[bool, str]:
    all_role_ids: set[int] = set()
    for group_key in ROLE_IDS:
        all_role_ids.update(get_group_role_ids(group_key))

    roles_to_remove = [role for role in member.roles if role.id in all_role_ids]

    if not roles_to_remove:
        return True, "Brak ról panelu do usunięcia."

    try:
        await member.remove_roles(*roles_to_remove, reason="Kosmiczny Zegar - czyszczenie wszystkich ról panelu")
        return True, "Usunięto wszystkie role panelu."
    except discord.Forbidden:
        return False, "Bot nie ma uprawnień do usuwania ról."
    except discord.HTTPException as e:
        return False, f"Błąd Discord API: {e}"


async def replace_role_group_by_id(
    member: discord.Member,
    guild: discord.Guild,
    selected_key: str,
    group_key: str
) -> tuple[bool, str]:
    target_role = get_role_for_key(guild, group_key, selected_key)

    if target_role is None:
        return False, f"Brak ID roli dla opcji: {selected_key}"

    can_manage, reason = bot_can_manage_role(guild, target_role)
    if not can_manage:
        return False, reason

    group_role_ids = get_group_role_ids(group_key)

    roles_to_remove = [
        role for role in member.roles
        if role.id in group_role_ids and role.id != target_role.id
    ]

    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Kosmiczny Zegar - zmiana roli z panelu")

        if target_role not in member.roles:
            await member.add_roles(target_role, reason="Kosmiczny Zegar - nadanie roli z panelu")
            logging.info(
                f"[PANEL] {member} -> nadano rolę '{target_role.name}' "
                f"(group={group_key}, key={selected_key}, guild={guild.id})"
            )
            return True, target_role.name

        return True, f"{target_role.name} (już ustawione)"

    except discord.Forbidden:
        return False, "Bot nie ma uprawnień do nadawania/usuwania ról."
    except discord.HTTPException as e:
        return False, f"Błąd Discord API: {e}"


def build_select_options(group_key: str) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for item in PANEL_ROLE_CONFIG[group_key]:
        options.append(
            discord.SelectOption(
                label=item["label"],
                emoji=item["emoji"],
                value=item["key"]
            )
        )
    return options


class StatusAvailabilitySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Wybierz swój status...",
            min_values=1,
            max_values=1,
            options=build_select_options("status"),
            custom_id="kosmiczny_zegar_status_select"
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ta opcja działa tylko na serwerze.", ephemeral=True)
            return

        ok, message = await replace_role_group_by_id(
            member=interaction.user,
            guild=interaction.guild,
            selected_key=self.values[0],
            group_key="status"
        )

        if ok:
            await interaction.response.send_message(f"✅ Ustawiono status: **{message}**", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class StatusMoodSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Wybierz swój nastrój...",
            min_values=1,
            max_values=1,
            options=build_select_options("mood"),
            custom_id="kosmiczny_zegar_mood_select"
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ta opcja działa tylko na serwerze.", ephemeral=True)
            return

        ok, message = await replace_role_group_by_id(
            member=interaction.user,
            guild=interaction.guild,
            selected_key=self.values[0],
            group_key="mood"
        )

        if ok:
            await interaction.response.send_message(f"✅ Ustawiono nastrój: **{message}**", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class StatusActivitySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Wybierz swoją aktywność...",
            min_values=1,
            max_values=1,
            options=build_select_options("activity"),
            custom_id="kosmiczny_zegar_activity_select"
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ta opcja działa tylko na serwerze.", ephemeral=True)
            return

        ok, message = await replace_role_group_by_id(
            member=interaction.user,
            guild=interaction.guild,
            selected_key=self.values[0],
            group_key="activity"
        )

        if ok:
            await interaction.response.send_message(f"✅ Ustawiono aktywność: **{message}**", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class ClearStatusButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Wyczyść status",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
            custom_id="kosmiczny_zegar_clear_status"
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ta opcja działa tylko na serwerze.", ephemeral=True)
            return

        ok, message = await clear_role_group(interaction.user, "status")
        if ok:
            await interaction.response.send_message(f"✅ {message}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class ClearMoodButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Wyczyść nastrój",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
            custom_id="kosmiczny_zegar_clear_mood"
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ta opcja działa tylko na serwerze.", ephemeral=True)
            return

        ok, message = await clear_role_group(interaction.user, "mood")
        if ok:
            await interaction.response.send_message(f"✅ {message}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class ClearActivityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Wyczyść aktywność",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
            custom_id="kosmiczny_zegar_clear_activity"
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ta opcja działa tylko na serwerze.", ephemeral=True)
            return

        ok, message = await clear_role_group(interaction.user, "activity")
        if ok:
            await interaction.response.send_message(f"✅ {message}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class ClearAllPanelRolesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Wyczyść wszystko",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            custom_id="kosmiczny_zegar_clear_all_panel_roles"
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ta opcja działa tylko na serwerze.", ephemeral=True)
            return

        ok, message = await clear_all_panel_roles(interaction.user)
        if ok:
            await interaction.response.send_message(f"✅ {message}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class StatusPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(StatusAvailabilitySelect())
        self.add_item(StatusMoodSelect())
        self.add_item(StatusActivitySelect())
        self.add_item(ClearStatusButton())
        self.add_item(ClearMoodButton())
        self.add_item(ClearActivityButton())
        self.add_item(ClearAllPanelRolesButton())

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
        timezone TEXT,
        language TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS guild_daily_stats (
        guild_id INTEGER PRIMARY KEY,
        stats_date TEXT,
        joined_today_count INTEGER DEFAULT 0
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
    if "language" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN language TEXT")

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
            timezone,
            language
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
        "language": row[10] or DEFAULT_LANGUAGE,
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
        timezone,
        language
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        cfg.get("timezone", DEFAULT_TIMEZONE),
        cfg.get("language", DEFAULT_LANGUAGE)
    ))

    conn.commit()
    conn.close()

# ================================
# POMOCNICZE
# ================================

def get_lang_code(cfg: dict | None) -> str:
    if not cfg:
        return DEFAULT_LANGUAGE
    lang = cfg.get("language", DEFAULT_LANGUAGE)
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def tr(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE
    text = LANGUAGES[lang].get(key, LANGUAGES[DEFAULT_LANGUAGE].get(key, key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


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
        "language": DEFAULT_LANGUAGE,
    }


def get_timezone_object(timezone_name: str):
    try:
        return pytz.timezone(timezone_name)
    except Exception:
        return pytz.timezone(DEFAULT_TIMEZONE)


def get_channel_from_config(guild: discord.Guild, cfg: dict, key: str):
    channel_id = cfg["channels"].get(key)
    if not channel_id:
        logging.warning(f"Brak channel_id w config dla klucza: {key}")
        return None

    channel = guild.get_channel(channel_id)
    if channel is None:
        logging.warning(f"Nie znaleziono kanału w guild dla klucza: {key}, id: {channel_id}")

    return channel


def get_today_string_for_timezone(timezone_name: str) -> str:
    timezone_obj = get_timezone_object(timezone_name)
    return datetime.now(timezone_obj).strftime("%Y-%m-%d")


def get_joined_today_count(guild_id: int, timezone_name: str) -> int:
    today = get_today_string_for_timezone(timezone_name)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        SELECT stats_date, joined_today_count
        FROM guild_daily_stats
        WHERE guild_id=?
    """, (guild_id,))
    row = c.fetchone()

    if not row:
        c.execute("""
            INSERT INTO guild_daily_stats (guild_id, stats_date, joined_today_count)
            VALUES (?, ?, 0)
        """, (guild_id, today))
        conn.commit()
        conn.close()
        return 0

    stats_date, joined_today_count = row

    if stats_date != today:
        c.execute("""
            UPDATE guild_daily_stats
            SET stats_date=?, joined_today_count=0
            WHERE guild_id=?
        """, (today, guild_id))
        conn.commit()
        conn.close()
        return 0

    conn.close()
    return int(joined_today_count or 0)


def increment_joined_today_count(guild_id: int, timezone_name: str) -> int:
    today = get_today_string_for_timezone(timezone_name)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        SELECT stats_date, joined_today_count
        FROM guild_daily_stats
        WHERE guild_id=?
    """, (guild_id,))
    row = c.fetchone()

    if not row:
        new_count = 1
        c.execute("""
            INSERT INTO guild_daily_stats (guild_id, stats_date, joined_today_count)
            VALUES (?, ?, ?)
        """, (guild_id, today, new_count))
        conn.commit()
        conn.close()
        return new_count

    stats_date, joined_today_count = row

    if stats_date != today:
        new_count = 1
        c.execute("""
            UPDATE guild_daily_stats
            SET stats_date=?, joined_today_count=?
            WHERE guild_id=?
        """, (today, new_count, guild_id))
        conn.commit()
        conn.close()
        return new_count

    new_count = int(joined_today_count or 0) + 1
    c.execute("""
        UPDATE guild_daily_stats
        SET joined_today_count=?
        WHERE guild_id=?
    """, (new_count, guild_id))
    conn.commit()
    conn.close()
    return new_count


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


def get_category_name(lang: str, group_name: str) -> str:
    mapping = {
        "weather": tr(lang, "cat_weather"),
        "clock": tr(lang, "cat_clock"),
        "stats": tr(lang, "cat_stats"),
    }
    return mapping[group_name]


def get_channel_fallback_name(lang: str, key: str) -> str:
    _, translation_key = CHANNEL_TEMPLATE_KEYS[key]
    return tr(lang, translation_key)


async def fetch_json(url: str):
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            text = await response.text()
            lowered = text.lower()

            if text.startswith("<!DOCTYPE") or "<html" in lowered:
                raise RuntimeError("API returned HTML instead of JSON")

            try:
                return json.loads(text)
            except Exception as e:
                raise RuntimeError(f"Failed to parse JSON: {e}")


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
            "country": item.get("country", "Unknown country"),
            "admin1": item.get("admin1"),
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
            "timezone": item.get("timezone", DEFAULT_TIMEZONE),
        })

    return parsed


def trim_channel_name(text: str) -> str:
    return text[:MAX_CHANNEL_NAME_LEN]


def format_uptime(delta):
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    return " ".join(parts)

# ================================
# POWIETRZE / PYLENIE / OPADY / ALERTY
# ================================

def air_quality_text(eaqi, lang: str):
    if eaqi is None:
        return tr(lang, "air_no_data")

    value = float(eaqi)

    if value <= 20:
        return tr(lang, "air_very_good")
    if value <= 40:
        return tr(lang, "air_good")
    if value <= 60:
        return tr(lang, "air_moderate")
    if value <= 80:
        return tr(lang, "air_fair")
    if value <= 100:
        return tr(lang, "air_bad")
    return tr(lang, "air_very_bad")


def pollen_level_name(value: float, lang: str) -> str:
    if value <= 0:
        return tr(lang, "pollen_none")
    if value <= 10:
        return tr(lang, "pollen_low")
    if value <= 50:
        return tr(lang, "pollen_medium")
    if value <= 100:
        return tr(lang, "pollen_high")
    return tr(lang, "pollen_very_high")


def build_pollen_channel_text(alder, birch, grass, mugwort, ragweed, lang: str) -> str:
    pollens = [
        (tr(lang, "pollen_alder"), float(alder or 0)),
        (tr(lang, "pollen_birch"), float(birch or 0)),
        (tr(lang, "pollen_grass"), float(grass or 0)),
        (tr(lang, "pollen_mugwort"), float(mugwort or 0)),
        (tr(lang, "pollen_ragweed"), float(ragweed or 0)),
    ]

    active = [(name, value) for name, value in pollens if value > 0]

    if not active:
        return f"🌿 {tr(lang, 'field_pollen')} {tr(lang, 'none')}"

    active.sort(key=lambda x: x[1], reverse=True)

    formatted_items = [
        f"{name} {pollen_level_name(value, lang)}"
        for name, value in active
    ]

    base = f"🌿 {tr(lang, 'field_pollen')} "
    joined = " • ".join(formatted_items)
    text = base + joined

    if len(text) <= MAX_CHANNEL_NAME_LEN:
        return text

    trimmed: list[str] = []
    for item in formatted_items:
        candidate = base + " • ".join(trimmed + [item])
        if len(candidate) <= MAX_CHANNEL_NAME_LEN:
            trimmed.append(item)
        else:
            break

    remaining = len(formatted_items) - len(trimmed)
    if remaining > 0:
        suffix = f" +{remaining}"
        candidate = base + " • ".join(trimmed) + suffix
        if len(candidate) <= MAX_CHANNEL_NAME_LEN:
            return candidate

    return trim_channel_name(base + " • ".join(trimmed))


def format_precipitation_channel(current: dict, lang: str) -> str:
    weather_code = int(current.get("weather_code", -1)) if current.get("weather_code") is not None else -1
    precipitation = float(current.get("precipitation", 0) or 0)
    rain = float(current.get("rain", 0) or 0)
    showers = float(current.get("showers", 0) or 0)
    snowfall = float(current.get("snowfall", 0) or 0)

    rain_total = rain + showers

    hail_codes = {96, 99}
    snow_codes = {71, 73, 75, 77, 85, 86}
    rain_codes = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}

    has_hail = weather_code in hail_codes
    has_snow = snowfall > 0 or weather_code in snow_codes
    has_rain = rain_total > 0 or (precipitation > 0 and weather_code in rain_codes)

    if not has_rain and not has_snow and not has_hail and precipitation <= 0:
        return tr(lang, "weather_rain_none")

    parts = []

    if has_hail:
        parts.append(tr(lang, "weather_hail"))

    if has_rain:
        parts.append(f"{tr(lang, 'weather_rain')} {round(rain_total, 1)} mm")

    if has_snow:
        parts.append(f"{tr(lang, 'weather_snow')} {round(snowfall, 1)} cm")

    if not parts and precipitation > 0:
        parts.append(f"{tr(lang, 'weather_precip')} {round(precipitation, 1)} mm")

    text = f"{tr(lang, 'weather_rain_text')} " + " / ".join(parts)

    if has_hail and has_rain and has_snow:
        text = f"⛈🌧🌨 {text}"
    elif has_hail and has_rain:
        text = f"⛈🌧 {text}"
    elif has_hail and has_snow:
        text = f"⛈🌨 {text}"
    elif has_rain and has_snow:
        text = f"🌨🌧 {text}"
    elif has_hail:
        text = f"⛈ {text}"
    elif has_snow:
        text = f"🌨 {text}"
    else:
        text = f"🌧 {text}"

    return trim_channel_name(text)


def parse_hhmm_to_today(now: datetime, hhmm: str) -> datetime | None:
    try:
        hour, minute = map(int, hhmm.split(":"))
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return None


def fallback_part_of_day(hour: int, minute: int, lang: str) -> str:
    total_minutes = hour * 60 + minute

    if 4 * 60 <= total_minutes < 6 * 60:
        return tr(lang, "part_dawn")
    if 6 * 60 <= total_minutes < 11 * 60:
        return tr(lang, "part_before_noon")
    if 11 * 60 <= total_minutes < 13 * 60:
        return tr(lang, "part_noon")
    if 13 * 60 <= total_minutes < 18 * 60:
        return tr(lang, "part_afternoon")
    if 18 * 60 <= total_minutes < 20 * 60:
        return tr(lang, "part_dusk")
    return tr(lang, "part_night")


def format_part_of_day(now: datetime, lang: str, sunrise_str: str | None = None, sunset_str: str | None = None) -> str:
    sunrise = parse_hhmm_to_today(now, sunrise_str) if sunrise_str else None
    sunset = parse_hhmm_to_today(now, sunset_str) if sunset_str else None

    if sunrise is None or sunset is None or sunrise >= sunset:
        return fallback_part_of_day(now.hour, now.minute, lang)

    dawn_start = sunrise - timedelta(minutes=45)
    dawn_end = sunrise + timedelta(minutes=30)

    noon_start = now.replace(hour=11, minute=0, second=0, microsecond=0)
    noon_end = now.replace(hour=13, minute=0, second=0, microsecond=0)

    dusk_start = sunset - timedelta(minutes=45)
    dusk_end = sunset + timedelta(minutes=35)

    if now < dawn_start:
        return tr(lang, "part_night")
    if dawn_start <= now < dawn_end:
        return tr(lang, "part_dawn")
    if dawn_end <= now < noon_start:
        return tr(lang, "part_before_noon")
    if noon_start <= now < noon_end:
        return tr(lang, "part_noon")
    if noon_end <= now < dusk_start:
        return tr(lang, "part_afternoon")
    if dusk_start <= now < dusk_end:
        return tr(lang, "part_dusk")
    return tr(lang, "part_night")


def day_length_text(sunrise_str, sunset_str, lang: str):
    try:
        sunrise = datetime.strptime(sunrise_str, "%H:%M")
        sunset = datetime.strptime(sunset_str, "%H:%M")

        diff = sunset - sunrise
        minutes = int(diff.total_seconds() // 60)
        hours = minutes // 60
        mins = minutes % 60

        return f"{tr(lang, 'day_length_prefix')} {hours}h {mins}m"
    except Exception:
        return f"{tr(lang, 'day_length_prefix')} --"


def moon_phase_name(now: datetime, lang: str) -> str:
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
        0: tr(lang, "moon_new"),
        1: tr(lang, "moon_waxing_crescent"),
        2: tr(lang, "moon_first_quarter"),
        3: tr(lang, "moon_waxing_gibbous"),
        4: tr(lang, "moon_full"),
        5: tr(lang, "moon_waning_gibbous"),
        6: tr(lang, "moon_last_quarter"),
        7: tr(lang, "moon_waning_crescent"),
    }

    return phases.get(phase_index, tr(lang, "moon_unknown"))


def build_weather_alerts(current: dict) -> dict:
    alerts: list[str] = []
    level = 0

    weather_code = int(current.get("weather_code", -1)) if current.get("weather_code") is not None else -1
    temperature = float(current.get("temperature_2m", 999)) if current.get("temperature_2m") is not None else 999
    precipitation = float(current.get("precipitation", 0) or 0)
    rain = float(current.get("rain", 0) or 0)
    showers = float(current.get("showers", 0) or 0)
    snowfall = float(current.get("snowfall", 0) or 0)
    gusts = float(current.get("wind_gusts_10m", 0) or 0)
    visibility = float(current.get("visibility", 999999) or 999999)

    if weather_code in {45, 48} or visibility <= 1000:
        alerts.append("fog")
        level = max(level, 1)

    if snowfall > 0 and gusts >= 40:
        alerts.append("snow drift")
        level = max(level, 1)

    if weather_code in {56, 57, 66, 67} or (temperature <= 1 and precipitation > 0):
        alerts.append("ice")
        level = max(level, 2)

    if weather_code in {65, 82} or precipitation >= 10 or rain >= 10 or showers >= 10:
        alerts.append("heavy rain")
        level = max(level, 2)

    if weather_code in {75, 86} or snowfall >= 1.0:
        alerts.append("heavy snow")
        level = max(level, 2)

    if snowfall > 0 and gusts >= 55:
        alerts.append("blizzard")
        level = max(level, 2)

    if gusts >= 70:
        alerts.append("strong wind")
        level = max(level, 2)

    if weather_code in {95, 96, 99}:
        alerts.append("storm")
        level = max(level, 3)

    if weather_code in {96, 99}:
        alerts.append("hail")
        level = max(level, 3)

    if gusts >= 118:
        alerts.append("hurricane")
        level = max(level, 3)

    unique_alerts: list[str] = []
    for alert in alerts:
        if alert not in unique_alerts:
            unique_alerts.append(alert)

    return {
        "alerts": unique_alerts,
        "level": level
    }


def format_alerts_channel(alerts: list[str], level: int, lang: str) -> str:
    if not alerts or level == 0:
        return tr(lang, "alert_none")

    formatted_alerts = [f"❗{alert}" for alert in alerts]

    if level == 1:
        base = tr(lang, "alert_l1")
    elif level == 2:
        base = tr(lang, "alert_l2")
    else:
        base = tr(lang, "alert_l3")

    joined = " ".join(formatted_alerts)
    text = base + joined

    if len(text) <= MAX_CHANNEL_NAME_LEN:
        return text

    trimmed: list[str] = []
    for alert in formatted_alerts:
        candidate = base + " ".join(trimmed + [alert])
        if len(candidate) <= MAX_CHANNEL_NAME_LEN:
            trimmed.append(alert)
        else:
            break

    remaining = len(formatted_alerts) - len(trimmed)
    if remaining > 0:
        suffix = f" +{remaining}"
        candidate = base + " ".join(trimmed) + suffix
        if len(candidate) <= MAX_CHANNEL_NAME_LEN:
            return candidate

    return trim_channel_name(base + " ".join(trimmed))

# ================================
# POBIERANIE POGODY
# ================================

async def get_weather_data(city_name: str, latitude: float, longitude: float, timezone_name: str = DEFAULT_TIMEZONE, lang: str = DEFAULT_LANGUAGE):
    encoded_timezone = quote(timezone_name)

    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}"
        f"&longitude={longitude}"
        "&current=temperature_2m,apparent_temperature,precipitation,rain,showers,snowfall,"
        "wind_speed_10m,wind_gusts_10m,surface_pressure,cloud_cover,weather_code,visibility"
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

    alerts_data = build_weather_alerts(current)
    alerts = alerts_data["alerts"]
    alert_level = alerts_data["level"]

    return {
        "temperature": f"🌡 {city_name.upper()} {round(float(temp))}°C" if temp is not None else f"🌡 {city_name.upper()} --°C",
        "feels": f"🥵 {tr(lang, 'field_feels')} {round(float(feels))}°C" if feels is not None else f"🥵 {tr(lang, 'field_feels')} --°C",
        "clouds": f"☁ {tr(lang, 'field_clouds')} {round(float(clouds))}%" if clouds is not None else f"☁ {tr(lang, 'field_clouds')} --%",
        "air": air_quality_text(air_current.get("european_aqi"), lang),
        "pollen": build_pollen_channel_text(alder, birch, grass, mugwort, ragweed, lang),
        "rain": format_precipitation_channel(current, lang),
        "wind": f"💨 {tr(lang, 'field_wind')} {round(float(wind))} km/h" if wind is not None else f"💨 {tr(lang, 'field_wind')} -- km/h",
        "pressure": f"⏱ {tr(lang, 'field_pressure')} {round(float(pressure))} hPa" if pressure is not None else f"⏱ {tr(lang, 'field_pressure')} -- hPa",
        "alerts": format_alerts_channel(alerts, alert_level, lang),
        "alerts_list": alerts,
        "alert_level": alert_level,
        "sunrise": f"🌅 {tr(lang, 'field_sunrise')} {sunrise_time}",
        "sunset": f"🌇 {tr(lang, 'field_sunset')} {sunset_time}",
        "sunrise_time": sunrise_time,
        "sunset_time": sunset_time,
        "day_length": day_length_text(sunrise_time, sunset_time, lang)
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

    lang = get_lang_code(cfg)

    weather_category = guild.get_channel(cfg.get("weather_category_id")) if cfg.get("weather_category_id") else None
    clock_category = guild.get_channel(cfg.get("clock_category_id")) if cfg.get("clock_category_id") else None
    stats_category = guild.get_channel(cfg.get("stats_category_id")) if cfg.get("stats_category_id") else None

    if not isinstance(weather_category, discord.CategoryChannel):
        weather_category = await create_or_get_category(guild, get_category_name(lang, "weather"))
        cfg["weather_category_id"] = weather_category.id

    if not isinstance(clock_category, discord.CategoryChannel):
        clock_category = await create_or_get_category(guild, get_category_name(lang, "clock"))
        cfg["clock_category_id"] = clock_category.id

    if not isinstance(stats_category, discord.CategoryChannel):
        stats_category = await create_or_get_category(guild, get_category_name(lang, "stats"))
        cfg["stats_category_id"] = stats_category.id

    category_map = {
        "weather": weather_category,
        "clock": clock_category,
        "stats": stats_category
    }

    channels = dict(cfg.get("channels", {}))

    for key, (group_name, _) in CHANNEL_TEMPLATE_KEYS.items():
        target_category = category_map[group_name]
        fallback_name = get_channel_fallback_name(lang, key)

        current_channel = None
        channel_id = channels.get(key)

        if channel_id:
            current_channel = guild.get_channel(channel_id)

        if current_channel is None:
            current_channel = find_voice_channel_in_category_by_name(target_category, fallback_name)

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
        logging.warning(f"Nie znaleziono kanału do zmiany nazwy na: {new_name}")
        return

    new_name = trim_channel_name(new_name)

    if channel.name == new_name:
        logging.info(f"Bez zmian: {channel.name}")
        return

    try:
        old_name = channel.name
        await channel.edit(
            name=new_name,
            reason="Kosmiczny Zegar: aktualizacja nazwy kanału"
        )
        logging.info(f"Zmieniono kanał: {old_name} -> {new_name}")
        await asyncio.sleep(CHANNEL_EDIT_DELAY)

    except discord.HTTPException as e:
        logging.error(
            f"HTTPException przy zmianie kanału {getattr(channel, 'id', 'brak_id')} "
            f"({getattr(channel, 'name', 'brak_nazwy')}): {e}"
        )
        await asyncio.sleep(5)

    except Exception as e:
        logging.error(
            f"Błąd zmiany nazwy kanału {getattr(channel, 'id', 'brak_id')} "
            f"({getattr(channel, 'name', 'brak_nazwy')}): {e}"
        )

# ================================
# AKTUALIZACJA KANAŁÓW
# ================================

async def update_weather_channels(guild: discord.Guild, cfg: dict, weather: dict):
    for key in [
        "temperature",
        "feels",
        "clouds",
        "rain",
        "wind",
        "pressure",
        "air",
        "pollen",
        "alerts",
    ]:
        await safe_edit_channel_name(
            get_channel_from_config(guild, cfg, key),
            weather[key]
        )


async def update_clock_channels(guild: discord.Guild, cfg: dict, weather: dict):
    lang = get_lang_code(cfg)
    timezone_obj = get_timezone_object(cfg.get("timezone", DEFAULT_TIMEZONE))
    now = datetime.now(timezone_obj)
    weekdays = LANGUAGES[lang]["weekday_short"]

    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "date"),
        f"📅 {tr(lang, 'field_date')} {weekdays[now.weekday()]} {now.strftime('%d.%m.%Y')}"
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "part_of_day"),
        format_part_of_day(now, lang, weather.get("sunrise_time"), weather.get("sunset_time"))
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
        moon_phase_name(now, lang)
    )


async def update_stats_channels(guild: discord.Guild, cfg: dict):
    lang = get_lang_code(cfg)

    members = [m for m in guild.members]
    human_members = [m for m in members if not m.bot]
    bot_members = [m for m in members if m.bot]

    members_count = len(members)
    humans_count = len(human_members)
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

    joined_today_count = get_joined_today_count(
        guild.id,
        cfg.get("timezone", DEFAULT_TIMEZONE)
    )

    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "members"),
        tr(lang, "stats_members", count=members_count)
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "humans"),
        tr(lang, "stats_humans", count=humans_count)
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "online"),
        tr(lang, "stats_online", count=online_count)
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "bots"),
        tr(lang, "stats_bots", count=bots_count)
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "vc"),
        tr(lang, "stats_vc", count=vc_count)
    )
    await safe_edit_channel_name(
        get_channel_from_config(guild, cfg, "joined_today"),
        tr(lang, "stats_joined_today", count=joined_today_count)
    )


async def refresh_existing_panel(guild: discord.Guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        return False

    lang = get_lang_code(cfg)

    weather = await get_weather_data(
        city_name=cfg["city_name"],
        latitude=cfg["latitude"],
        longitude=cfg["longitude"],
        timezone_name=cfg.get("timezone", DEFAULT_TIMEZONE),
        lang=lang
    )

    await update_weather_channels(guild, cfg, weather)
    await update_clock_channels(guild, cfg, weather)
    await update_stats_channels(guild, cfg)

    return True


async def refresh_weather_and_clock_only(guild: discord.Guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        return False

    lang = get_lang_code(cfg)

    weather = await get_weather_data(
        city_name=cfg["city_name"],
        latitude=cfg["latitude"],
        longitude=cfg["longitude"],
        timezone_name=cfg.get("timezone", DEFAULT_TIMEZONE),
        lang=lang
    )

    await update_weather_channels(guild, cfg, weather)
    await update_clock_channels(guild, cfg, weather)

    return True


async def refresh_stats_only(guild: discord.Guild):
    cfg = get_guild_config(guild.id)
    if not cfg:
        return

    await update_stats_channels(guild, cfg)

# ================================
# STATUS ZEGARA BOTA
# ================================

@tasks.loop(seconds=5)
async def update_status_clock():
    timezone = pytz.timezone("Europe/Warsaw")
    now = datetime.now(timezone)

    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=f"🕒 {now.strftime('%H:%M:%S')}"
    )

    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=activity
        )
    except discord.HTTPException as e:
        logging.error(f"Błąd zmiany statusu bota: {e}")
    except Exception as e:
        logging.error(f"Nieoczekiwany błąd zmiany statusu bota: {e}")


@update_status_clock.before_loop
async def before_update_status_clock():
    await bot.wait_until_ready()

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

            await refresh_weather_and_clock_only(guild)

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
    except asyncio.CancelledError:
        return
    except Exception as e:
        logging.error(f"Błąd live stats dla {guild.id}: {e}")
    finally:
        stats_update_tasks.pop(guild.id, None)


def schedule_stats_refresh(guild: discord.Guild | None):
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
            app_commands.Choice(name="London, United Kingdom", value="London"),
            app_commands.Choice(name="New York, USA", value="New York"),
        ]

    try:
        results = await geocode_city(current, count=10)
        choices = []

        for item in results[:25]:
            label = item["name"] or "Unknown city"
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

@bot.tree.command(name="help", description="Shows bot help")
async def help_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    lang = get_lang_code(cfg)

    embed = discord.Embed(
        title=tr(lang, "help_title"),
        description=tr(lang, "help_desc"),
        color=discord.Color.green()
    )

    embed.add_field(
        name=tr(lang, "help_general"),
        value=tr(lang, "help_general_value"),
        inline=False
    )

    embed.add_field(
        name=tr(lang, "help_admin"),
        value=tr(lang, "help_admin_value"),
        inline=False
    )

    embed.add_field(
        name=tr(lang, "help_delete"),
        value=tr(lang, "help_delete_value"),
        inline=False
    )

    embed.add_field(
        name=tr(lang, "help_start"),
        value=tr(lang, "help_start_value"),
        inline=False
    )

    embed.set_footer(text=tr(lang, "help_footer"))

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setup", description="Creates bot categories and channels")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            tr(DEFAULT_LANGUAGE, "only_server"),
            ephemeral=True
        )
        return

    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    lang = get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)

    try:
        await setup_categories_and_channels(guild)
        await refresh_existing_panel(guild)

        await interaction.followup.send(
            tr(lang, "setup_ok"),
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            tr(lang, "setup_error", error=e),
            ephemeral=True
        )


@bot.tree.command(name="refresh", description="Refreshes all bot channels")
@app_commands.checks.has_permissions(manage_guild=True)
async def refresh_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            tr(DEFAULT_LANGUAGE, "only_server"),
            ephemeral=True
        )
        return

    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    lang = get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)

    try:
        refreshed = await refresh_existing_panel(guild)

        if not refreshed:
            await interaction.followup.send(
                tr(lang, "refresh_no_config"),
                ephemeral=True
            )
            return

        await interaction.followup.send(
            tr(lang, "refresh_ok"),
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            tr(lang, "refresh_error", error=e),
            ephemeral=True
        )


@bot.tree.command(name="status", description="Shows bot configuration status")
async def status_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            tr(DEFAULT_LANGUAGE, "only_server"),
            ephemeral=True
        )
        return

    cfg = get_guild_config(guild.id)

    if not cfg:
        await interaction.response.send_message(
            tr(DEFAULT_LANGUAGE, "no_config"),
            ephemeral=True
        )
        return

    lang = get_lang_code(cfg)

    embed = discord.Embed(
        title=tr(lang, "status_title"),
        color=discord.Color.blue()
    )
    embed.add_field(name=tr(lang, "status_weather_cat"), value=str(cfg.get("weather_category_id")), inline=False)
    embed.add_field(name=tr(lang, "status_clock_cat"), value=str(cfg.get("clock_category_id")), inline=False)
    embed.add_field(name=tr(lang, "status_stats_cat"), value=str(cfg.get("stats_category_id")), inline=False)
    embed.add_field(name=tr(lang, "status_saved_channels"), value=str(len(cfg.get("channels", {}))), inline=False)
    embed.add_field(name=tr(lang, "status_city"), value=f"{cfg.get('city_name', DEFAULT_CITY_NAME)}, {cfg.get('country', DEFAULT_COUNTRY)}", inline=False)
    embed.add_field(name=tr(lang, "status_lat"), value=str(cfg.get("latitude", DEFAULT_LATITUDE)), inline=True)
    embed.add_field(name=tr(lang, "status_lon"), value=str(cfg.get("longitude", DEFAULT_LONGITUDE)), inline=True)
    embed.add_field(name=tr(lang, "status_timezone"), value=str(cfg.get("timezone", DEFAULT_TIMEZONE)), inline=False)
    embed.add_field(name=tr(lang, "status_language"), value=tr(lang, "lang_name"), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="info", description="Shows bot information")
async def info_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    lang = get_lang_code(cfg)

    uptime = datetime.now(UTC) - bot_start_time
    uptime_str = format_uptime(uptime)

    guild_count = len(bot.guilds)
    user_count = sum(guild.member_count or 0 for guild in bot.guilds)

    embed = discord.Embed(
        title=tr(lang, "info_title"),
        description=tr(lang, "info_desc"),
        color=discord.Color.blurple()
    )

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    elif bot.user and bot.user.default_avatar:
        embed.set_thumbnail(url=bot.user.default_avatar.url)

    embed.add_field(
        name=tr(lang, "info_features"),
        value=tr(lang, "info_features_value"),
        inline=False
    )

    embed.add_field(
        name=tr(lang, "info_status"),
        value=tr(lang, "info_status_value", uptime=uptime_str, guilds=guild_count, users=user_count),
        inline=False
    )

    embed.add_field(
        name=tr(lang, "info_modules"),
        value=tr(lang, "info_modules_value"),
        inline=False
    )

    embed.add_field(
        name=tr(lang, "info_author"),
        value=f"**{tr(lang, 'creator')}**",
        inline=True
    )

    embed.add_field(
        name=tr(lang, "info_version"),
        value=f"**{tr(lang, 'bot_version')}**",
        inline=True
    )

    embed.add_field(
        name=tr(lang, "info_stability"),
        value=tr(lang, "info_stability_value"),
        inline=False
    )

    embed.set_footer(text=tr(lang, "info_footer"))

    await interaction.response.send_message(embed=embed, ephemeral=False)


@bot.tree.command(name="pogoda", description="Shows current weather")
async def weather_command(interaction: discord.Interaction):
    try:
        guild = interaction.guild
        cfg = get_guild_config(guild.id) if guild else None
        lang = get_lang_code(cfg)

        city_name = cfg["city_name"] if cfg else DEFAULT_CITY_NAME
        latitude = cfg["latitude"] if cfg else DEFAULT_LATITUDE
        longitude = cfg["longitude"] if cfg else DEFAULT_LONGITUDE
        timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
        country = cfg["country"] if cfg else DEFAULT_COUNTRY

        weather = await get_weather_data(
            city_name=city_name,
            latitude=latitude,
            longitude=longitude,
            timezone_name=timezone_name,
            lang=lang
        )

        embed = discord.Embed(
            title=tr(lang, "weather_title", city=city_name, country=country),
            color=discord.Color.teal()
        )
        embed.add_field(name=tr(lang, "field_temperature"), value=weather["temperature"], inline=False)
        embed.add_field(name=tr(lang, "field_feels"), value=weather["feels"], inline=False)
        embed.add_field(name=tr(lang, "field_clouds"), value=weather["clouds"], inline=False)
        embed.add_field(name=tr(lang, "field_air"), value=weather["air"], inline=False)
        embed.add_field(name=tr(lang, "field_pollen"), value=weather["pollen"], inline=False)
        embed.add_field(name=tr(lang, "field_rain"), value=weather["rain"], inline=False)
        embed.add_field(name=tr(lang, "field_wind"), value=weather["wind"], inline=False)
        embed.add_field(name=tr(lang, "field_pressure"), value=weather["pressure"], inline=False)
        embed.add_field(name=tr(lang, "field_alerts"), value=", ".join(weather["alerts_list"]) if weather["alerts_list"] else tr(lang, "none"), inline=False)
        embed.add_field(name=tr(lang, "field_alert_level"), value=f"{weather['alert_level']}°" if weather["alert_level"] > 0 else tr(lang, "none"), inline=False)
        embed.add_field(name=tr(lang, "field_sunrise"), value=weather["sunrise"], inline=False)
        embed.add_field(name=tr(lang, "field_sunset"), value=weather["sunset"], inline=False)
        embed.add_field(name=tr(lang, "field_day_length"), value=weather["day_length"], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        lang = get_lang_code(get_guild_config(interaction.guild.id)) if interaction.guild else DEFAULT_LANGUAGE
        await interaction.response.send_message(
            tr(lang, "weather_error", error=e),
            ephemeral=True
        )


@bot.tree.command(name="czas", description="Shows current time")
async def time_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    lang = get_lang_code(cfg)

    timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
    city_name = cfg["city_name"] if cfg else DEFAULT_CITY_NAME

    timezone_obj = get_timezone_object(timezone_name)
    now = datetime.now(timezone_obj)

    sunrise_time = None
    sunset_time = None
    try:
        weather = await get_weather_data(
            city_name=city_name,
            latitude=cfg["latitude"] if cfg else DEFAULT_LATITUDE,
            longitude=cfg["longitude"] if cfg else DEFAULT_LONGITUDE,
            timezone_name=timezone_name,
            lang=lang
        )
        sunrise_time = weather.get("sunrise_time")
        sunset_time = weather.get("sunset_time")
    except Exception:
        pass

    embed = discord.Embed(
        title=tr(lang, "time_title"),
        color=discord.Color.orange()
    )
    embed.add_field(name=tr(lang, "time_city"), value=city_name, inline=False)
    embed.add_field(name=tr(lang, "time_clock"), value=now.strftime("%H:%M:%S"), inline=False)
    embed.add_field(name=tr(lang, "time_date"), value=now.strftime("%d.%m.%Y"), inline=False)
    embed.add_field(name=tr(lang, "time_part_of_day"), value=format_part_of_day(now, lang, sunrise_time, sunset_time), inline=False)
    embed.add_field(name=tr(lang, "time_timezone"), value=timezone_name, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ksiezyc", description="Shows current moon phase")
async def moon_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    lang = get_lang_code(cfg)

    timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
    timezone_obj = get_timezone_object(timezone_name)
    now = datetime.now(timezone_obj)

    await interaction.response.send_message(
        moon_phase_name(now, lang),
        ephemeral=True
    )


@bot.tree.command(name="miasto", description="Sets city for weather and clock on this server")
@app_commands.describe(nazwa="City name, e.g. Rzeszów, London, Tokyo")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.autocomplete(nazwa=city_autocomplete)
async def city_command(interaction: discord.Interaction, nazwa: str):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            tr(DEFAULT_LANGUAGE, "only_server"),
            ephemeral=True
        )
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message(
            tr(DEFAULT_LANGUAGE, "city_setup_first"),
            ephemeral=True
        )
        return

    lang = get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)

    try:
        results = await geocode_city(nazwa, count=10)

        if not results:
            await interaction.followup.send(
                tr(lang, "city_not_found", city=nazwa),
                ephemeral=True
            )
            return

        city = results[0]

        cfg["city_name"] = city["name"] or DEFAULT_CITY_NAME
        cfg["latitude"] = city["latitude"] if city["latitude"] is not None else DEFAULT_LATITUDE
        cfg["longitude"] = city["longitude"] if city["longitude"] is not None else DEFAULT_LONGITUDE
        cfg["country"] = city.get("country", DEFAULT_COUNTRY)
        cfg["timezone"] = city.get("timezone", DEFAULT_TIMEZONE)

        save_guild_config(guild.id, cfg)

        refreshed = await refresh_weather_and_clock_only(guild)

        if not refreshed:
            await interaction.followup.send(
                tr(lang, "refresh_no_config"),
                ephemeral=True
            )
            return

        extra = ""
        if city.get("admin1"):
            extra = f", {city['admin1']}"

        await interaction.followup.send(
            tr(lang, "city_updated", city=f"{city['name']}{extra}, {city['country']}"),
            ephemeral=True
        )

    except Exception as e:
        await interaction.followup.send(
            tr(lang, "city_error", error=e),
            ephemeral=True
        )


@bot.tree.command(name="language", description="Changes bot language for this server")
@app_commands.describe(code="Language code: pl or en")
@app_commands.checks.has_permissions(manage_guild=True)
async def language_command(interaction: discord.Interaction, code: str):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            tr(DEFAULT_LANGUAGE, "only_server"),
            ephemeral=True
        )
        return

    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)

    code = code.lower().strip()
    if code not in LANGUAGES:
        await interaction.response.send_message(
            tr(get_lang_code(cfg), "language_invalid"),
            ephemeral=True
        )
        return

    cfg["language"] = code
    save_guild_config(guild.id, cfg)

    await interaction.response.defer(ephemeral=True)

    try:
        if cfg.get("channels"):
            await refresh_existing_panel(guild)
    except Exception as e:
        logging.error(f"Błąd odświeżania po zmianie języka: {e}")

    await interaction.followup.send(
        tr(code, "language_set"),
        ephemeral=True
    )


@bot.tree.command(name="panel_statusow", description="Wysyła panel wyboru statusów, nastroju i aktywności")
@app_commands.checks.has_permissions(manage_guild=True)
async def panel_statusow_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛰️ Panel statusów • Kosmiczny Zegar MAX",
        description=(
            "Wybierz z menu swój **status**, **nastrój** i **aktywność**.\n\n"
            "• w każdej grupie możesz mieć tylko **jedną** rolę\n"
            "• nowy wybór usuwa poprzednią rolę z tej samej grupy\n"
            "• na dole masz przyciski do szybkiego czyszczenia ról"
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🟢 Status",
        value="Dostępny, AFK, Idę spać, W pracy, W szkole i inne",
        inline=False
    )
    embed.add_field(
        name="😎 Nastrój",
        value="Na luzie, W dobrym humorze, Wkurzony, Chory i inne",
        inline=False
    )
    embed.add_field(
        name="🎮 Aktywność",
        value="Gram, Czatuję, Streamuję, Na VC, Uczę się i inne",
        inline=False
    )
    embed.add_field(
        name="🧹 Czyszczenie",
        value="Możesz wyczyścić osobno status, nastrój, aktywność albo wszystko.",
        inline=False
    )

    embed.set_footer(text="Kosmiczny Zegar 25 MAX • Panel ról")

    await interaction.response.send_message(embed=embed, view=StatusPanelView())


@bot.tree.command(name="wyczysc_moje_statusy", description="Czyści Twoje role z panelu statusów")
async def clear_my_panel_roles_command(interaction: discord.Interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    ok, message = await clear_all_panel_roles(interaction.user)
    if ok:
        await interaction.response.send_message(f"✅ {message}", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ {message}", ephemeral=True)

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
        key for key, (category_key, _) in CHANNEL_TEMPLATE_KEYS.items()
        if category_key == group_name
    ]

    for key in keys_to_remove:
        channels.pop(key, None)

    cfg["channels"] = channels
    return cfg


@bot.tree.command(name="usun_pogoda", description="Deletes Weather category with channels")
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_weather_category_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "delete_only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "delete_no_config"), ephemeral=True)
        return

    lang = get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)

    await delete_category_with_channels(guild, cfg.get("weather_category_id"))
    cfg["weather_category_id"] = None
    cfg = remove_channel_keys_by_group(cfg, "weather")
    save_guild_config(guild.id, cfg)

    await interaction.followup.send(tr(lang, "delete_weather_ok"), ephemeral=True)


@bot.tree.command(name="usun_kosmiczny_zegar", description="Deletes Cosmic Clock category with channels")
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_clock_category_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "delete_only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "delete_no_config"), ephemeral=True)
        return

    lang = get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)

    await delete_category_with_channels(guild, cfg.get("clock_category_id"))
    cfg["clock_category_id"] = None
    cfg = remove_channel_keys_by_group(cfg, "clock")
    save_guild_config(guild.id, cfg)

    await interaction.followup.send(tr(lang, "delete_clock_ok"), ephemeral=True)


@bot.tree.command(name="usun_statystyki", description="Deletes Statistics category with channels")
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_stats_category_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "delete_only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "delete_no_config"), ephemeral=True)
        return

    lang = get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)

    await delete_category_with_channels(guild, cfg.get("stats_category_id"))
    cfg["stats_category_id"] = None
    cfg = remove_channel_keys_by_group(cfg, "stats")
    save_guild_config(guild.id, cfg)

    await interaction.followup.send(tr(lang, "delete_stats_ok"), ephemeral=True)


@bot.tree.command(name="usun_wszystko", description="Deletes all bot categories")
@app_commands.checks.has_permissions(manage_guild=True)
async def delete_all_command(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "delete_only_server"), ephemeral=True)
        return

    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "delete_no_config"), ephemeral=True)
        return

    lang = get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)

    await delete_category_with_channels(guild, cfg.get("weather_category_id"))
    await delete_category_with_channels(guild, cfg.get("clock_category_id"))
    await delete_category_with_channels(guild, cfg.get("stats_category_id"))

    cfg["weather_category_id"] = None
    cfg["clock_category_id"] = None
    cfg["stats_category_id"] = None
    cfg["channels"] = {}

    save_guild_config(guild.id, cfg)

    await interaction.followup.send(tr(lang, "delete_all_ok"), ephemeral=True)

# ================================
# EVENTY LIVE STATYSTYK
# ================================

@bot.event
async def on_member_join(member: discord.Member):
    cfg = get_guild_config(member.guild.id)
    timezone_name = cfg.get("timezone", DEFAULT_TIMEZONE) if cfg else DEFAULT_TIMEZONE

    increment_joined_today_count(member.guild.id, timezone_name)
    schedule_stats_refresh(member.guild)


@bot.event
async def on_member_remove(member: discord.Member):
    schedule_stats_refresh(member.guild)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if before.channel != after.channel:
        schedule_stats_refresh(member.guild)


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    if before.status != after.status:
        schedule_stats_refresh(after.guild)

# ================================
# START BOTA
# ================================

@bot.event
async def on_ready():
    if bot.user is None:
        logging.error("Bot.user jest None w on_ready")
        return

    logging.info(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")

    try:
        synced = await bot.tree.sync()
        logging.info(f"Zsynchronizowano {len(synced)} komend")
        for cmd in synced:
            logging.info(f"Komenda aktywna: /{cmd.name}")
    except Exception as e:
        logging.error(f"Błąd synchronizacji komend: {e}")

    bot.add_view(StatusPanelView())

    if not auto_refresh.is_running():
        auto_refresh.start()

    if not update_status_clock.is_running():
        update_status_clock.start()

    for guild in bot.guilds:
        try:
            await refresh_stats_only(guild)
        except Exception as e:
            logging.error(f"Błąd odświeżania statystyk po starcie dla {guild.id}: {e}")


init_db()
bot.run(TOKEN)
