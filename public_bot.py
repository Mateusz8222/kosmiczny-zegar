
import asyncio
import logging
from typing import Callable

import public_bot as mod


def _get_pending_changes(cache: dict[int, dict[str, str]], guild_id: int, new_payload: dict[str, str]):
    old_payload = cache.get(guild_id) or {}
    return [(key, value) for key, value in new_payload.items() if old_payload.get(key) != value]


def _commit_payload(cache: dict[int, dict[str, str]], guild_id: int, payload: dict[str, str]) -> None:
    cache[guild_id] = dict(payload)


async def _apply_changed_channels(
    guild,
    cfg: dict,
    payload: dict[str, str],
    cache: dict[int, dict[str, str]],
    *,
    label: str,
    force_full: bool = False,
) -> bool:
    pending = list(payload.items()) if force_full else _get_pending_changes(cache, guild.id, payload)
    if not pending:
        return False

    if force_full:
        logging.info("[%s] Pełna synchronizacja kanałów dla serwera %s", label, guild.name)
    else:
        logging.info("[%s] Wykryto zmianę danych dla serwera %s", label, guild.name)

    for key, new_name in pending:
        channel = mod.get_channel_from_config(guild, cfg, key)
        if channel is None:
            continue
        await mod.safe_edit_channel_name(channel, new_name)

    _commit_payload(cache, guild.id, payload)
    return True


async def update_weather_channels(guild, cfg: dict, weather: dict, *, force_full: bool = False):
    payload = {
        key: weather.get(key, mod.get_channel_fallback_name(mod.get_lang_code(cfg), key))
        for key in ["temperature", "feels", "clouds", "air", "pollen", "rain", "wind", "pressure", "alerts"]
    }
    return await _apply_changed_channels(
        guild,
        cfg,
        payload,
        mod.last_weather_payloads,
        label="POGODA",
        force_full=force_full,
    )


async def update_clock_channels(guild, cfg: dict, weather: dict | None = None, *, force_full: bool = False):
    lang = mod.get_lang_code(cfg)
    timezone_obj = mod.get_timezone_object(cfg.get("timezone", mod.DEFAULT_TIMEZONE))
    now = mod.datetime.now(timezone_obj)
    weekdays = mod.LANGUAGES[lang]["weekday_short"]

    cached_weather = weather or mod.weather_cache.get(guild.id, {})
    sunrise_time = cached_weather.get("sunrise_time")
    sunset_time = cached_weather.get("sunset_time")
    payload = {
        "date": f"{mod.tr(lang, 'ch_date')} {weekdays[now.weekday()]} {now.strftime('%d.%m.%Y')}",
        "part_of_day": mod.format_part_of_day(now, lang, sunrise_time, sunset_time),
        "sunrise": cached_weather.get("sunrise", f"🌅 {mod.tr(lang, 'field_sunrise')} --:--"),
        "sunset": cached_weather.get("sunset", f"🌇 {mod.tr(lang, 'field_sunset')} --:--"),
        "day_length": cached_weather.get("day_length", f"{mod.tr(lang, 'day_length_prefix')} --"),
        "moon": mod.moon_phase_name(now, lang),
    }
    return await _apply_changed_channels(
        guild,
        cfg,
        payload,
        mod.last_clock_payloads,
        label="ZEGAR",
        force_full=force_full,
    )


async def update_stats_channels(guild, cfg: dict, *, force_full: bool = False):
    await mod.ensure_guild_members_cached(guild)

    lang = mod.get_lang_code(cfg)
    members = list(guild.members)
    human_members = [m for m in members if not m.bot]
    bot_members = [m for m in members if m.bot]

    members_count = guild.member_count or len(members)
    humans_count = len(human_members)
    bots_count = len(bot_members)

    online_count = sum(
        1 for m in members
        if m.status in {mod.discord.Status.online, mod.discord.Status.idle, mod.discord.Status.dnd}
    )

    vc_count = sum(1 for m in members if m.voice and m.voice.channel)

    timezone_obj = mod.get_timezone_object(cfg.get("timezone", mod.DEFAULT_TIMEZONE))
    today = mod.datetime.now(timezone_obj).date()

    joined_today_count = sum(
        1 for m in human_members
        if m.joined_at and m.joined_at.astimezone(timezone_obj).date() == today
    )

    try:
        bans_count = 0
        async for _ in guild.bans(limit=None):
            bans_count += 1
    except mod.discord.Forbidden:
        logging.warning("[STATYSTYKI] Brak uprawnień do odczytu banów na serwerze %s", guild.name)
        bans_count = 0
    except Exception as e:
        logging.warning("[STATYSTYKI] Nie udało się pobrać banów na serwerze %s: %s", guild.name, e)
        bans_count = 0

    payload = {
        "members": mod.tr(lang, "stats_members", count=members_count),
        "humans": mod.tr(lang, "stats_humans", count=humans_count),
        "online": mod.tr(lang, "stats_online", count=online_count),
        "bots": mod.tr(lang, "stats_bots", count=bots_count),
        "vc": mod.tr(lang, "stats_vc", count=vc_count),
        "joined_today": mod.tr(lang, "stats_joined_today", count=joined_today_count),
        "bans": mod.tr(lang, "stats_bans", count=bans_count),
    }

    pending = list(payload.items()) if force_full else _get_pending_changes(mod.last_stats_payloads, guild.id, payload)
    if not pending:
        return False

    logging.info(
        "[STATYSTYKI] %s | %s | wszyscy=%s ludzie=%s boty=%s online=%s vc=%s today=%s bany=%s",
        "Pełna synchronizacja" if force_full else "Zmiana",
        guild.name,
        members_count,
        humans_count,
        bots_count,
        online_count,
        vc_count,
        joined_today_count,
        bans_count,
    )

    for key, new_name in pending:
        await mod.safe_edit_channel_name(mod.get_channel_from_config(guild, cfg, key), new_name)

    _commit_payload(mod.last_stats_payloads, guild.id, payload)
    return True


async def refresh_existing_panel(guild, *, force_full: bool = False) -> bool:
    cfg = mod.get_guild_config(guild.id)
    if not cfg:
        return False

    lang = mod.get_lang_code(cfg)
    weather = await mod.get_weather_data(
        cfg["city_name"],
        cfg["latitude"],
        cfg["longitude"],
        cfg.get("timezone", mod.DEFAULT_TIMEZONE),
        lang,
    )

    mod.weather_cache[guild.id] = weather

    await update_weather_channels(guild, cfg, weather, force_full=force_full)
    await update_clock_channels(guild, cfg, weather, force_full=force_full)
    await update_stats_channels(guild, cfg, force_full=force_full)
    return True


async def schedule_background_refresh(guild, *, force_full: bool = False):
    existing = mod.background_refresh_tasks.get(guild.id)
    if existing and not existing.done():
        return

    async def runner():
        try:
            logging.info(
                "[REFRESH] Start %sodświeżenia dla serwera %s",
                "pełnego " if force_full else "",
                guild.name,
            )
            await mod.ensure_guild_members_cached(guild)
            await refresh_existing_panel(guild, force_full=force_full)
            await mod.refresh_status_panel_message(guild)
            logging.info(
                "[REFRESH] Koniec %sodświeżenia dla serwera %s",
                "pełnego " if force_full else "",
                guild.name,
            )
        except Exception as e:
            logging.warning("Błąd background refresh dla serwera %s: %s", guild.id, e)
        finally:
            mod.background_refresh_tasks.pop(guild.id, None)

    task = asyncio.create_task(runner())
    mod.background_refresh_tasks[guild.id] = task
    await task


async def setup_command(interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(mod.tr(mod.DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return

    cfg = mod.get_guild_config(guild.id) or mod.build_default_guild_config(guild.id)
    lang = mod.get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)
    try:
        await mod.setup_categories_and_channels(guild)

        mod.last_weather_payloads.pop(guild.id, None)
        mod.last_clock_payloads.pop(guild.id, None)
        mod.last_stats_payloads.pop(guild.id, None)
        mod.weather_cache.pop(guild.id, None)

        await schedule_background_refresh(guild, force_full=True)
        await interaction.followup.send(mod.tr(lang, "setup_ok"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(mod.tr(lang, "setup_error", error=e), ephemeral=True)


async def refresh_command(interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(mod.tr(mod.DEFAULT_LANGUAGE, "only_server"), ephemeral=True)
        return

    cfg = mod.get_guild_config(guild.id) or mod.build_default_guild_config(guild.id)
    lang = mod.get_lang_code(cfg)

    await interaction.response.defer(ephemeral=True)
    try:
        if not cfg.get("channels"):
            await interaction.followup.send(mod.tr(lang, "refresh_no_config"), ephemeral=True)
            return
        await schedule_background_refresh(guild, force_full=False)
        await interaction.followup.send(mod.tr(lang, "refresh_ok"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(mod.tr(lang, "refresh_error", error=e), ephemeral=True)


# monkey patches
mod.update_weather_channels = update_weather_channels
mod.update_clock_channels = update_clock_channels
mod.update_stats_channels = update_stats_channels
mod.refresh_existing_panel = refresh_existing_panel
mod.schedule_background_refresh = schedule_background_refresh

# replace command callbacks
for cmd in mod.bot.tree.get_commands():
    if cmd.name == "setup":
        cmd.callback = setup_command
    elif cmd.name == "refresh":
        cmd.callback = refresh_command


if __name__ == "__main__":
    mod.main()
