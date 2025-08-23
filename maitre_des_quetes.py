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

async def envoyer_quete(channel, quete, categorie):
    emoji = EMOJI_PAR_CATEGORIE.get(categorie, "❓")
    couleur = COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
    titre = f"{emoji} {categorie}\n– {quete['id']} {quete['nom']}"

    embed = discord.Embed(title=titre, description=quete["resume"], color=couleur)
    type_texte = f"{categorie} – {quete['recompense']} Lumes"
    embed.add_field(name="📌 Type & Récompense", value=type_texte, inline=False)
    embed.set_footer(text="Clique sur le bouton ci-dessous pour accepter la quête.")
    await channel.send(embed=embed, view=VueAcceptation(quete, categorie))

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
#  VUE BOUTON "ACCEPTER"
# ======================
class VueAcceptation(View):
    def __init__(self, quete, categorie):
        super().__init__(timeout=None)
        self.quete = quete
        self.categorie = categorie

    @discord.ui.button(label="Accepter 📥", style=discord.ButtonStyle.green)
    async def accepter(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        quete_id = self.quete["id"]

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
        if deja_faite and self.categorie != "Quêtes Journalières":
            try:
                await interaction.user.send(
                    f"📪 Tu as déjà terminé **{self.quete['nom']}** (non rejouable). "
                    "Consulte `!mes_quetes`."
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Tu as déjà terminé cette quête (non rejouable), et je ne peux pas t’envoyer de MP.",
                    ephemeral=True
                )
            return

        accepted_collection.update_one(
            {"_id": user_id},
            {"$addToSet": {
                "quetes": {
                    "categorie": self.categorie,
                    "id": quete_id,
                    "nom": self.quete["nom"]
                }
            }, "$set": {"pseudo": interaction.user.name}},
            upsert=True
        )

        # MP d’instructions
        if self.categorie == "Quêtes Énigmes":
            embed = discord.Embed(
                title="🧩 Quête Énigmes",
                description=f"**{self.quete['id']} – {self.quete['nom']}**",
                color=COULEURS_PAR_CATEGORIE.get(self.categorie, 0xCCCCCC)
            )
            embed.add_field(name="💬 Énoncé", value=self.quete["enonce"], inline=False)
            embed.add_field(name="👉 Objectif", value="Trouve la réponse et réponds-moi ici.", inline=False)
            embed.set_footer(text=f"🏅 Récompense : {self.quete['recompense']} Lumes")
        else:
            titre_embed = f"{EMOJI_PAR_CATEGORIE.get(self.categorie, '📜')} {self.categorie}"
            embed = discord.Embed(
                title=titre_embed,
                description=f"**{self.quete['id']} – {self.quete['nom']}**",
                color=COULEURS_PAR_CATEGORIE.get(self.categorie, 0xCCCCCC)
            )
            embed.add_field(name="💬 Description", value=self.quete["description"], inline=False)
            embed.add_field(name="👉 Objectif", value=self.quete["details_mp"], inline=False)
            embed.set_footer(text=f"🏅 Récompense : {self.quete['recompense']} Lumes")

        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message(
                "Quête acceptée ✅ Regarde tes MP ! (`!mes_quetes` pour le suivi)",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP 😅", ephemeral=True)

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
            "👋 Oyez oyez, <@&1345479226886979641> ! Les quêtes **journalières** et/ou **hebdomadaires** ont été mises à jour "
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

    embed.description = desc
    await ctx.send(embed=embed)

@bot.command()
async def bourse(ctx):
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

# ======================
#  EVENTS: COMPLETION
# ======================
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member is None or payload.member.bot:
        return

    user = payload.member
    user_id = str(payload.user_id)
    emoji = str(payload.emoji)

    quetes = charger_quetes()
    user_data = accepted_collection.find_one({"_id": user_id})
    if not user_data:
        return

    quetes_acceptees = user_data.get("quetes", [])
    toutes_quetes = [q for lst in quetes.values() for q in lst]

    for quete in toutes_quetes:
        if quete.get("type") != "reaction":
            continue
        if quete["id"] not in [q["id"] if isinstance(q, dict) else q for q in quetes_acceptees]:
            continue

        liste_emojis = quete.get("emoji", [])
        if isinstance(liste_emojis, str):
            liste_emojis = [liste_emojis]

        if emoji in liste_emojis:
            accepted_collection.update_one({"_id": user_id}, {"$pull": {"quetes": {"id": quete["id"]}}})
            completed_collection.update_one(
                {"_id": user_id},
                {"$addToSet": {"quetes": {"id": quete["id"], "nom": quete["nom"], "categorie": quete["categorie"]}},
                 "$set": {"pseudo": user.name}},
                upsert=True
            )
            utilisateurs.update_one(
                {"_id": user_id},
                {"$inc": {"lumes": quete["recompense"]},
                 "$setOnInsert": {"pseudo": user.name, "derniere_offrande": {}, "roles_temporaires": {}}},
                upsert=True
            )
            try:
                await user.send(f"✨ Tu as terminé **{quete['nom']}** et gagné **{quete['recompense']} Lumes** !")
            except discord.Forbidden:
                ch = bot.get_channel(payload.channel_id)
                if ch:
                    await ch.send(f"✅ {user.mention} a terminé **{quete['nom']}** ! (MP non reçu)")
            return

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Réponse aux énigmes en MP
    if isinstance(message.channel, discord.DMChannel):
        user = message.author
        user_id = str(user.id)
        contenu = message.content.strip()

        quetes = charger_quetes()
        user_data = accepted_collection.find_one({"_id": user_id})
        if not user_data:
            return

        quetes_acceptees = user_data.get("quetes", [])
        toutes_quetes = [q for lst in quetes.values() for q in lst]

        for quete in toutes_quetes:
            if quete["id"] not in [q["id"] if isinstance(q, dict) else q for q in quetes_acceptees]:
                continue

            bonne = normaliser(quete.get("reponse_attendue", ""))
            if normaliser(contenu) == bonne:
                accepted_collection.update_one({"_id": user_id}, {"$pull": {"quetes": {"id": quete["id"]}}})
                completed_collection.update_one(
                    {"_id": user_id},
                    {"$addToSet": {"quetes": {"id": quete["id"], "nom": quete["nom"], "categorie": quete["categorie"]}},
                     "$set": {"pseudo": user.name}},
                    upsert=True
                )
                utilisateurs.update_one(
                    {"_id": user_id},
                    {"$inc": {"lumes": quete["recompense"]},
                     "$setOnInsert": {"pseudo": user.name, "derniere_offrande": {}, "roles_temporaires": {}}},
                    upsert=True
                )
                await message.channel.send(
                    f"✅ Parfait ! Tu as complété **{quete['nom']}** et gagné **{quete['recompense']} Lumes** !"
                )
                return

    await bot.process_commands(message)

# ======================
#  SCHEDULER
# ======================
_scheduler = None

@bot.event
async def on_ready():
    global _scheduler
    print(f"✅ Bot prêt : {bot.user}")

    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=TZ_PARIS)
        # Tous les jours 10:30 → journalières
        _scheduler.add_job(lambda: bot.loop.create_task(poster_journalieres()),
                           CronTrigger(hour=10, minute=30))
        # Chaque lundi 10:31 → hebdo (décalé d’1 min pour éviter concurrence)
        _scheduler.add_job(lambda: bot.loop.create_task(poster_hebdo()),
                           CronTrigger(day_of_week='mon', hour=10, minute=31))
        # Annonce après chaque post hebdo
        if ANNOUNCE_CHANNEL_ID:
            _scheduler.add_job(lambda: bot.loop.create_task(annoncer_mise_a_jour()),
                               CronTrigger(day_of_week='mon', hour=10, minute=32))

        _scheduler.start()
        print("⏰ Scheduler démarré (journalières quotidiennes, hebdo le lundi).")

# ======================
#  RUN
# ======================
if __name__ == "__main__":
    if not DISCORD_TOKEN or not MONGO_URI or not QUESTS_CHANNEL_ID:
        print("❌ DISCORD_TOKEN / MONGO_URI / QUESTS_CHANNEL_ID manquant(s).")
    bot.run(DISCORD_TOKEN)
