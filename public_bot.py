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
# KOSMICZNY ZEGAR PUBLIC - BOT v25
# MULTILANGUAGE: PL / EN
# FULL + SYSTEM STATUSÓW
# ================================
# WAŻNE:
# 1. Ustaw DISCORD_TOKEN w zmiennych środowiskowych.
# 2. Wstaw prawdziwe ID ról w STATUS_ROLES / MOOD_ROLES / ACTIVITY_ROLES.
# 3. Bot musi mieć uprawnienie: Zarządzanie rolami.
# 4. Rola bota musi być WYŻEJ niż role statusowe.
# ================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TOKEN = os.getenv("DISCORD_TOKEN")
DB_FILE = os.getenv("DB_FILE", "bot_data_public.db")

DEFAULT_CITY_NAME = "Rzeszów"
DEFAULT_LATITUDE = 50.0413
DEFAULT_LONGITUDE = 21.9990
DEFAULT_COUNTRY = "Polska"
DEFAULT_TIMEZONE = "Europe/Warsaw"
DEFAULT_LANGUAGE = "pl"

WEATHER_REFRESH_MINUTES = 15
CHANNEL_EDIT_DELAY = 1.2
STATS_REFRESH_DEBOUNCE_SECONDS = 10
MAX_CHANNEL_NAME_LENGTH = 100
STATUS_CITY_NAME = "Warszawa"

bot_start_time = datetime.now(UTC)
stats_update_tasks: dict[int, asyncio.Task] = {}
channel_edit_locks: dict[int, asyncio.Lock] = {}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.presences = False

bot = commands.Bot(command_prefix="!", intents=intents)

# ================================
# SYSTEM STATUSÓW / PANEL RÓL
# UZUPEŁNIJ PRAWDZIWE ID RÓL
# ================================

STATUS_ROLES = {
    "dostepny": 111111111111111111,
    "afk": 111111111111111112,
    "ide_spac": 111111111111111113,
    "w_pracy": 111111111111111114,
    "w_szkole": 111111111111111115,
}

MOOD_ROLES = {
    "na_luzie": 111111111111111121,
    "w_dobrym_humorze": 111111111111111122,
    "wkurzony": 111111111111111123,
    "chory": 111111111111111124,
    "zmeczony": 111111111111111125,
}

ACTIVITY_ROLES = {
    "gram": 111111111111111131,
    "czatuje": 111111111111111132,
    "streamuje": 111111111111111133,
    "na_vc": 111111111111111134,
    "ucze_sie": 111111111111111135,
    "pracuje": 111111111111111136,
}

ROLE_GROUPS = {
    "status": STATUS_ROLES,
    "mood": MOOD_ROLES,
    "activity": ACTIVITY_ROLES,
}

GROUP_LABELS = {
    "status": "🟢 Status",
    "mood": "😎 Nastrój",
    "activity": "🎮 Aktywność",
}

ROLE_DISPLAY_NAMES = {
    "dostepny": "Dostępny",
    "afk": "AFK",
    "ide_spac": "Idę spać",
    "w_pracy": "W pracy",
    "w_szkole": "W szkole",
    "na_luzie": "Na luzie",
    "w_dobrym_humorze": "W dobrym humorze",
    "wkurzony": "Wkurzony",
    "chory": "Chory",
    "zmeczony": "Zmęczony",
    "gram": "Gram",
    "czatuje": "Czatuję",
    "streamuje": "Streamuję",
    "na_vc": "Na VC",
    "ucze_sie": "Uczę się",
    "pracuje": "Pracuję",
}

ROLE_EMOJIS = {
    "dostepny": "🟢",
    "afk": "🌙",
    "ide_spac": "😴",
    "w_pracy": "💼",
    "w_szkole": "🎒",
    "na_luzie": "😎",
    "w_dobrym_humorze": "😁",
    "wkurzony": "😡",
    "chory": "🤒",
    "zmeczony": "🥱",
    "gram": "🎮",
    "czatuje": "💬",
    "streamuje": "📺",
    "na_vc": "🎧",
    "ucze_sie": "📚",
    "pracuje": "🛠️",
}

# ================================
# MAPA KANAŁÓW
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

LANGUAGES = {
    "pl": {
        "lang_name": "Polski",
        "creator": "Mati",
        "bot_version": "v25",
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
        "help_title": "📘 Pomoc • Kosmiczny Zegar 24",
        "help_desc": "Lista dostępnych komend slash. Bot tworzy kanały z czasem, pogodą, fazą księżyca, statystykami i panelem statusów.",
        "help_general": "🌍 Komendy ogólne",
        "help_admin": "🛠️ Komendy administracyjne",
        "help_delete": "🗑️ Komendy usuwania",
        "help_start": "ℹ️ Jak zacząć",
        "help_footer": "Kosmiczny Zegar 24 • Pomoc",
        "help_general_value": (
            "`/help` — pokazuje pomoc
"
            "`/info` — informacje o bocie
"
            "`/pogoda` — aktualna pogoda
"
            "`/czas` — aktualny czas
"
            "`/ksiezyc` — aktualna faza księżyca
"
            "`/pokaz_statusy` — statystyki ról statusowych
"
            "`/ustaw_status_swoj` — ustaw ręcznie swój status"
        ),
        "help_admin_value": (
            "`/setup` — tworzy kategorie i kanały bota
"
            "`/refresh` — odświeża wszystkie kanały bota
"
            "`/status` — pokazuje status konfiguracji
"
            "`/miasto` — ustawia miasto dla pogody i zegara
"
            "`/language` — zmienia język bota
"
            "`/panel_statusow` — wysyła panel statusów"
        ),
        "help_delete_value": (
            "`/usun_pogoda` — usuwa kategorię Pogoda
"
            "`/usun_kosmiczny_zegar` — usuwa kategorię Kosmiczny Zegar
"
            "`/usun_statystyki` — usuwa kategorię Statystyki
"
            "`/usun_wszystko` — usuwa wszystkie kategorie bota"
        ),
        "help_start_value": (
            "1. Użyj `/setup`
"
            "2. Ustaw `/miasto` dla swojego serwera
"
            "3. Użyj `/refresh`, aby ręcznie odświeżyć dane
"
            "4. Wyślij `/panel_statusow`, jeśli chcesz panel ról"
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
        "info_title": "🌌 Kosmiczny Zegar 24",
        "info_desc": "Nowoczesny bot Discord 24/7 do automatycznej prezentacji czasu, pogody, fazy księżyca, statystyk serwera i panelu statusów.",
        "info_features": "✨ Najważniejsze funkcje",
        "info_status": "📈 Status bota",
        "info_modules": "🧩 Dostępne moduły",
        "info_author": "👨‍💻 Twórca",
        "info_version": "🤖 Wersja",
        "info_stability": "🛡️ Stabilność",
        "info_footer": "Kosmiczny Zegar 24 • Bot Discord działający 24/7",
        "info_features_value": (
            "• 🛰️ Kosmiczny zegar w kanałach
"
            "• 🌤️ Pogoda dla wybranego miasta
"
            "• 🌙 Faza księżyca i długość dnia
"
            "• 📊 Statystyki członków serwera
"
            "• 🧩 Panel statusów, nastroju i aktywności
"
            "• ⚡ Automatyczne aktualizacje 24/7"
        ),
        "info_status_value": (
            "**Uptime:** `{uptime}`
"
            "**Serwery:** `{guilds}`
"
            "**Użytkownicy:** `{users}`
"
            "**Tryb pracy:** `Online 24/7`"
        ),
        "info_modules_value": (
            "`/help` `/setup` `/refresh` `/status` `/info`
"
            "`/pogoda` `/czas` `/ksiezyc` `/miasto` `/language`
"
            "`/panel_statusow` `/pokaz_statusy` `/ustaw_status_swoj`
"
            "`/usun_pogoda` `/usun_kosmiczny_zegar` `/usun_statystyki` `/usun_wszystko`"
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
        "role_panel_server_only": "Ta komenda działa tylko na serwerze.",
        "role_bad_option": "Nieprawidłowa opcja roli.",
        "role_not_found": "Nie znaleziono roli na serwerze. Sprawdź ID roli w kodzie.",
        "role_no_manage": "Bot nie ma uprawnienia **Zarządzanie rolami**.",
        "role_hierarchy": "Bot nie może nadać roli **{role}**. Przesuń rolę bota wyżej niż role statusowe.",
        "role_forbidden": "Bot nie ma uprawnień do nadania lub usunięcia tej roli.",
        "role_http_error": "Wystąpił błąd Discord API: `{error}`",
        "role_set_ok": "{emoji} Ustawiono: **{label}**",
        "role_panel_title": "🛠️ Panel statusów • Kosmiczny Zegar",
        "role_panel_desc": "Wybierz z menu swój **status, nastrój i aktywność**.

• w każdej grupie możesz mieć tylko **jedną** rolę
• wybranie nowej opcji automatycznie usuwa starą z tej samej grupy
• możesz też użyć komendy **/ustaw_status_swoj**",
        "role_panel_footer": "Kosmiczny Zegar 24 • Panel ról",
        "role_stats_title": "📊 Statystyki ról statusowych",
        "role_stats_desc": "Poniżej widzisz, ile osób ma każdą rolę z panelu.",
    },
    "en": {
        "lang_name": "English",
        "creator": "Mati",
        "bot_version": "v25",
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
        "help_title": "📘 Help • Cosmic Clock 24",
        "help_desc": "List of available slash commands.",
        "help_general": "🌍 General commands",
        "help_admin": "🛠️ Admin commands",
        "help_delete": "🗑️ Delete commands",
        "help_start": "ℹ️ Getting started",
        "help_footer": "Cosmic Clock 24 • Help",
        "help_general_value": "`/help` `/info` `/pogoda` `/czas` `/ksiezyc` `/pokaz_statusy` `/ustaw_status_swoj`",
        "help_admin_value": "`/setup` `/refresh` `/status` `/miasto` `/language` `/panel_statusow`",
        "help_delete_value": "`/usun_pogoda` `/usun_kosmiczny_zegar` `/usun_statystyki` `/usun_wszystko`",
        "help_start_value": "1. Use `/setup`
2. Set `/miasto`
3. Use `/refresh`
4. Send `/panel_statusow`",
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
        "info_title": "🌌 Cosmic Clock 24",
        "info_desc": "Modern Discord bot with weather, clock, stats and status panel.",
        "info_features": "✨ Main features",
        "info_status": "📈 Bot status",
        "info_modules": "🧩 Available modules",
        "info_author": "👨‍💻 Author",
        "info_version": "🤖 Version",
        "info_stability": "🛡️ Stability",
        "info_footer": "Cosmic Clock 24 • Discord bot running 24/7",
        "info_features_value": "• Weather
• Clock
• Moon phase
• Server stats
• Status panel
• 24/7 updates",
        "info_status_value": "**Uptime:** `{uptime}`
**Servers:** `{guilds}`
**Users:** `{users}`
**Mode:** `Online 24/7`",
        "info_modules_value": "`/help` `/setup` `/refresh` `/status` `/info` `/pogoda` `/czas` `/ksiezyc` `/miasto` `/language` `/panel_statusow` `/pokaz_statusy` `/ustaw_status_swoj`",
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
        "role_panel_server_only": "This command works only in a server.",
        "role_bad_option": "Invalid role option.",
        "role_not_found": "Role not found on the server. Check role IDs in code.",
        "role_no_manage": "Bot does not have **Manage Roles** permission.",
        "role_hierarchy": "Bot cannot assign **{role}**. Move bot role above status roles.",
        "role_forbidden": "Bot does not have permission to add or remove this role.",
        "role_http_error": "Discord API error: `{error}`",
        "role_set_ok": "{emoji} Set: **{label}**",
        "role_panel_title": "🛠️ Status panel • Cosmic Clock",
        "role_panel_desc": "Choose your **status, mood and activity** from the menus.

• you can have only **one** role per group
• choosing a new option removes the old one from the same group
• you can also use **/ustaw_status_swoj**",
        "role_panel_footer": "Cosmic Clock 24 • Role panel",
        "role_stats_title": "📊 Status role statistics",
        "role_stats_desc": "Below you can see how many people have each panel role.",
    },
}

# ================================
# BAZA DANYCH
# ================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
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
        """
    )
    conn.commit()

    c.execute("PRAGMA table_info(guild_config)")
    columns = [row[1] for row in c.fetchall()]
    if "country" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN country TEXT")
    if "timezone" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN timezone TEXT")
    if "language" not in columns:
        c.execute("ALTER TABLE guild_config ADD COLUMN language TEXT")

    conn.commit()
    conn.close()


def get_guild_config(guild_id: int) -> dict | None:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT guild_id, weather_category_id, clock_category_id, stats_category_id,
               channels_json, city_name, latitude, longitude, country, timezone, language
        FROM guild_config
        WHERE guild_id=?
        """,
        (guild_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None

    try:
        channels = json.loads(row[4]) if row[4] else {}
    except Exception:
        channels = {}

    return {
        "guild_id": row[0],
        "weather_category_id": row[1],
        "clock_category_id": row[2],
        "stats_category_id": row[3],
        "channels": channels,
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
    c.execute(
        """
        INSERT OR REPLACE INTO guild_config (
            guild_id, weather_category_id, clock_category_id, stats_category_id,
            channels_json, city_name, latitude, longitude, country, timezone, language
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            cfg.get("weather_category_id"),
            cfg.get("clock_category_id"),
            cfg.get("stats_category_id"),
            json.dumps(cfg.get("channels", {}), ensure_ascii=False),
            cfg.get("city_name", DEFAULT_CITY_NAME),
            cfg.get("latitude", DEFAULT_LATITUDE),
            cfg.get("longitude", DEFAULT_LONGITUDE),
            cfg.get("country", DEFAULT_COUNTRY),
            cfg.get("timezone", DEFAULT_TIMEZONE),
            cfg.get("language", DEFAULT_LANGUAGE),
        ),
    )
    conn.commit()
    conn.close()

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
        "language": DEFAULT_LANGUAGE,
    }


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


def get_timezone_object(timezone_name: str):
    try:
        return pytz.timezone(timezone_name)
    except Exception:
        return pytz.timezone(DEFAULT_TIMEZONE)


def trim_channel_name(text: str) -> str:
    text = " ".join(str(text).split())
    return text[:MAX_CHANNEL_NAME_LENGTH].strip()


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


def get_channel_from_config(guild: discord.Guild, cfg: dict, key: str):
    channel_id = cfg.get("channels", {}).get(key)
    if not channel_id:
        return None
    ch = guild.get_channel(channel_id)
    return ch if isinstance(ch, discord.VoiceChannel) else None


def find_voice_channel_in_category_by_name(category: discord.CategoryChannel | None, name: str) -> discord.VoiceChannel | None:
    if category is None:
        return None
    for channel in category.voice_channels:
        if channel.name == name:
            return channel
    return None


def format_uptime(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m {seconds}s"
    return f"{hours}h {minutes}m {seconds}s"


def get_channel_lock(channel_id: int) -> asyncio.Lock:
    if channel_id not in channel_edit_locks:
        channel_edit_locks[channel_id] = asyncio.Lock()
    return channel_edit_locks[channel_id]


async def safe_edit_channel_name(channel: discord.abc.GuildChannel | None, new_name: str):
    if channel is None:
        return
    new_name = trim_channel_name(new_name)
    if channel.name == new_name:
        return
    lock = get_channel_lock(channel.id)
    async with lock:
        if channel.name == new_name:
            return
        try:
            await channel.edit(name=new_name)
            await asyncio.sleep(CHANNEL_EDIT_DELAY)
        except discord.Forbidden:
            logging.warning("Brak uprawnień do zmiany nazwy kanału %s", channel.id)
        except discord.HTTPException as e:
            logging.warning("Nie udało się zmienić nazwy kanału %s: %s", channel.id, e)


async def create_or_get_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    for category in guild.categories:
        if category.name == name:
            return category
    return await guild.create_category(name)


async def create_or_get_voice_channel(category: discord.CategoryChannel, name: str) -> discord.VoiceChannel:
    existing = find_voice_channel_in_category_by_name(category, name)
    if existing:
        return existing
    return await category.create_voice_channel(name)


def parse_hhmm_to_today(now: datetime, hhmm: str | None) -> datetime | None:
    if not hhmm:
        return None
    try:
        hour, minute = map(int, hhmm.split(":"))
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return None


def localized_alert_name(name: str, lang: str) -> str:
    if lang == "pl":
        mapping = {
            "fog": "mgła",
            "snow drift": "zawieja śnieżna",
            "ice": "gołoledź",
            "heavy rain": "ulewa",
            "heavy snow": "intensywny śnieg",
            "blizzard": "zamieć śnieżna",
            "strong wind": "silny wiatr",
            "storm": "burza",
            "hail": "grad",
            "hurricane": "orkan",
        }
        return mapping.get(name, name)
    return name

# ================================
# API / POGODA
# ================================

async def fetch_json(url: str):
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"User-Agent": "KosmicznyZegar/25"}) as response:
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
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={quote(city_query)}&count={count}&language=pl&format=json"
    )
    data = await fetch_json(url)
    results = data.get("results", []) or []
    parsed = []
    for item in results:
        parsed.append(
            {
                "name": item.get("name"),
                "country": item.get("country", "Unknown country"),
                "admin1": item.get("admin1"),
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
                "timezone": item.get("timezone") or DEFAULT_TIMEZONE,
            }
        )
    return parsed


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
        return trim_channel_name(f"🌿 {tr(lang, 'field_pollen')} {tr(lang, 'none')}")
    active.sort(key=lambda x: x[1], reverse=True)
    formatted_items = [f"{name} {pollen_level_name(value, lang)}" for name, value in active]
    return trim_channel_name(f"🌿 {tr(lang, 'field_pollen')} " + " • ".join(formatted_items))


def format_precipitation_channel(current: dict, lang: str) -> str:
    weather_code = int(current.get("weather_code", -1)) if current.get("weather_code") is not None else -1
    precipitation = float(current.get("precipitation", 0) or 0)
    rain = float(current.get("rain", 0) or 0)
    showers = float(current.get("showers", 0) or 0)
    snowfall = float(current.get("snowfall", 0) or 0)
    rain_total = rain + showers
    rain_codes = {51, 53, 55, 61, 63, 65, 80, 81, 82}
    snow_codes = {71, 73, 75, 77, 85, 86}
    hail_codes = {96, 99}
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
    elif has_snow and has_rain:
        text = f"🌧🌨 {text}"
    elif has_hail:
        text = f"⛈ {text}"
    elif has_snow:
        text = f"🌨 {text}"
    else:
        text = f"🌧 {text}"
    return trim_channel_name(text)


def build_weather_alerts(current: dict) -> dict:
    alerts: list[str] = []
    level = 0
    weather_code = int(current.get("weather_code", -1)) if current.get("weather_code") is not None else -1
    temperature = float(current.get("temperature_2m", 999) or 999)
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
    return {"alerts": unique_alerts, "level": level}


def format_alerts_channel(alerts: list[str], level: int, lang: str) -> str:
    if not alerts or level == 0:
        return tr(lang, "alert_none")
    translated_alerts = [f"❗{localized_alert_name(alert, lang)}" for alert in alerts]
    if level == 1:
        base = tr(lang, "alert_l1")
    elif level == 2:
        base = tr(lang, "alert_l2")
    else:
        base = tr(lang, "alert_l3")
    return trim_channel_name(base + " ".join(translated_alerts))


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
    dusk_start = sunset - timedelta(minutes=40)
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
    diff = now - datetime(2001, 1, 1, tzinfo=now.tzinfo)
    days = diff.total_seconds() / 86400
    lunations = 0.20439731 + (days * 0.03386319269)
    phase_index = int((lunations % 1) * 8 + 0.5) & 7
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


async def get_weather_data(city_name: str, latitude: float, longitude: float, timezone_name: str = DEFAULT_TIMEZONE, lang: str = DEFAULT_LANGUAGE):
    encoded_timezone = quote(timezone_name)
    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,apparent_temperature,cloud_cover,precipitation,rain,showers,snowfall,weather_code,wind_speed_10m,wind_gusts_10m,surface_pressure,visibility"
        "&daily=sunrise,sunset"
        f"&timezone={encoded_timezone}"
    )
    air_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={latitude}&longitude={longitude}"
        "&current=european_aqi"
        f"&timezone={encoded_timezone}"
    )
    pollen_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={latitude}&longitude={longitude}"
        "&hourly=alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,ragweed_pollen"
        f"&timezone={encoded_timezone}"
    )
    weather_data, air_data, pollen_data = await asyncio.gather(fetch_json(weather_url), fetch_json(air_url), fetch_json(pollen_url))
    current = weather_data.get("current", {}) or {}
    daily = weather_data.get("daily", {}) or {}
    air_current = air_data.get("current", {}) or {}
    hourly = pollen_data.get("hourly", {}) or {}
    hourly_time = hourly.get("time", []) or []
    current_time = current.get("time")
    pollen_index = 0
    if current_time and current_time in hourly_time:
        pollen_index = hourly_time.index(current_time)

    def pollen_value(name: str):
        values = hourly.get(name, []) or []
        if 0 <= pollen_index < len(values):
            return values[pollen_index]
        return 0

    alder = pollen_value("alder_pollen")
    birch = pollen_value("birch_pollen")
    grass = pollen_value("grass_pollen")
    mugwort = pollen_value("mugwort_pollen")
    ragweed = pollen_value("ragweed_pollen")
    alerts_info = build_weather_alerts(current)
    alerts = alerts_info["alerts"]
    alert_level = alerts_info["level"]
    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    clouds = current.get("cloud_cover")
    wind = current.get("wind_speed_10m")
    pressure = current.get("surface_pressure")
    sunrise_raw = (daily.get("sunrise") or [None])[0]
    sunset_raw = (daily.get("sunset") or [None])[0]
    sunrise_time = sunrise_raw[11:16] if sunrise_raw else "--:--"
    sunset_time = sunset_raw[11:16] if sunset_raw else "--:--"
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
        "alerts_list": [localized_alert_name(a, lang) for a in alerts],
        "alert_level": alert_level,
        "sunrise": f"🌅 {tr(lang, 'field_sunrise')} {sunrise_time}",
        "sunset": f"🌇 {tr(lang, 'field_sunset')} {sunset_time}",
        "sunrise_time": sunrise_time,
        "sunset_time": sunset_time,
        "day_length": day_length_text(sunrise_time, sunset_time, lang),
    }

# ================================
# PANEL KANAŁÓW
# ================================

async def setup_categories_and_channels(guild: discord.Guild):
    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
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
    category_map = {"weather": weather_category, "clock": clock_category, "stats": stats_category}
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
            current_channel = await create_or_get_voice_channel(target_category, fallback_name)
        channels[key] = current_channel.id
    cfg["channels"] = channels
    save_guild_config(guild.id, cfg)
    return cfg


async def update_weather_channels(guild: discord.Guild, cfg: dict, weather: dict):
    for key in ["temperature", "feels", "clouds", "air", "pollen", "rain", "wind", "pressure", "alerts"]:
        await safe_edit_channel_name(get_channel_from_config(guild, cfg, key), weather.get(key, get_channel_fallback_name(get_lang_code(cfg), key)))


async def update_clock_channels(guild: discord.Guild, cfg: dict, weather: dict):
    lang = get_lang_code(cfg)
    timezone_obj = get_timezone_object(cfg.get("timezone", DEFAULT_TIMEZONE))
    now = datetime.now(timezone_obj)
    weekdays = LANGUAGES[lang]["weekday_short"]
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "date"), f"{tr(lang, 'ch_date')} {weekdays[now.weekday()]} {now.strftime('%d.%m.%Y')}")
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "part_of_day"), format_part_of_day(now, lang, weather.get("sunrise_time"), weather.get("sunset_time")))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "sunrise"), weather.get("sunrise", f"{tr(lang, 'ch_sunrise')} --:--"))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "sunset"), weather.get("sunset", f"{tr(lang, 'ch_sunset')} --:--"))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "day_length"), weather.get("day_length", f"{tr(lang, 'day_length_prefix')} --"))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "moon"), moon_phase_name(now, lang))


async def update_stats_channels(guild: discord.Guild, cfg: dict):
    lang = get_lang_code(cfg)
    members = list(guild.members)
    human_members = [m for m in members if not m.bot]
    bot_members = [m for m in members if m.bot]
    members_count = len(members)
    humans_count = len(human_members)
    bots_count = len(bot_members)
    online_count = sum(1 for m in members if m.status != discord.Status.offline)
    vc_count = sum(1 for m in members if m.voice and m.voice.channel)
    timezone_obj = get_timezone_object(cfg.get("timezone", DEFAULT_TIMEZONE))
    today = datetime.now(timezone_obj).date()
    joined_today_count = sum(1 for m in human_members if m.joined_at and m.joined_at.astimezone(timezone_obj).date() == today)
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "members"), tr(lang, "stats_members", count=members_count))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "humans"), tr(lang, "stats_humans", count=humans_count))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "online"), tr(lang, "stats_online", count=online_count))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "bots"), tr(lang, "stats_bots", count=bots_count))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "vc"), tr(lang, "stats_vc", count=vc_count))
    await safe_edit_channel_name(get_channel_from_config(guild, cfg, "joined_today"), tr(lang, "stats_joined_today", count=joined_today_count))


async def refresh_existing_panel(guild: discord.Guild) -> bool:
    cfg = get_guild_config(guild.id)
    if not cfg:
        return False
    lang = get_lang_code(cfg)
    weather = await get_weather_data(cfg["city_name"], cfg["latitude"], cfg["longitude"], cfg.get("timezone", DEFAULT_TIMEZONE), lang)
    await update_weather_channels(guild, cfg, weather)
    await update_clock_channels(guild, cfg, weather)
    await update_stats_channels(guild, cfg)
    return True

# ================================
# SYSTEM STATUSÓW / PANEL RÓL
# ================================

def get_panel_role(guild: discord.Guild, role_id: int) -> discord.Role | None:
    return guild.get_role(role_id)


def get_role_lang(guild_id: int | None) -> str:
    if guild_id is None:
        return DEFAULT_LANGUAGE
    return get_lang_code(get_guild_config(guild_id))


async def set_single_role_in_group(member: discord.Member, group_name: str, role_key: str) -> tuple[bool, str]:
    guild = member.guild
    lang = get_role_lang(guild.id)
    mapping = ROLE_GROUPS[group_name]
    if role_key not in mapping:
        return False, tr(lang, "role_bad_option")
    selected_role = get_panel_role(guild, mapping[role_key])
    if selected_role is None:
        return False, tr(lang, "role_not_found")
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return False, tr(lang, "role_no_manage")
    if selected_role >= me.top_role:
        return False, tr(lang, "role_hierarchy", role=selected_role.name)
    roles_to_remove = []
    for other_key, other_role_id in mapping.items():
        other_role = get_panel_role(guild, other_role_id)
        if other_role and other_role in member.roles and other_key != role_key:
            roles_to_remove.append(other_role)
    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason=f"Zmiana roli z grupy {group_name}")
        if selected_role not in member.roles:
            await member.add_roles(selected_role, reason=f"Ustawienie roli z grupy {group_name}")
        label = ROLE_DISPLAY_NAMES.get(role_key, selected_role.name)
        emoji = ROLE_EMOJIS.get(role_key, "✅")
        return True, tr(lang, "role_set_ok", emoji=emoji, label=label)
    except discord.Forbidden:
        return False, tr(lang, "role_forbidden")
    except discord.HTTPException as e:
        return False, tr(lang, "role_http_error", error=e)


class GroupSelect(discord.ui.Select):
    def __init__(self, group_name: str, placeholder: str):
        self.group_name = group_name
        mapping = ROLE_GROUPS[group_name]
        options = []
        for role_key in mapping:
            label = ROLE_DISPLAY_NAMES.get(role_key, role_key)
            emoji = ROLE_EMOJIS.get(role_key)
            options.append(discord.SelectOption(label=label[:100], value=role_key, emoji=emoji))
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, custom_id=f"status_panel_select_{group_name}")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(tr(DEFAULT_LANGUAGE, "role_panel_server_only"), ephemeral=True)
            return
        selected_key = self.values[0]
        _ok, msg = await set_single_role_in_group(interaction.user, self.group_name, selected_key)
        await interaction.followup.send(msg, ephemeral=True)


class StatusPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GroupSelect("status", "Wybierz swój status..."))
        self.add_item(GroupSelect("mood", "Wybierz swój nastrój..."))
        self.add_item(GroupSelect("activity", "Wybierz swoją aktywność..."))


def build_panel_embed(guild: discord.Guild) -> discord.Embed:
    lang = get_role_lang(guild.id)

    def role_count(role_id: int) -> int:
        role = guild.get_role(role_id)
        return len(role.members) if role else 0

    status_preview = ", ".join(ROLE_DISPLAY_NAMES[k] for k in list(STATUS_ROLES.keys())[:5])
    mood_preview = ", ".join(ROLE_DISPLAY_NAMES[k] for k in list(MOOD_ROLES.keys())[:5])
    activity_preview = ", ".join(ROLE_DISPLAY_NAMES[k] for k in list(ACTIVITY_ROLES.keys())[:6])
    total_status = sum(role_count(rid) for rid in STATUS_ROLES.values())
    total_mood = sum(role_count(rid) for rid in MOOD_ROLES.values())
    total_activity = sum(role_count(rid) for rid in ACTIVITY_ROLES.values())
    embed = discord.Embed(title=tr(lang, "role_panel_title"), description=tr(lang, "role_panel_desc"), color=discord.Color.blurple())
    embed.add_field(name=f"🟢 Status • aktywnych ról: {total_status}", value=status_preview, inline=False)
    embed.add_field(name=f"😎 Nastrój • aktywnych ról: {total_mood}", value=mood_preview, inline=False)
    embed.add_field(name=f"🎮 Aktywność • aktywnych ról: {total_activity}", value=activity_preview, inline=False)
    embed.set_footer(text=tr(lang, "role_panel_footer"))
    return embed


def build_role_stats_embed(guild: discord.Guild) -> discord.Embed:
    lang = get_role_lang(guild.id)
    embed = discord.Embed(title=tr(lang, "role_stats_title"), description=tr(lang, "role_stats_desc"), color=discord.Color.green())
    for group_name, mapping in ROLE_GROUPS.items():
        lines = []
        total = 0
        for role_key, role_id in mapping.items():
            role = guild.get_role(role_id)
            count = len(role.members) if role else 0
            total += count
            emoji = ROLE_EMOJIS.get(role_key, "•")
            label = ROLE_DISPLAY_NAMES.get(role_key, role_key)
            line = f"{emoji} **{label}** — `{count}`"
            if role is None:
                line += " ⚠️"
            lines.append(line)
        embed.add_field(name=f"{GROUP_LABELS[group_name]} • {total}", value="
".join(lines) if lines else "Brak danych", inline=False)
    return embed

# ================================
# KOMENDY / AUTOCOMPLETE
# ================================

async def city_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    static_choices = [
        app_commands.Choice(name="Warszawa, Polska", value="Warszawa"),
        app_commands.Choice(name="Rzeszów, Polska", value="Rzeszów"),
        app_commands.Choice(name="Kraków, Polska", value="Kraków"),
        app_commands.Choice(name="Wrocław, Polska", value="Wrocław"),
        app_commands.Choice(name="Poznań, Polska", value="Poznań"),
        app_commands.Choice(name="Gdańsk, Polska", value="Gdańsk"),
        app_commands.Choice(name="London, United Kingdom", value="London"),
        app_commands.Choice(name="New York, USA", value="New York"),
    ]
    if not current.strip():
        return static_choices[:25]
    lowered = current.lower()
    filtered = [c for c in static_choices if lowered in c.name.lower() or lowered in c.value.lower()]
    try:
        results = await geocode_city(current, count=10)
        dynamic = []
        for item in results[:25]:
            label = item["name"] or "Unknown city"
            if item.get("admin1"):
                label += f", {item['admin1']}"
            if item.get("country"):
                label += f", {item['country']}"
            dynamic.append(app_commands.Choice(name=label[:100], value=item["name"] or current))
        combined = filtered[:]
        existing_values = {c.value for c in combined}
        for choice in dynamic:
            if choice.value not in existing_values:
                combined.append(choice)
                existing_values.add(choice.value)
        return combined[:25]
    except Exception:
        return filtered[:25] or static_choices[:25]


@bot.tree.command(name="help", description="Pokazuje pomoc bota")
async def help_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    lang = get_lang_code(cfg)
    embed = discord.Embed(title=tr(lang, "help_title"), description=tr(lang, "help_desc"), color=discord.Color.green())
    embed.add_field(name=tr(lang, "help_general"), value=tr(lang, "help_general_value"), inline=False)
    embed.add_field(name=tr(lang, "help_admin"), value=tr(lang, "help_admin_value"), inline=False)
    embed.add_field(name=tr(lang, "help_delete"), value=tr(lang, "help_delete_value"), inline=False)
    embed.add_field(name=tr(lang, "help_start"), value=tr(lang, "help_start_value"), inline=False)
    embed.set_footer(text=tr(lang, "help_footer"))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setup", description="Tworzy kategorie i kanały bota")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return
    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    lang = get_lang_code(cfg)
    await interaction.response.defer(ephemeral=True)
    try:
        await setup_categories_and_channels(guild)
        await refresh_existing_panel(guild)
        await interaction.followup.send(tr(lang, "setup_ok"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(tr(lang, "setup_error", error=e), ephemeral=True)


@bot.tree.command(name="refresh", description="Odświeża wszystkie kanały bota")
@app_commands.checks.has_permissions(manage_guild=True)
async def refresh_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return
    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    lang = get_lang_code(cfg)
    await interaction.response.defer(ephemeral=True)
    try:
        refreshed = await refresh_existing_panel(guild)
        if not refreshed:
            await interaction.followup.send(tr(lang, "refresh_no_config"), ephemeral=True)
            return
        await interaction.followup.send(tr(lang, "refresh_ok"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(tr(lang, "refresh_error", error=e), ephemeral=True)


@bot.tree.command(name="status", description="Pokazuje status konfiguracji bota")
async def status_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return
    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "no_config"), ephemeral=True)
        return
    lang = get_lang_code(cfg)
    embed = discord.Embed(title=tr(lang, "status_title"), color=discord.Color.blue())
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


@bot.tree.command(name="info", description="Pokazuje informacje o bocie")
async def info_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    lang = get_lang_code(cfg)
    uptime = datetime.now(UTC) - bot_start_time
    uptime_str = format_uptime(uptime)
    guild_count = len(bot.guilds)
    user_count = sum(g.member_count or 0 for g in bot.guilds)
    embed = discord.Embed(title=tr(lang, "info_title"), description=tr(lang, "info_desc"), color=discord.Color.blurple())
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.add_field(name=tr(lang, "info_features"), value=tr(lang, "info_features_value"), inline=False)
    embed.add_field(name=tr(lang, "info_status"), value=tr(lang, "info_status_value", uptime=uptime_str, guilds=guild_count, users=user_count), inline=False)
    embed.add_field(name=tr(lang, "info_modules"), value=tr(lang, "info_modules_value"), inline=False)
    embed.add_field(name=tr(lang, "info_author"), value=f"**{tr(lang, 'creator')}**", inline=True)
    embed.add_field(name=tr(lang, "info_version"), value=f"**{tr(lang, 'bot_version')}**", inline=True)
    embed.add_field(name=tr(lang, "info_stability"), value=tr(lang, "info_stability_value"), inline=False)
    embed.set_footer(text=tr(lang, "info_footer"))
    await interaction.response.send_message(embed=embed, ephemeral=False)


@bot.tree.command(name="pogoda", description="Pokazuje aktualną pogodę")
async def weather_command(interaction: discord.Interaction):
    try:
        guild = interaction.guild
        cfg = get_guild_config(guild.id) if guild else None
        lang = get_lang_code(cfg)
        city_name = cfg["city_name"] if cfg else DEFAULT_CITY_NAME
        latitude = cfg["latitude"] if cfg else DEFAULT_LATITUDE
        longitude = cfg["longitude"] if cfg else DEFAULT_LONGITUDE
        country = cfg["country"] if cfg else DEFAULT_COUNTRY
        timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
        weather = await get_weather_data(city_name, latitude, longitude, timezone_name, lang)
        embed = discord.Embed(title=tr(lang, "weather_title", city=city_name, country=country), color=discord.Color.teal())
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
        cfg = get_guild_config(interaction.guild.id) if interaction.guild else None
        lang = get_lang_code(cfg)
        await interaction.response.send_message(tr(lang, "weather_error", error=e), ephemeral=True)


@bot.tree.command(name="czas", description="Pokazuje aktualny czas")
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
        weather = await get_weather_data(city_name, cfg["latitude"] if cfg else DEFAULT_LATITUDE, cfg["longitude"] if cfg else DEFAULT_LONGITUDE, timezone_name, lang)
        sunrise_time = weather.get("sunrise_time")
        sunset_time = weather.get("sunset_time")
    except Exception:
        pass
    embed = discord.Embed(title=tr(lang, "time_title"), color=discord.Color.orange())
    embed.add_field(name=tr(lang, "time_city"), value=city_name, inline=False)
    embed.add_field(name=tr(lang, "time_clock"), value=now.strftime("%H:%M:%S"), inline=False)
    embed.add_field(name=tr(lang, "time_date"), value=now.strftime("%d.%m.%Y"), inline=False)
    embed.add_field(name=tr(lang, "time_part_of_day"), value=format_part_of_day(now, lang, sunrise_time, sunset_time), inline=False)
    embed.add_field(name=tr(lang, "time_timezone"), value=timezone_name, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ksiezyc", description="Pokazuje aktualną fazę księżyca")
async def moon_command(interaction: discord.Interaction):
    guild = interaction.guild
    cfg = get_guild_config(guild.id) if guild else None
    lang = get_lang_code(cfg)
    timezone_name = cfg["timezone"] if cfg else DEFAULT_TIMEZONE
    timezone_obj = get_timezone_object(timezone_name)
    now = datetime.now(timezone_obj)
    await interaction.response.send_message(moon_phase_name(now, lang), ephemeral=True)


@bot.tree.command(name="miasto", description="Ustawia miasto dla pogody i zegara na tym serwerze")
@app_commands.describe(nazwa="Miasto, np. Warszawa, Rzeszów, Kraków, London")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.autocomplete(nazwa=city_autocomplete)
async def city_command(interaction: discord.Interaction, nazwa: str):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return
    cfg = get_guild_config(guild.id)
    if not cfg:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "city_setup_first"), ephemeral=True)
        return
    lang = get_lang_code(cfg)
    await interaction.response.defer(ephemeral=True)
    try:
        results = await geocode_city(nazwa, count=10)
        if not results:
            await interaction.followup.send(tr(lang, "city_not_found", city=nazwa), ephemeral=True)
            return
        preferred = None
        lowered = nazwa.strip().lower()
        for item in results:
            item_name = (item.get("name") or "").lower()
            item_country = (item.get("country") or "").lower()
            if item_name == lowered and item_country in {"polska", "poland"}:
                preferred = item
                break
        city = preferred or results[0]
        cfg["city_name"] = city["name"] or nazwa
        cfg["latitude"] = city["latitude"]
        cfg["longitude"] = city["longitude"]
        cfg["country"] = city.get("country") or DEFAULT_COUNTRY
        cfg["timezone"] = city.get("timezone") or DEFAULT_TIMEZONE
        save_guild_config(guild.id, cfg)
        refreshed = await refresh_existing_panel(guild)
        if not refreshed:
            await interaction.followup.send(tr(lang, "refresh_no_config"), ephemeral=True)
            return
        extra = f", {city['admin1']}" if city.get("admin1") else ""
        await interaction.followup.send(tr(lang, "city_updated", city=f"{city['name']}{extra}, {city['country']}"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(tr(lang, "city_error", error=e), ephemeral=True)


@bot.tree.command(name="language", description="Zmienia język bota na tym serwerze")
@app_commands.describe(code="Kod języka: pl lub en")
@app_commands.checks.has_permissions(manage_guild=True)
async def language_command(interaction: discord.Interaction, code: str):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return
    cfg = get_guild_config(guild.id) or build_default_guild_config(guild.id)
    code = code.lower().strip()
    if code not in LANGUAGES:
        await interaction.response.send_message(tr(get_lang_code(cfg), "language_invalid"), ephemeral=True)
        return
    cfg["language"] = code
    save_guild_config(guild.id, cfg)
    await interaction.response.defer(ephemeral=True)
    try:
        if cfg.get("channels"):
            await refresh_existing_panel(guild)
    except Exception as e:
        logging.error("Błąd odświeżania po zmianie języka: %s", e)
    await interaction.followup.send(tr(code, "language_set"), ephemeral=True)


@bot.tree.command(name="panel_statusow", description="Tworzy panel statusów, nastroju i aktywności")
@app_commands.checks.has_permissions(manage_guild=True)
async def panel_statusow(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "role_panel_server_only"), ephemeral=True)
        return
    embed = build_panel_embed(interaction.guild)
    await interaction.response.send_message(embed=embed, view=StatusPanelView())


@bot.tree.command(name="pokaz_statusy", description="Pokazuje ile osób ma każdą rolę z panelu")
async def pokaz_statusy(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "role_panel_server_only"), ephemeral=True)
        return
    embed = build_role_stats_embed(interaction.guild)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ustaw_status_swoj", description="Ustawia ręcznie swój status, nastrój albo aktywność")
@app_commands.describe(grupa="Wybierz grupę roli", opcja="Wybierz konkretną opcję z tej grupy")
@app_commands.choices(
    grupa=[
        app_commands.Choice(name="Status", value="status"),
        app_commands.Choice(name="Nastrój", value="mood"),
        app_commands.Choice(name="Aktywność", value="activity"),
    ]
)
async def ustaw_status_swoj(interaction: discord.Interaction, grupa: app_commands.Choice[str], opcja: str):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(tr(DEFAULT_LANGUAGE, "role_panel_server_only"), ephemeral=True)
        return
    _ok, msg = await set_single_role_in_group(interaction.user, grupa.value, opcja)
    await interaction.response.send_message(msg, ephemeral=True)


@ustaw_status_swoj.autocomplete("opcja")
async def ustaw_status_swoj_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        namespace = interaction.namespace
        grupa = getattr(namespace, "grupa", None)
        if grupa is None:
            return []
        group_value = grupa.value if isinstance(grupa, app_commands.Choice) else str(grupa)
        if group_value not in ROLE_GROUPS:
            return []
        choices = []
        for role_key in ROLE_GROUPS[group_value].keys():
            label = ROLE_DISPLAY_NAMES.get(role_key, role_key)
            if current.lower() in label.lower() or current.lower() in role_key.lower():
                choices.append(app_commands.Choice(name=label, value=role_key))
        return choices[:25]
    except Exception:
        return []

# ================================
# USUWANIE KATEGORII
# ================================

async def delete_category_with_channels(guild: discord.Guild, category_id: int | None):
    if not category_id:
        return
    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return
    for channel in list(category.channels):
        try:
            await channel.delete()
        except Exception as e:
            logging.warning("Nie udało się usunąć kanału %s: %s", channel.id, e)
    try:
        await category.delete()
    except Exception as e:
        logging.warning("Nie udało się usunąć kategorii %s: %s", category.id, e)


def remove_channel_keys_by_group(cfg: dict, group_name: str) -> dict:
    channels = dict(cfg.get("channels", {}))
    keys_to_remove = [key for key, (category_key, _) in CHANNEL_TEMPLATE_KEYS.items() if category_key == group_name]
    for key in keys_to_remove:
        channels.pop(key, None)
    cfg["channels"] = channels
    return cfg


@bot.tree.command(name="usun_pogoda", description="Usuwa kategorię Pogoda razem z kanałami")
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


@bot.tree.command(name="usun_kosmiczny_zegar", description="Usuwa kategorię Kosmiczny Zegar razem z kanałami")
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


@bot.tree.command(name="usun_statystyki", description="Usuwa kategorię Statystyki razem z kanałami")
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


@bot.tree.command(name="usun_wszystko", description="Usuwa wszystkie kategorie bota")
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
# EVENTY / LIVE
# ================================

def schedule_stats_refresh(guild: discord.Guild):
    if guild.id in stats_update_tasks and not stats_update_tasks[guild.id].done():
        return

    async def delayed_refresh():
        try:
            await asyncio.sleep(STATS_REFRESH_DEBOUNCE_SECONDS)
            cfg = get_guild_config(guild.id)
            if cfg:
                await update_stats_channels(guild, cfg)
        except Exception as e:
            logging.warning("Błąd odświeżania statystyk live dla %s: %s", guild.id, e)
        finally:
            stats_update_tasks.pop(guild.id, None)

    stats_update_tasks[guild.id] = asyncio.create_task(delayed_refresh())


@bot.event
async def on_member_join(member: discord.Member):
    schedule_stats_refresh(member.guild)


@bot.event
async def on_member_remove(member: discord.Member):
    schedule_stats_refresh(member.guild)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if before.channel != after.channel:
        schedule_stats_refresh(member.guild)

# ================================
# TASKI TŁA
# ================================

@tasks.loop(minutes=WEATHER_REFRESH_MINUTES)
async def auto_refresh():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        try:
            cfg = get_guild_config(guild.id)
            if cfg:
                await refresh_existing_panel(guild)
        except Exception as e:
            logging.warning("Błąd auto_refresh dla serwera %s: %s", guild.id, e)


@tasks.loop(seconds=1)
async def update_status_clock():
    await bot.wait_until_ready()
    now = datetime.now(get_timezone_object(DEFAULT_TIMEZONE))
    # Renderowanie / wyrównanie statusu ustala Discord, więc można zmienić tylko tekst.
    status_text = f"🕒 {now.strftime('%H:%M:%S')} • {STATUS_CITY_NAME}"
    try:
        await bot.change_presence(activity=discord.CustomActivity(name=status_text))
    except Exception as e:
        logging.warning("Nie udało się zaktualizować statusu bota: %s", e)

# ================================
# START BOTA
# ================================

@bot.event
async def on_ready():
    logging.info("Zalogowano jako %s (ID: %s)", bot.user, bot.user.id if bot.user else "?")
    try:
        bot.add_view(StatusPanelView())
    except Exception as e:
        logging.warning("Nie udało się zarejestrować StatusPanelView: %s", e)

    try:
        synced = await bot.tree.sync()
        logging.info("Zsynchronizowano %s komend", len(synced))
        for cmd in synced:
            logging.info("Komenda aktywna: /%s", cmd.name)
    except Exception as e:
        logging.error("Błąd synchronizacji komend: %s", e)

    if not auto_refresh.is_running():
        auto_refresh.start()
    if not update_status_clock.is_running():
        update_status_clock.start()


init_db()

if not TOKEN:
    raise RuntimeError("Brak DISCORD_TOKEN w zmiennych środowiskowych.")

bot.run(TOKEN)
