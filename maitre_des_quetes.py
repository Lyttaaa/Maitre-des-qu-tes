import os
import re
import json
import unicodedata
from random import choice

import discord
from discord.ext import commands
from discord.ui import View
from pymongo import MongoClient

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# ======================
#  CONFIG DISCORD & DB
# ======================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

MONGO_URI = os.getenv("MONGO_URI")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
QUESTS_CHANNEL_ID = int(os.getenv("QUESTS_CHANNEL_ID", "0"))
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", "0"))  # optionnel

client = MongoClient(MONGO_URI)
db = client.lumharel_bot
accepted_collection = db.quetes_acceptees
completed_collection = db.quetes_terminees
utilisateurs = db.utilisateurs
rotation_collection = db.rotation_quetes

TZ_PARIS = pytz.timezone("Europe/Paris")

# ======================
#  CONSTANTES UI
# ======================
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

# ======================
#  UTILS
# ======================
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
    # Injecter la catégorie dans chaque quête
    for categorie, quetes in data.items():
        for quete in quetes:
            quete["categorie"] = categorie
    return data

async def purger_messages_categorie(channel: discord.TextChannel, categorie: str, limit=100):
    """
    Supprime uniquement les anciens messages du bot qui contiennent un embed
    dont le titre commence par l’emoji de la catégorie.
    """
    prefix = EMOJI_PAR_CATEGORIE.get(categorie, "")
    async for message in channel.history(limit=limit):
        if message.author == bot.user and message.embeds:
            title = message.embeds[0].title or ""
            if title.startswith(prefix):
                try:
                    await message.delete()
                except:
                    pass

# ======================
#  BOUTONS PERSISTANTS
# ======================
# index (catégorie, id) -> quete
_QUEST_INDEX: dict[tuple[str, str], dict] = {}

def make_accept_view(quete_id: str, categorie: str) -> discord.ui.View:
    """
    Crée une View persistante avec un custom_id stable.
    """
    v = discord.ui.View(timeout=None)
    btn = discord.ui.Button(
        label="Accepter 📥",
        style=discord.ButtonStyle.green,
        custom_id=f"acc::{categorie}::{quete_id}",  # ID stable pour persistance
    )

    async def _callback(interaction: discord.Interaction):
        await handle_accept_interaction(interaction, quete_id, categorie)

    btn.callback = _callback
    v.add_item(btn)
    return v

async def handle_accept_interaction(interaction: discord.Interaction, quete_id: str, categorie: str):
    """
    Logique d'acceptation d'une quête (remplace l'ancienne VueAcceptation.accepter)
    """
    user_id = str(interaction.user.id)

    # Récupère la quête depuis l'index ; fallback: recharge quetes.json
    quete = _QUEST_INDEX.get((categorie, quete_id))
    if not quete:
        for cat, lst in charger_quetes().items():
            for q in lst:
                if q["id"] == quete_id and cat == categorie:
                    quete = q
                    _QUEST_INDEX[(cat, quete_id)] = q
                    break
            if quete:
                break
    if not quete:
        await interaction.response.send_message("⚠️ Quête introuvable.", ephemeral=True)
        return

    # déjà acceptée ?
    quete_data = accepted_collection.find_one({"_id": user_id})
    if quete_data and any(q.get("id") == quete_id for q in quete_data.get("quetes", [])):
        await interaction.response.send_message(
            "Tu as déjà accepté cette quête ! Consulte `!mes_quetes`.",
            ephemeral=True
        )
        return

    # déjà terminée ? (sauf journalières)
    deja_faite = completed_collection.find_one(
        {"_id": user_id, "quetes": {"$elemMatch": {"id": quete_id}}}
    )
    if deja_faite and categorie != "Quêtes Journalières":
        try:
            await interaction.user.send(
                f"📪 Tu as déjà terminé **{quete['nom']}** (non rejouable). Consulte `!mes_quetes`."
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Tu as déjà terminé cette quête (non rejouable), et je ne peux pas t’envoyer de MP.",
                ephemeral=True
            )
        return

    # Enregistre l'acceptation
    accepted_collection.update_one(
        {"_id": user_id},
        {"$addToSet": {
            "quetes": {
                "categorie": categorie,
                "id": quete_id,
                "nom": quete["nom"]
            }
        }, "$set": {"pseudo": interaction.user.name}},
        upsert=True
    )

    # Prépare le MP d’instructions
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
        titre_embed = f"{EMOJI_PAR_CATEGORIE.get(categorie, '📜')} {categorie}"
        embed = discord.Embed(
            title=titre_embed,
            description=f"**{quete['id']} – {quete['nom']}**",
            color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
        )
        embed.add_field(name="💬 Description", value=quete["description"], inline=False)
        embed.add_field(name="👉 Objectif", value=quete["details_mp"], inline=False)
        embed.set_footer(text=f"🏅 Récompense : {quete['recompense']} Lumes")

    # Réponse à l’interaction (toujours répondre)
    try:
        await interaction.user.send(embed=embed)
        await interaction.response.send_message(
            "Quête acceptée ✅ Regarde tes MP ! (`!mes_quetes` pour le suivi)",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP 😅", ephemeral=True)

async def register_persistent_views():
    """
    Enregistre TOUTES les views persistantes des quêtes existantes.
    Ainsi, les anciens boutons continuent de marcher après redémarrage.
    """
    quetes_par_type = charger_quetes()
    _QUEST_INDEX.clear()
    for categorie, lst in quetes_par_type.items():
        for q in lst:
            _QUEST_INDEX[(categorie, q["id"])] = q
            bot.add_view(make_accept_view(q["id"], categorie))

# ======================
#  ENVOI DES QUÊTES
# ======================
async def envoyer_quete(channel, quete, categorie):
    emoji = EMOJI_PAR_CATEGORIE.get(categorie, "❓")
    couleur = COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
    titre = f"{emoji} {categorie}\n– {quete['id']} {quete['nom']}"

    embed = discord.Embed(title=titre, description=quete["resume"], color=couleur)
    type_texte = f"{categorie} – {quete['recompense']} Lumes"
    embed.add_field(name="📌 Type & Récompense", value=type_texte, inline=False)
    embed.set_footer(text="Clique sur le bouton ci-dessous pour accepter la quête.")

    # View persistante
    await channel.send(embed=embed, view=make_accept_view(quete["id"], categorie))

def get_quete_non_postee(categorie, quetes_possibles):
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

# ======================
#  POSTERS
# ======================
async def poster_journalieres():
    """Poste seulement les 2 quêtes journalières (tous les jours)."""
    quetes_par_type = charger_quetes()
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if not channel:
        print("❌ Channel quêtes introuvable.")
        return

    await purger_messages_categorie(channel, "Quêtes Journalières", limit=100)
    for quete in quetes_par_type.get("Quêtes Journalières", [])[:2]:
        await envoyer_quete(channel, quete, "Quêtes Journalières")
    print("✅ Journalières postées.")

async def poster_hebdo():
    """Poste 1 interaction + 1 recherche + 1 énigme avec rotation (chaque semaine)."""
    quetes_par_type = charger_quetes()
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if not channel:
        print("❌ Channel quêtes introuvable.")
        return

    # Interactions
    interactions = quetes_par_type.get("Quêtes Interactions", [])
    if interactions:
        await purger_messages_categorie(channel, "Quêtes Interactions", limit=100)
        q = get_quete_non_postee("Quêtes Interactions", interactions)
        await envoyer_quete(channel, q, "Quêtes Interactions")

    # Recherches
    recherches = quetes_par_type.get("Quêtes Recherches", [])
    if recherches:
        await purger_messages_categorie(channel, "Quêtes Recherches", limit=100)
        q = get_quete_non_postee("Quêtes Recherches", recherches)
        await envoyer_quete(channel, q, "Quêtes Recherches")

    # Énigmes
    enigmes = quetes_par_type.get("Quêtes Énigmes", [])
    if enigmes:
        await purger_messages_categorie(channel, "Quêtes Énigmes", limit=100)
        q = get_quete_non_postee("Quêtes Énigmes", enigmes)
        await envoyer_quete(channel, q, "Quêtes Énigmes")

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

# ======================
#  COMMANDES
# ======================
@bot.command()
@commands.has_permissions(administrator=True)
async def poster_quetes(ctx):
    """Poste tout d’un coup (journalières + hebdo) — commande admin."""
    await poster_journalieres()
    await poster_hebdo()
    await annoncer_mise_a_jour()
    await ctx.reply("✅ Quêtes postées (journalières + hebdo).")

@bot.command()
@commands.has_permissions(administrator=True)
async def journaliere(ctx):
    await poster_journalieres()
    await ctx.reply("✅ Journalières postées.")

@bot.command()
@commands.has_permissions(administrator=True)
async def hebdo(ctx):
    await poster_hebdo()
    await ctx.reply("✅ Hebdomadaires postées.")

@bot.command()
async def mes_quetes(ctx):
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

    embed = discord.Embed(
        title=f"📘 Quêtes de {ctx.author.display_name}",
        color=0xA86E2A
    )
    desc = "📜 **Quêtes en cours**\n"
    for cat, data in categories.items():
        desc += f"{data['emoji']} __{cat.replace('Quêtes ', '')} :__\n"
        desc += "\n".join(data["encours"]) + "\n" if data["encours"] else "*Aucune*\n"

    desc += "\n🏅 **Quêtes terminées**\n"
    for cat, data in categories.items():
        desc += f"{data['emoji']} __{cat.replace('Quêtes ', '')} :__\n"
        desc += "\n".join(data["terminees"]) + "\n" if data["terminees"] else "*Aucune*\n"

    embed
