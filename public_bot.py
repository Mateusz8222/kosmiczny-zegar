import discord
from discord.ext import commands
from discord import app_commands
import datetime

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --------------------
# BOT READY
# --------------------

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot uruchomiony jako {bot.user}")
    print(f"Bot jest na {len(bot.guilds)} serwerach")

# --------------------
# PING
# --------------------

@bot.tree.command(name="ping", description="Sprawdza czy bot działa")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! {latency} ms")

# --------------------
# STATYSTYKI BOTA
# --------------------

@bot.tree.command(name="botinfo", description="Pokazuje statystyki bota")
async def botinfo(interaction: discord.Interaction):

    servers = len(bot.guilds)
    users = sum(g.member_count or 0 for g in bot.guilds)

    embed = discord.Embed(
        title="📊 Statystyki bota",
        color=0x5865F2
    )

    embed.add_field(name="🌍 Serwery", value=servers, inline=False)
    embed.add_field(name="👥 Użytkownicy", value=users, inline=False)
    embed.add_field(name="🤖 Bot", value=str(bot.user), inline=False)

    if bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    await interaction.response.send_message(embed=embed)

# --------------------
# LICZBA SERWERÓW
# --------------------

@bot.tree.command(name="serwery", description="Pokazuje ile serwerów ma bot")
async def serwery(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🌍 Bot jest na **{len(bot.guilds)}** serwerach."
    )

# --------------------
# INVITE
# --------------------

@bot.tree.command(name="invite", description="Link do dodania bota")
async def invite(interaction: discord.Interaction):

    link = "https://discord.com/oauth2/authorize?client_id=1481070169077055548&permissions=2147568640&scope=bot%20applications.commands"

    embed = discord.Embed(
        title="➕ Dodaj Kosmiczny Zegar",
        description=f"[Kliknij tutaj aby dodać bota]({link})",
        color=0x5865F2
    )

    await interaction.response.send_message(embed=embed)

# --------------------
# CZAS
# --------------------

@bot.tree.command(name="czas", description="Pokazuje aktualny czas")
async def czas(interaction: discord.Interaction):

    now = datetime.datetime.now().strftime("%H:%M:%S")

    await interaction.response.send_message(
        f"🕒 Aktualny czas: **{now}**"
    )

# --------------------
# DATA
# --------------------

@bot.tree.command(name="data", description="Pokazuje aktualną datę")
async def data(interaction: discord.Interaction):

    today = datetime.datetime.now().strftime("%d.%m.%Y")

    await interaction.response.send_message(
        f"📅 Dzisiejsza data: **{today}**"
    )

# --------------------
# POMOC
# --------------------

@bot.tree.command(name="pomoc", description="Lista komend bota")
async def pomoc(interaction: discord.Interaction):

    embed = discord.Embed(
        title="🚀 Kosmiczny Zegar - Komendy",
        color=0x5865F2
    )

    embed.add_field(name="/ping", value="sprawdza czy bot działa", inline=False)
    embed.add_field(name="/botinfo", value="statystyki bota", inline=False)
    embed.add_field(name="/serwery", value="ile serwerów ma bot", inline=False)
    embed.add_field(name="/invite", value="link do dodania bota", inline=False)
    embed.add_field(name="/czas", value="aktualny czas", inline=False)
    embed.add_field(name="/data", value="dzisiejsza data", inline=False)

    await interaction.response.send_message(embed=embed)

# --------------------
# START BOTA
# --------------------

bot.run("TWÓJ_TOKEN_BOTA")
