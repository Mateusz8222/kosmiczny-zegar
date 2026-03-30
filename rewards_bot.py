import json
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

DB_FILE = "rewards_data.db"
SHOP_FILE = "shop.json"
CRATES_FILE = "crates.json"

POINTS_PER_MESSAGE = 25
MESSAGE_COOLDOWN_SECONDS = 30
DAILY_REWARD = 500
EMBED_COLOR = discord.Color.blurple()

last_message_points: dict[tuple[int, int], float] = {}


DEFAULT_SHOP = {
    "vip_7dni": {
        "name": "VIP 7 dni",
        "description": "Nagroda ręczna od administracji",
        "price": 5000,
        "type": "manual"
    },
    "specjalna_rola": {
        "name": "Specjalna rola",
        "description": "Rola przyznawana automatycznie przez bota",
        "price": 3000,
        "type": "role",
        "role_id": 0
    },
    "mega_pakiet": {
        "name": "Mega Pakiet Punktów",
        "description": "Dostajesz dodatkowe punkty",
        "price": 4000,
        "type": "points",
        "points": 2500
    }
}

DEFAULT_CRATES = {
    "common": {
        "name": "Common Skrzynka",
        "price": 1000,
        "rewards": [
            {"type": "points", "label": "250 punktów", "value": 250, "chance": 40},
            {"type": "points", "label": "500 punktów", "value": 500, "chance": 30},
            {"type": "points", "label": "1000 punktów", "value": 1000, "chance": 20},
            {"type": "nothing", "label": "Pusta skrzynka", "chance": 10}
        ]
    },
    "rare": {
        "name": "Rare Skrzynka",
        "price": 3000,
        "rewards": [
            {"type": "points", "label": "1000 punktów", "value": 1000, "chance": 35},
            {"type": "points", "label": "2500 punktów", "value": 2500, "chance": 30},
            {"type": "points", "label": "5000 punktów", "value": 5000, "chance": 20},
            {"type": "manual", "label": "Nagroda specjalna", "chance": 10},
            {"type": "nothing", "label": "Pusta skrzynka", "chance": 5}
        ]
    }
}


def ensure_json_files() -> None:
    if not Path(SHOP_FILE).exists():
        with open(SHOP_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SHOP, f, ensure_ascii=False, indent=2)

    if not Path(CRATES_FILE).exists():
        with open(CRATES_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CRATES, f, ensure_ascii=False, indent=2)


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_shop() -> dict[str, Any]:
    return load_json(SHOP_FILE)


def load_crates() -> dict[str, Any]:
    return load_json(CRATES_FILE)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            daily_last_claim INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            item_name TEXT NOT NULL,
            price INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS crate_opens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            crate_key TEXT NOT NULL,
            reward_label TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def ensure_user(guild_id: int, user_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO users (guild_id, user_id, points, daily_last_claim)
        VALUES (?, ?, 0, 0)
    """, (guild_id, user_id))
    conn.commit()
    conn.close()


def get_points(guild_id: int, user_id: int) -> int:
    ensure_user(guild_id, user_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cur.fetchone()
    conn.close()
    return int(row["points"]) if row else 0


def add_points(guild_id: int, user_id: int, amount: int) -> int:
    ensure_user(guild_id, user_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET points = points + ?
        WHERE guild_id=? AND user_id=?
    """, (amount, guild_id, user_id))
    conn.commit()
    conn.close()
    return get_points(guild_id, user_id)


def remove_points(guild_id: int, user_id: int, amount: int) -> bool:
    current = get_points(guild_id, user_id)
    if current < amount:
        return False

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET points = points - ?
        WHERE guild_id=? AND user_id=?
    """, (amount, guild_id, user_id))
    conn.commit()
    conn.close()
    return True


def get_daily_last_claim(guild_id: int, user_id: int) -> int:
    ensure_user(guild_id, user_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT daily_last_claim
        FROM users
        WHERE guild_id=? AND user_id=?
    """, (guild_id, user_id))
    row = cur.fetchone()
    conn.close()
    return int(row["daily_last_claim"]) if row else 0


def set_daily_last_claim(guild_id: int, user_id: int, ts: int) -> None:
    ensure_user(guild_id, user_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET daily_last_claim=?
        WHERE guild_id=? AND user_id=?
    """, (ts, guild_id, user_id))
    conn.commit()
    conn.close()


def save_purchase(guild_id: int, user_id: int, item_key: str, item_name: str, price: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO purchases (guild_id, user_id, item_key, item_name, price, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (guild_id, user_id, item_key, item_name, price, int(time.time())))
    conn.commit()
    conn.close()


def save_crate_open(guild_id: int, user_id: int, crate_key: str, reward_label: str) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO crate_opens (guild_id, user_id, crate_key, reward_label, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (guild_id, user_id, crate_key, reward_label, int(time.time())))
    conn.commit()
    conn.close()


def get_top_users(guild_id: int, limit: int = 10) -> list[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, points
        FROM users
        WHERE guild_id=?
        ORDER BY points DESC
        LIMIT ?
    """, (guild_id, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


def make_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR)


def pick_crate_reward(crate_data: dict[str, Any]) -> dict[str, Any]:
    rewards = crate_data["rewards"]
    total = sum(item["chance"] for item in rewards)
    roll = random.uniform(0, total)
    current = 0.0

    for reward in rewards:
        current += reward["chance"]
        if roll <= current:
            return reward

    return rewards[-1]


async def grant_shop_reward(
    interaction: discord.Interaction,
    item_key: str,
    item: dict[str, Any],
) -> str:
    reward_type = item.get("type", "manual")

    if reward_type == "points":
        points = int(item.get("points", 0))
        new_total = add_points(interaction.guild.id, interaction.user.id, points)
        return f"✅ Kupiono **{item['name']}**.\nDostałeś **{points} pkt**.\nMasz teraz **{new_total} pkt**."

    if reward_type == "role":
        role_id = int(item.get("role_id", 0))
        role = interaction.guild.get_role(role_id)
        if role is None:
            return f"⚠️ Kupiono **{item['name']}**, ale rola nie została znaleziona. Ustaw poprawne `role_id` w `shop.json`."

        if not isinstance(interaction.user, discord.Member):
            return "❌ To działa tylko na serwerze."

        try:
            await interaction.user.add_roles(role, reason="Zakup nagrody w sklepie punktowym")
            return f"✅ Kupiono **{item['name']}** i nadano rolę **{role.name}**."
        except discord.Forbidden:
            return f"⚠️ Kupiono **{item['name']}**, ale bot nie ma uprawnień do nadania roli **{role.name}**."

    return (
        f"✅ Kupiono **{item['name']}**.\n"
        f"To nagroda ręczna — administracja powinna ją teraz przyznać."
    )


async def grant_crate_reward(
    interaction: discord.Interaction,
    crate_key: str,
    reward: dict[str, Any],
) -> str:
    reward_type = reward.get("type", "nothing")
    label = reward.get("label", "Nieznana nagroda")

    if reward_type == "points":
        value = int(reward.get("value", 0))
        new_total = add_points(interaction.guild.id, interaction.user.id, value)
        save_crate_open(interaction.guild.id, interaction.user.id, crate_key, label)
        return (
            f"🎁 Otworzyłeś skrzynkę i wygrałeś: **{label}**\n"
            f"💰 Dodano **{value} pkt**.\n"
            f"Masz teraz **{new_total} pkt**."
        )

    if reward_type == "manual":
        save_crate_open(interaction.guild.id, interaction.user.id, crate_key, label)
        return (
            f"🎁 Otworzyłeś skrzynkę i trafiłeś: **{label}**\n"
            f"⚠️ To nagroda ręczna — administracja powinna ją przyznać."
        )

    save_crate_open(interaction.guild.id, interaction.user.id, crate_key, label)
    return f"🎁 Otworzyłeś skrzynkę i trafiłeś: **{label}**"


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Zalogowano jako {bot.user}")
        print(f"✅ Zsynchronizowano {len(synced)} komend slash")
    except Exception as e:
        print(f"❌ Błąd sync: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    content = message.content.strip()
    if len(content) < 3:
        await bot.process_commands(message)
        return

    key = (message.guild.id, message.author.id)
    now = time.time()
    last = last_message_points.get(key, 0)

    if now - last >= MESSAGE_COOLDOWN_SECONDS:
        add_points(message.guild.id, message.author.id, POINTS_PER_MESSAGE)
        last_message_points[key] = now

    await bot.process_commands(message)


@bot.tree.command(name="punkty", description="Pokazuje ile masz punktów")
async def punkty(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    points = get_points(interaction.guild.id, interaction.user.id)
    embed = make_embed("💰 Twoje punkty", f"Masz obecnie **{points} pkt**.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="top", description="Pokazuje top 10 użytkowników")
async def top(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    rows = get_top_users(interaction.guild.id, 10)

    if not rows:
        await interaction.response.send_message("ℹ️ Brak danych do rankingu.", ephemeral=True)
        return

    lines = []
    for idx, row in enumerate(rows, start=1):
        member = interaction.guild.get_member(row["user_id"])
        name = member.display_name if member else f"Użytkownik {row['user_id']}"
        lines.append(f"**{idx}.** {name} — **{row['points']} pkt**")

    embed = make_embed("🏆 Top 10", "\n".join(lines))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="daily", description="Odbierz dzienną nagrodę punktową")
async def daily(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    now = int(time.time())
    last_claim = get_daily_last_claim(interaction.guild.id, interaction.user.id)

    if now - last_claim < 86400:
        remaining = 86400 - (now - last_claim)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await interaction.response.send_message(
            f"⏳ Daily już odebrane. Spróbuj ponownie za **{hours}h {minutes}m**.",
            ephemeral=True
        )
        return

    new_total = add_points(interaction.guild.id, interaction.user.id, DAILY_REWARD)
    set_daily_last_claim(interaction.guild.id, interaction.user.id, now)

    embed = make_embed(
        "🎉 Daily odebrane",
        f"Dostałeś **{DAILY_REWARD} pkt**.\nMasz teraz **{new_total} pkt**."
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="sklep", description="Pokazuje sklep z nagrodami")
async def sklep(interaction: discord.Interaction):
    shop = load_shop()

    lines = []
    for key, item in shop.items():
        lines.append(
            f"**{item['name']}**\n"
            f"ID: `{key}`\n"
            f"Cena: **{item['price']} pkt**\n"
            f"Opis: {item.get('description', 'Brak opisu')}\n"
        )

    embed = make_embed("🛒 Sklep", "\n".join(lines) if lines else "Brak nagród w sklepie.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="kup", description="Kup nagrodę ze sklepu")
@app_commands.describe(item_id="ID nagrody ze sklepu, np. vip_7dni")
async def kup(interaction: discord.Interaction, item_id: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    shop = load_shop()
    item = shop.get(item_id)

    if item is None:
        await interaction.response.send_message("❌ Nie znaleziono takiej nagrody.", ephemeral=True)
        return

    price = int(item["price"])
    current = get_points(interaction.guild.id, interaction.user.id)

    if current < price:
        await interaction.response.send_message(
            f"❌ Masz za mało punktów. Potrzebujesz **{price} pkt**, a masz **{current} pkt**.",
            ephemeral=True
        )
        return

    if not remove_points(interaction.guild.id, interaction.user.id, price):
        await interaction.response.send_message("❌ Nie udało się pobrać punktów.", ephemeral=True)
        return

    save_purchase(interaction.guild.id, interaction.user.id, item_id, item["name"], price)
    result = await grant_shop_reward(interaction, item_id, item)
    await interaction.response.send_message(result, ephemeral=True)


@bot.tree.command(name="skrzynki", description="Pokazuje dostępne skrzynki")
async def skrzynki(interaction: discord.Interaction):
    crates = load_crates()

    lines = []
    for key, crate in crates.items():
        lines.append(
            f"**{crate['name']}**\n"
            f"ID: `{key}`\n"
            f"Cena: **{crate['price']} pkt**\n"
        )

    embed = make_embed("🎁 Skrzynki", "\n".join(lines) if lines else "Brak skrzynek.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="otworz", description="Otwiera wybraną skrzynkę")
@app_commands.describe(crate_id="ID skrzynki, np. common albo rare")
async def otworz(interaction: discord.Interaction, crate_id: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    crates = load_crates()
    crate = crates.get(crate_id)

    if crate is None:
        await interaction.response.send_message("❌ Nie znaleziono takiej skrzynki.", ephemeral=True)
        return

    price = int(crate["price"])
    current = get_points(interaction.guild.id, interaction.user.id)

    if current < price:
        await interaction.response.send_message(
            f"❌ Masz za mało punktów. Potrzebujesz **{price} pkt**, a masz **{current} pkt**.",
            ephemeral=True
        )
        return

    if not remove_points(interaction.guild.id, interaction.user.id, price):
        await interaction.response.send_message("❌ Nie udało się pobrać punktów.", ephemeral=True)
        return

    reward = pick_crate_reward(crate)
    result = await grant_crate_reward(interaction, crate_id, reward)
    await interaction.response.send_message(result, ephemeral=True)


@bot.tree.command(name="dodajpunkty", description="Dodaje punkty użytkownikowi")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(uzytkownik="Komu dodać punkty", ilosc="Ile punktów dodać")
async def dodajpunkty(interaction: discord.Interaction, uzytkownik: discord.Member, ilosc: int):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Ta komenda działa tylko na serwerze.", ephemeral=True)
        return

    if ilosc <= 0:
        await interaction.response.send_message("❌ Ilość punktów musi być większa od 0.", ephemeral=True)
        return

    total = add_points(interaction.guild.id, uzytkownik.id, ilosc)
    await interaction.response.send_message(
        f"✅ Dodano **{ilosc} pkt** dla **{uzytkownik.display_name}**.\n"
        f"Ma teraz **{total} pkt**.",
        ephemeral=True
    )


@bot.tree.command(name="reload_config", description="Przeładowuje sklep i skrzynki z plików JSON")
@app_commands.checks.has_permissions(administrator=True)
async def reload_config(interaction: discord.Interaction):
    try:
        load_shop()
        load_crates()
        await interaction.response.send_message("✅ Pliki `shop.json` i `crates.json` zostały przeładowane.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd przeładowania configu: {e}", ephemeral=True)


def main():
    if not TOKEN:
        raise RuntimeError("Brak DISCORD_TOKEN w zmiennych środowiskowych.")

    ensure_json_files()
    init_db()
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
