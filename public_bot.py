import os
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("PUBLIC_DISCORD_TOKEN")

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user}")

    try:
        synced = await bot.tree.sync()
        print(f"Zsynchronizowano {len(synced)} komend slash")
    except Exception as e:
        print(e)


@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Publiczny bot działa!")


@bot.tree.command(name="ping", description="Sprawdza czy publiczny bot działa")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Publiczny bot działa!")


if not TOKEN:
    raise ValueError("Brak PUBLIC_DISCORD_TOKEN w Railway Variables")

bot.run(TOKEN)
