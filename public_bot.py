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


# komenda prefix
@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Publiczny bot działa!")


# slash command
@bot.tree.command(name="ping", description="Sprawdza czy publiczny bot działa")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Publiczny bot działa!")


@bot.event
async def setup_hook():
    await bot.tree.sync()


if not TOKEN:
    raise ValueError("Brak PUBLIC_DISCORD_TOKEN w .env")

bot.run(TOKEN)
