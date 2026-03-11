import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN_PUBLIC")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


@bot.event
async def on_ready():
    print(f"Publiczny bot zalogowany jako {bot.user}")
    try:
        synced = await tree.sync()
        print(f"Zsynchronizowano {len(synced)} komend.")
    except Exception as e:
        print(e)


@tree.command(name="ping", description="Sprawdza czy bot działa")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Publiczny bot działa!")


bot.run(TOKEN)
