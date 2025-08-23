import os
import re
import json
import unicodedata
import logging
from random import choice

import discord
from discord.ext import commands
from discord import app_commands
from pymongo import MongoClient

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# -------------------
# LOGGING
# -------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mdq")
logger.setLevel(logging.INFO)

# -------------------
# ENV / CONFIG
# -------------------
MONGO_URI = os.getenv("MONGO_URI")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
QUESTS_CHANNEL_ID = int(os.getenv("QUESTS_CHANNEL_ID", "0"))
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", "0"))
GUILD_ID = int(os.getenv("GUILD_ID", "0"))  # si défini, sync slash à l’instant sur cette guilde
DEBUG_LOG_MESSAGES = os.getenv("DEBUG_LOG_MESSAGES", "0") == "1"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!"),
    intents=intents,
    case_insensitive=True,
)

# -------------------
# DB
# -------------------
client = MongoClient(MONGO_URI) if MONGO_URI else None
db = client.lumharel_bot if client else None
accepted_collection = db.quetes_acceptees if db else None
completed_collection = db.quetes_terminees if db else None
utilisateurs = db.utilisateurs if db else None
rotation_collection = db.rotation_quetes if db else None

TZ_PARIS = pytz.timezone("Europe/Paris")

# -------------------
# CONSTANTES UI
# -------------------
EMOJI_PAR_CATEGORIE = {
    "Quêtes Journalières": "🕘",
    "Quêtes Interactions": "🕹️",
    "Quêtes Recherches": "🔍",
    "Quêtes Énigmes": "🧩",
}
COULEURS_PAR_CATEGORIE = {
    "Quêtes Journalières": 0x4CAF50,
    "Quêtes Interactions": 0x2196F3,
    "Quêtes Recherches": 0x9C27B0,
    "Quêtes Énigmes": 0xFFC107,
}

# -------------------
# UTILS
# -------------------
def ids_quetes(liste):
    return [q["id"] if isinstance(q, dict) else q for q in liste]

def normaliser(texte):
    if not isinstance(texte, str):
        return ""
    texte = texte.lower().strip()
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = texte.replace("’", "'")
    texte = re.sub(r'[“”«»]', '"', texte)
    texte = re.sub(r"\s+", " ", texte)
    texte = texte.replace("\u200b", "")
    return texte

def charger_quetes():
    with open("quetes.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    for categorie, quetes in data.items():
        for quete in quetes:
            quete["categorie"] = categorie
    return data

async def purger_messages_categorie(channel: discord.TextChannel, categorie: str, limit=100):
    prefix = EMOJI_PAR_CATEGORIE.get(categorie, "")
    async for message in channel.history(limit=limit):
        if message.author == bot.user and message.embeds:
            title = message.embeds[0].title or ""
            if title.startswith(prefix):
                try: await message.delete()
                except: pass

# -------------------
# BOUTONS PERSISTANTS
# -------------------
_QUEST_INDEX: dict[tuple[str, str], dict] = {}

def make_accept_view(quete_id: str, categorie: str) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    btn = discord.ui.Button(
        label="Accepter 📥",
        style=discord.ButtonStyle.green,
        custom_id=f"acc::{categorie}::{quete_id}",
    )
    async def _callback(interaction: discord.Interaction):
        await handle_accept_interaction(interaction, quete_id, categorie)
    btn.callback = _callback
    v.add_item(btn)
    return v

async def handle_accept_interaction(interaction: discord.Interaction, quete_id: str, categorie: str):
    if not all([accepted_collection, completed_collection, utilisateurs]):
        await interaction.response.send_message("DB non initialisée.", ephemeral=True)
        return

    user_id = str(interaction.user.id)
    quete = _QUEST_INDEX.get((categorie, quete_id))
    if not quete:
        for cat, lst in charger_quetes().items():
            for q in lst:
                if q["id"] == quete_id and cat == categorie:
                    quete = q
                    _QUEST_INDEX[(cat, quete_id)] = q
                    break
            if quete: break
    if not quete:
        await interaction.response.send_message("⚠️ Quête introuvable.", ephemeral=True)
        return

    quete_data = accepted_collection.find_one({"_id": user_id})
    if quete_data and any(q.get("id") == quete_id for q in quete_data.get("quetes", [])):
        await interaction.response.send_message("Tu as déjà accepté cette quête ! (`!mes_quetes`)", ephemeral=True)
        return

    deja_faite = completed_collection.find_one(
        {"_id": user_id, "quetes": {"$elemMatch": {"id": quete_id}}}
    )
    if deja_faite and categorie != "Quêtes Journalières":
        try:
            await interaction.user.send(
                f"📪 Tu as déjà terminé **{quete['nom']}** (non rejouable). `!mes_quetes` pour voir."
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Quête déjà terminée (non rejouable), et MP impossible.",
                ephemeral=True
            )
        return

    accepted_collection.update_one(
        {"_id": user_id},
        {"$addToSet": {
            "quetes": {"categorie": categorie, "id": quete_id, "nom": quete["nom"]}
        }, "$set": {"pseudo": interaction.user.name}},
        upsert=True
    )

    if categorie == "Quêtes Énigmes":
        embed = discord.Embed(
            title="🧩 Quête Énigmes",
            description=f"**{quete['id']} – {quete['nom']}**",
            color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
        )
        embed.add_field(name="💬 Énoncé", value=quete["enonce"], inline=False)
        embed.add_field(name="👉 Objectif", value="Trouve la réponse et réponds-moi ici.", inline=False)
        embed.set_footer(text=f"🏅 Récompense : {quete['recompense']} Lumes")
    else:
        embed = discord.Embed(
            title=f"{EMOJI_PAR_CATEGORIE.get(categorie, '📜')} {categorie}",
            description=f"**{quete['id']} – {quete['nom']}**",
            color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
        )
        embed.add_field(name="💬 Description", value=quete["description"], inline=False)
        embed.add_field(name="👉 Objectif", value=quete["details_mp"], inline=False)
        embed.set_footer(text=f"🏅 Récompense : {quete['recompense']} Lumes")

    try:
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Quête acceptée ✅ Regarde tes MP !", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP 😅", ephemeral=True)

async def register_persistent_views():
    quetes_par_type = charger_quetes()
    _QUEST_INDEX.clear()
    for categorie, lst in quetes_par_type.items():
        for q in lst:
            _QUEST_INDEX[(categorie, q["id"])] = q
            bot.add_view(make_accept_view(q["id"], categorie))

# -------------------
# ENVOI DES QUÊTES
# -------------------
async def envoyer_quete(channel, quete, categorie):
    emoji = EMOJI_PAR_CATEGORIE.get(categorie, "❓")
    couleur = COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
    titre = f"{emoji} {categorie}\n– {quete['id']} {quete['nom']}"

    embed = discord.Embed(title=titre, description=quete["resume"], color=couleur)
    embed.add_field(name="📌 Type & Récompense", value=f"{categorie} – {quete['recompense']} Lumes", inline=False)
    embed.set_footer(text="Clique sur le bouton ci-dessous pour accepter la quête.")

    await channel.send(embed=embed, view=make_accept_view(quete["id"], categorie))

def get_quete_non_postee(categorie, quetes_possibles):
    if not rotation_collection:
        return choice(quetes_possibles)
    doc = rotation_collection.find_one({"_id": categorie})
    deja_postees = doc["postees"] if doc else []
    restantes = [q for q in quetes_possibles if q["id"] not in deja_postees]
    if not restantes:
        restantes = quetes_possibles
        deja_postees = []
    quete = choice(restantes)
    rotation_collection.update_one(
        {"_id": categorie},
        {"$set": {"postees": deja_postees + [quete["id"]]}},
        upsert=True
    )
    return quete

# -------------------
# POSTERS
# -------------------
async def poster_journalieres():
    quetes_par_type = charger_quetes()
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if not channel:
        print("❌ Salon quêtes introuvable.")
        return
    await purger_messages_categorie(channel, "Quêtes Journalières", limit=100)
    for quete in quetes_par_type.get("Quêtes Journalières", [])[:2]:
        await envoyer_quete(channel, quete, "Quêtes Journalières")
    print("✅ Journalières postées.")

async def poster_hebdo():
    quetes_par_type = charger_quetes()
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if not channel:
        print("❌ Salon quêtes introuvable.")
        return

    interactions = quetes_par_type.get("Quêtes Interactions", [])
    if interactions:
        await purger_messages_categorie(channel, "Quêtes Interactions", limit=100)
        await envoyer_quete(channel, get_quete_non_postee("Quêtes Interactions", interactions), "Quêtes Interactions")

    recherches = quetes_par_type.get("Quêtes Recherches", [])
    if recherches:
        await purger_messages_categorie(channel, "Quêtes Recherches", limit=100)
        await envoyer_quete(channel, get_quete_non_postee("Quêtes Recherches", recherches), "Quêtes Recherches")

    enigmes = quetes_par_type.get("Quêtes Énigmes", [])
    if enigmes:
        await purger_messages_categorie(channel, "Quêtes Énigmes", limit=100)
        await envoyer_quete(channel, get_quete_non_postee("Quêtes Énigmes", enigmes), "Quêtes Énigmes")

    print("✅ Hebdomadaires postées.")

async def annoncer_mise_a_jour():
    if not ANNOUNCE_CHANNEL_ID:
        return
    ch = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if ch:
        await ch.send(
            "👋 Oyez oyez, @Aventuriers.ères 🥾 ! Les quêtes **journalières** et/ou **hebdomadaires** ont été mises à jour "
            f"dans <#{QUESTS_CHANNEL_ID}>. Puissent les Souffles vous être favorables 🌬️ !"
        )

# -------------------
# COMMANDES (prefix)
# -------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def poster_quetes(ctx):
    await poster_journalieres()
    await poster_hebdo()
    await annoncer_mise_a_jour()
    await ctx.reply("✅ Quêtes postées (journalières + hebdo).", mention_author=False)

@bot.command()
@commands.has_permissions(administrator=True)
async def journaliere(ctx):
    await poster_journalieres()
    await ctx.reply("✅ Journalières postées.", mention_author=False)

@bot.command()
@commands.has_permissions(administrator=True)
async def hebdo(ctx):
    await poster_hebdo()
    await ctx.reply("✅ Hebdomadaires postées.", mention_author=False)

@bot.command()
async def mes_quetes(ctx):
    if not all([accepted_collection, completed_collection]):
        await ctx.send("DB non initialisée.")
        return

    user_id = str(ctx.author.id)
    toutes_quetes = [q for lst in charger_quetes().values() for q in lst]

    user_accept = accepted_collection.find_one({"_id": user_id}) or {}
    user_done = completed_collection.find_one({"_id": user_id}) or {}

    quetes_accept = user_accept.get("quetes", [])
    quetes_done = user_done.get("quetes", [])

    ids_accept = set(q["id"] if isinstance(q, dict) else q for q in quetes_accept)
    ids_done = set(q.get("id") if isinstance(q, dict) else q for q in quetes_done)

    categories = {
        "Quêtes Journalières": {"emoji": "🕘", "encours": [], "terminees": []},
        "Quêtes Interactions": {"emoji": "🕹️", "encours": [], "terminees": []},
        "Quêtes Recherches": {"emoji": "🔍", "encours": [], "terminees": []},
        "Quêtes Énigmes": {"emoji": "🧩", "encours": [], "terminees": []},
    }

    for quete in toutes_quetes:
        cat = quete.get("categorie")
        if not cat or cat not in categories:
            continue
        ligne = f"• {quete['id']} – {quete['nom']}"
        if quete["id"] in ids_done:
            categories[cat]["terminees"].append(ligne)
        elif quete["id"] in ids_accept:
            categories[cat]["encours"].append(ligne)

    embed = discord.Embed(title=f"📘 Quêtes de {ctx.author.display_name}", color=0xA86E2A)
    desc = "📜 **Quêtes en cours**\n"
    for cat, data in categories.items():
        desc += f"{data['emoji']} __{cat.replace('Quêtes ', '')} :__\n"
        desc += "\n".join(data["encours"]) + "\n" if data["encours"] else "*Aucune*\n"

    desc += "\n🏅 **Quêtes terminées**\n"
    for cat, data in categories.items():
        desc += f"{data['emoji']} __{cat.replace('Quêtes ', '')} :__\n"
        desc += "\n".join(data["terminees"]) + "\n" if data["terminees"] else "*Aucune*\n"

    embed.description = desc
    await ctx.send(embed=embed)

@bot.command()
async def bourse(ctx):
    if not utilisateurs:
        await ctx.send("DB non initialisée.")
        return
    user_id = str(ctx.author.id)
    user = utilisateurs.find_one({"_id": user_id})
    if not user:
        utilisateurs.insert_one({
            "_id": user_id,
            "pseudo": ctx.author.name,
            "lumes": 0,
            "derniere_offrande": {},
            "roles_temporaires": {},
        })
        user = utilisateurs.find_one({"_id": user_id}) or {}
    await ctx.send(f"💰 {ctx.author.mention}, tu possèdes **{user.get('lumes', 0)} Lumes**.")

# -------------------
# SLASH COMMANDS (miroirs)
# -------------------
@bot.tree.command(name="ping", description="Test de latence")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong 🏓 ({round(bot.latency*1000)} ms)", ephemeral=True)

@bot.tree.command(name="poster_quetes", description="(Admin) Postez journalières + hebdo")
@app_commands.checks.has_permissions(administrator=True)
async def slash_poster_quetes(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await poster_journalieres()
    await poster_hebdo()
    await annoncer_mise_a_jour()
    await interaction.followup.send("✅ Quêtes postées.", ephemeral=True)

@bot.tree.command(name="journaliere", description="(Admin) Postez les journalières")
@app_commands.checks.has_permissions(administrator=True)
async def slash_journaliere(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await poster_journalieres()
    await interaction.followup.send("✅ Journalières postées.", ephemeral=True)

@bot.tree.command(name="hebdo", description="(Admin) Postez les hebdomadaires")
@app_commands.checks.has_permissions(administrator=True)
async def slash_hebdo(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await poster_hebdo()
    await interaction.followup.send("✅ Hebdomadaires postées.", ephemeral=True)

@bot.tree.command(name="sync", description="(Admin) Forcer la synchronisation des slash")
@app_commands.checks.has_permissions(administrator=True)
async def slash_sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.guild:
            synced = await bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(f"✅ Slash sync (guilde) : {len(synced)}", ephemeral=True)
        else:
            synced = await bot.tree.sync()
            await interaction.followup.send(f"✅ Slash sync (global) : {len(synced)}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Erreur sync : {e}", ephemeral=True)

# -------------------
# DIAG / PREVIEW
# -------------------
@bot.command()
async def poster_preview(ctx):
    msgs = []
    ok = True
    if not QUESTS_CHANNEL_ID:
        ok = False
        msgs.append("❌ `QUESTS_CHANNEL_ID` vide.")
    else:
        ch = bot.get_channel(QUESTS_CHANNEL_ID)
        if not ch:
            ok = False
            msgs.append(f"❌ Salon introuvable pour ID={QUESTS_CHANNEL_ID}.")
        else:
            perms = ch.permissions_for(ch.guild.me)
            if not perms.view_channel or not perms.send_messages:
                ok = False
                msgs.append("❌ Permissions insuffisantes (voir/écrire).")
            else:
                msgs.append("✅ Accès au salon quêtes OK.")
    await ctx.reply("\n".join(msgs) if msgs else "OK", mention_author=False)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        return await ctx.reply("❌ Il faut être **administrateur**.", mention_author=False)
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        print("⚠️ Command error:", repr(error))
        await ctx.reply("⚠️ Erreur pendant la commande (voir logs).", mention_author=False)

@bot.event
async def on_message(message: discord.Message):
    if DEBUG_LOG_MESSAGES:
        print(f"[DBG] msg in #{getattr(message.channel,'name',message.channel.id)} by {message.author}: {message.content}")
    await bot.process_commands(message)

# -------------------
# SCHEDULER & HOOKS
# -------------------
_scheduler = None

@bot.event
async def setup_hook():
    try:
        await register_persistent_views()
        print("🧷 Views persistantes enregistrées.")
    except Exception as e:
        print("⚠️ register_persistent_views error:", e)

    # Sync slash au démarrage
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print("🌿 Slash synchronisées (guild).")
        else:
            await bot.tree.sync()
            print("🌿 Slash synchronisées (global).")
    except Exception as e:
        print("⚠️ Slash sync error:", e)

@bot.event
async def on_ready():
    print(f"✅ SMOKE: connecté en tant que {bot.user} (latence {round(bot.latency*1000)} ms)")
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=TZ_PARIS)
        _scheduler.add_job(lambda: bot.loop.create_task(poster_journalieres()), CronTrigger(hour=10, minute=30))
        _scheduler.add_job(lambda: bot.loop.create_task(poster_hebdo()), CronTrigger(day_of_week='mon', hour=10, minute=31))
        if ANNOUNCE_CHANNEL_ID:
            _scheduler.add_job(lambda: bot.loop.create_task(annoncer_mise_a_jour()), CronTrigger(day_of_week='mon', hour=10, minute=32))
        _scheduler.start()
        print("⏰ Scheduler démarré.")

# -------------------
# RUN
# -------------------
if __name__ == "__main__":
    missing = []
    if not DISCORD_TOKEN: missing.append("DISCORD_TOKEN")
    if not MONGO_URI: missing.append("MONGO_URI")
    if not QUESTS_CHANNEL_ID: missing.append("QUESTS_CHANNEL_ID")
    if missing:
        print("❌ Variables manquantes :", ", ".join(missing))
    bot.run(DISCORD_TOKEN)
