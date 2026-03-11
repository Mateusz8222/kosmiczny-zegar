import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN_PUBLIC", "").strip()

if not TOKEN:
    raise ValueError("Brakuje DISCORD_TOKEN_PUBLIC w zmiennych środowiskowych")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# =========================
# FUNKCJE POMOCNICZE
# =========================
def find_category(guild: discord.Guild, name: str):
    return discord.utils.get(guild.categories, name=name)


def find_voice_channel(guild: discord.Guild, category: discord.CategoryChannel, name: str):
    for channel in guild.voice_channels:
        if channel.category == category and channel.name == name:
            return channel
    return None


async def create_voice_if_missing(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str
):
    existing = find_voice_channel(guild, category, name)
    if existing is not None:
        return existing, False

    created = await guild.create_voice_channel(name=name, category=category)
    return created, True


# =========================
# READY
# =========================
@bot.event
async def on_ready():
    print(f"[PUBLIC BOT] Zalogowano jako {bot.user} (ID: {bot.user.id})")
    try:
        synced = await tree.sync()
        print(f"[PUBLIC BOT] Zsynchronizowano {len(synced)} komend slash.")
    except Exception as e:
        print(f"[PUBLIC BOT] Błąd synchronizacji komend: {e}")


# =========================
# /ping
# =========================
@tree.command(name="ping", description="Sprawdza czy publiczny bot działa")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Publiczny bot działa!", ephemeral=True)


# =========================
# /setup
# =========================
@tree.command(name="setup", description="Tworzy panel statystyk i pogody")
async def setup(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Tej komendy można użyć tylko na serwerze.",
            ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Tylko administrator może użyć tej komendy.",
            ephemeral=True
        )
        return

    guild = interaction.guild

    await interaction.response.defer(ephemeral=True)

    try:
        # =========================
        # KATEGORIE
        # =========================
        stats_category = find_category(guild, "📊 STATYSTYKI")
        weather_category = find_category(guild, "🌦 POGODA")

        created_categories = []

        if stats_category is None:
            stats_category = await guild.create_category("📊 STATYSTYKI")
            created_categories.append("📊 STATYSTYKI")

        if weather_category is None:
            weather_category = await guild.create_category("🌦 POGODA")
            created_categories.append("🌦 POGODA")

        # =========================
        # DANE STATYSTYK
        # =========================
        members_count = guild.member_count or 0

        online_count = sum(
            1 for member in guild.members
            if not member.bot and member.status != discord.Status.offline
        )

        vc_count = sum(
            1 for member in guild.members
            if not member.bot and member.voice and member.voice.channel is not None
        )

        # =========================
        # LISTA KANAŁÓW
        # =========================
        stats_channels = [
            f"👥 Członkowie • {members_count}",
            f"🟢 Online • {online_count}",
            f"🎤 Na VC • {vc_count}",
        ]

        weather_channels = [
            "🌡 Temperatura • --°C",
            "🤒 Odczuwalna • --°C",
            "🌧 Opady • --",
            "💨 Wiatr • -- km/h",
            "🧭 Ciśnienie • -- hPa",
        ]

        created_channels = []
        existing_channels = []

        # =========================
        # TWORZENIE KANAŁÓW STATYSTYK
        # =========================
        for channel_name in stats_channels:
            channel, created = await create_voice_if_missing(
                guild=guild,
                category=stats_category,
                name=channel_name
            )
            if created:
                created_channels.append(channel.name)
            else:
                existing_channels.append(channel.name)

        # =========================
        # TWORZENIE KANAŁÓW POGODY
        # =========================
        for channel_name in weather_channels:
            channel, created = await create_voice_if_missing(
                guild=guild,
                category=weather_category,
                name=channel_name
            )
            if created:
                created_channels.append(channel.name)
            else:
                existing_channels.append(channel.name)

        # =========================
        # WIADOMOŚĆ KOŃCOWA
        # =========================
        msg_parts = []

        if created_categories:
            msg_parts.append(
                "✅ Utworzono kategorie:\n" +
                "\n".join(f"• {name}" for name in created_categories)
            )

        if created_channels:
            msg_parts.append(
                "✅ Utworzono kanały:\n" +
                "\n".join(f"• {name}" for name in created_channels)
            )

        if not created_categories and not created_channels:
            msg_parts.append("ℹ️ Panel już istnieje — nic nowego nie utworzono.")

        await interaction.followup.send(
            "\n\n".join(msg_parts),
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Bot nie ma uprawnień do tworzenia kategorii lub kanałów.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Wystąpił błąd podczas tworzenia panelu: {e}",
            ephemeral=True
        )


bot.run(TOKEN)
