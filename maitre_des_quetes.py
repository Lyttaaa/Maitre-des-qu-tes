import os
import re
import json
import unicodedata
from random import choice

import discord
from discord.ext import commands
from discord.ui import View

# --- MongoDB (safe & required) ---
try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None

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

CHEMIN_QUETES = os.getenv("CHEMIN_QUETES", "quetes.json")
MONGO_URI = os.getenv("MONGO_URI")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")  # token du Maître des Quêtes
QUESTS_CHANNEL_ID = int(os.getenv("QUESTS_CHANNEL_ID", "0"))
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", "0"))  # optionnel

if not (MongoClient and MONGO_URI):
    raise RuntimeError("MONGO_URI + pymongo requis pour le Maître des Quêtes.")

client = MongoClient(MONGO_URI)
db = client.get_database("lumharel_bot")

accepted_collection  = db.quetes_acceptees
completed_collection = db.quetes_terminees
utilisateurs         = db.utilisateurs
rotation_collection  = db.rotation_quetes
user_state           = db.user_state   # état 'active_interaction' lu par le bot PNJ

TZ_PARIS = pytz.timezone("Europe/Paris")

# ======================
#  CONSTANTES UI
# ======================
EMOJI_PAR_CATEGORIE = {
    "Quêtes Journalières": "🕘",
    "Quêtes Interactions": "🕹️",
    "Quêtes Recherches":   "🔍",
    "Quêtes Énigmes":      "🧩",
}
COULEURS_PAR_CATEGORIE = {
    "Quêtes Journalières": 0x4CAF50,
    "Quêtes Interactions": 0x2196F3,
    "Quêtes Recherches":   0x9C27B0,
    "Quêtes Énigmes":      0xFFC107,
}

NO_MENTIONS = discord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=False)

# ======================
#  LOADER QUÊTES
# ======================
QUETES_RAW = None
QUETES_INDEX = {}      # {"QE012": {quete}}
CATEGORIE_PAR_ID = {}  # {"QE012": "Quêtes Énigmes"}

def _normalize_cat_name(cat: str) -> str:
    if "Interaction" in cat: return "Quêtes Interactions"
    if "Recherche"  in cat: return "Quêtes Recherches"
    if "Énigme" in cat or "Enigme" in cat: return "Quêtes Énigmes"
    if "Journalière" in cat or "Journaliere" in cat: return "Quêtes Journalières"
    return cat

def charger_toutes_les_quetes():
    """Charge le JSON une seule fois, crée index & catégories par ID."""
    global QUETES_RAW, QUETES_INDEX, CATEGORIE_PAR_ID
    if QUETES_RAW is not None:
        return

    with open(CHEMIN_QUETES, "r", encoding="utf-8") as f:
        QUETES_RAW = json.load(f)

    QUETES_INDEX.clear()
    CATEGORIE_PAR_ID.clear()

    for cat, lst in QUETES_RAW.items():
        if not isinstance(lst, list):
            continue
        cat_norm = _normalize_cat_name(cat)
        for q in lst:
            qid = (q.get("id") or "").upper()
            if not qid:
                continue
            QUETES_INDEX[qid] = q
            CATEGORIE_PAR_ID[qid] = cat_norm

def charger_quete_par_id(quest_id: str):
    charger_toutes_les_quetes()
    return QUETES_INDEX.get((quest_id or "").upper())

def categorie_par_id(quest_id: str) -> str:
    charger_toutes_les_quetes()
    return CATEGORIE_PAR_ID.get((quest_id or "").upper(), "Quête")

def charger_quetes_groupes():
    """Retourne un dict {cat_norm: [quetes]} et injecte la clé 'categorie' dans chaque quete."""
    charger_toutes_les_quetes()
    groupes = {"Quêtes Journalières": [], "Quêtes Interactions": [], "Quêtes Recherches": [], "Quêtes Énigmes": []}
    for qid, q in QUETES_INDEX.items():
        cat = CATEGORIE_PAR_ID.get(qid, "Quête")
        q = dict(q)  # copie légère pour injection non destructive
        q["categorie"] = cat
        if cat in groupes:
            groupes[cat].append(q)
    return groupes

# ======================
#  UTILS
# ======================
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

async def purger_messages_categorie(channel: discord.TextChannel, categorie: str, limit=100):
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
    embed = discord.Embed(title=titre, description=quete.get("resume",""), color=couleur)

    # ✅ mention discrète si multi-étapes
    if quete.get("type") == "multi_step":
        embed.add_field(name="🔁 Progression", value="Quête à plusieurs étapes", inline=False)

    type_texte = f"{categorie} – {quete.get('recompense',0)} Lumes"
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

        # Enregistrer l'acceptation
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

        # ➕ État actif (utilisé par le bot PNJ) — UNE SEULE FOIS
        if self.categorie == "Quêtes Interactions":
            etat = {
                "quest_id": self.quete["id"],
                "type": self.quete.get("type", "interaction"),  # "multi_step" ou "interaction"
                "pnj": (self.quete.get("pnj") or "").strip(),
                "current_step": 1 if self.quete.get("type") == "multi_step" else None,
                "awaiting_reaction": False,
                "emoji": None
            }
            user_state.update_one(
                {"_id": str(interaction.user.id)},
                {"$set": {"active_interaction": etat}},
                upsert=True
            )

        # MP d’instructions
        if self.categorie == "Quêtes Énigmes":
            embed = discord.Embed(
                title="🧩 Quête Énigmes",
                description=f"**{self.quete['id']} – {self.quete['nom']}**",
                color=COULEURS_PAR_CATEGORIE.get(self.categorie, 0xCCCCCC)
            )
            img = self.quete.get("image_url")
            if img:
                embed.add_field(name="💬 Rébus", value="Observe bien ce symbole...", inline=False)
                embed.set_image(url=img)
            else:
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

    if self.quete.get("type") == "multi_step":
        steps = self.quete.get("steps", [])
        step1 = steps[0] if steps else {}
        # 💬 description courte
        if self.quete.get("description"):
            embed.add_field(name="💬 Description", value=self.quete["description"], inline=False)

        # 🧭 Étape actuelle uniquement
        lignes = []
        # Lieu (channel / channel_id)
        ch_nom = step1.get("channel")
        ch_id = step1.get("channel_id")
        if ch_nom:
            lignes.append(f"• **Lieu** : `#{ch_nom}`")
        elif ch_id:
            lignes.append(f"• **Lieu** : <#{ch_id}>")

        # Action attendue
        mots = step1.get("mots_cles") or []
        if mots:
            lignes.append("• **Action** : écris un message contenant : " + ", ".join(f"`{m}`" for m in mots))
        if step1.get("emoji"):
            lignes.append(f"• **Validation** : réagis avec {step1['emoji']} sur le message du PNJ")

        embed.add_field(name="🚶 Étape 1", value="\n".join(lignes) or "Suis les indications du PNJ.", inline=False)
        embed.add_field(name="🔁 Progression", value="Quête à plusieurs étapes (les prochaines te seront révélées au fur et à mesure).", inline=False)
    else:
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
    groupes = charger_quetes_groupes()
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if not channel:
        print("❌ Channel quêtes introuvable.")
        return

    await purger_messages_categorie(channel, "Quêtes Journalières", limit=100)
    for quete in groupes.get("Quêtes Journalières", [])[:2]:
        await envoyer_quete(channel, quete, "Quêtes Journalières")
    print("✅ Journalières postées.")

async def poster_hebdo():
    groupes = charger_quetes_groupes()
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if not channel:
        print("❌ Channel quêtes introuvable.")
        return

    # Interactions
    interactions = groupes.get("Quêtes Interactions", [])
    if interactions:
        await purger_messages_categorie(channel, "Quêtes Interactions", limit=100)
        q = get_quete_non_postee("Quêtes Interactions", interactions)
        await envoyer_quete(channel, q, "Quêtes Interactions")

    # Recherches
    recherches = groupes.get("Quêtes Recherches", [])
    if recherches:
        await purger_messages_categorie(channel, "Quêtes Recherches", limit=100)
        q = get_quete_non_postee("Quêtes Recherches", recherches)
        await envoyer_quete(channel, q, "Quêtes Recherches")

    # Énigmes
    enigmes = groupes.get("Quêtes Énigmes", [])
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
    await poster_journalieres()
    await poster_hebdo()
    await annoncer_mise_a_jour()
    await ctx.reply("✅ Quêtes postées (journalières + hebdo).", allowed_mentions=NO_MENTIONS)

@bot.command()
@commands.has_permissions(administrator=True)
async def journaliere(ctx):
    await poster_journalieres()
    await ctx.reply("✅ Journalières postées.", allowed_mentions=NO_MENTIONS)

@bot.command()
@commands.has_permissions(administrator=True)
async def hebdo(ctx):
    await poster_hebdo()
    await ctx.reply("✅ Hebdomadaires postées.", allowed_mentions=NO_MENTIONS)

# ======================
#  COMMANDES TEST
# ======================

@bot.command(name="tester_quete")
@commands.has_permissions(administrator=True)
async def tester_quete(ctx, quest_id: str, channel: discord.TextChannel = None):
    """Poste une quête précise avec le bouton Accepter, sans toucher à la rotation."""
    quest_id = (quest_id or "").upper().strip()
    quete = charger_quete_par_id(quest_id)
    if not quete:
        await ctx.reply(f"Je ne trouve pas la quête `{quest_id}`.", allowed_mentions=NO_MENTIONS)
        return

    categorie = categorie_par_id(quest_id)
    target = channel or ctx.channel
    await envoyer_quete(target, quete, categorie)
    await ctx.reply(f"✅ Quête **{quest_id}** postée pour test dans {target.mention}.", allowed_mentions=NO_MENTIONS)


@bot.command(name="forcer_accept")
@commands.has_permissions(administrator=True)
async def forcer_accept(ctx, quest_id: str, membre: discord.Member = None):
    """
    Simule l'acceptation d'une quête : envoie le MP d'instructions et,
    pour les Interactions, crée l'active_interaction (utilisé par le bot PNJ).
    """
    quest_id = (quest_id or "").upper().strip()
    quete = charger_quete_par_id(quest_id)
    if not quete:
        await ctx.reply(f"Je ne trouve pas la quête `{quest_id}`.", allowed_mentions=NO_MENTIONS)
        return

    categorie = categorie_par_id(quest_id)
    user = membre or ctx.author
    user_id = str(user.id)

    # déjà acceptée ?
    quete_data = accepted_collection.find_one({"_id": user_id}) or {}
    if any(q.get("id") == quest_id for q in quete_data.get("quetes", [])):
        await ctx.reply("Cette personne a déjà accepté cette quête.", allowed_mentions=NO_MENTIONS)
        return

    # déjà finie ? (sauf journalières)
    if categorie != "Quêtes Journalières":
        deja = completed_collection.find_one({"_id": user_id, "quetes": {"$elemMatch": {"id": quest_id}}})
        if deja:
            await ctx.reply("Cette personne a déjà terminé cette quête (non rejouable).", allowed_mentions=NO_MENTIONS)
            return

    # Enregistrer l'acceptation (comme le bouton)
    accepted_collection.update_one(
        {"_id": user_id},
        {"$addToSet": {"quetes": {"categorie": categorie, "id": quest_id, "nom": quete["nom"]}},
         "$set": {"pseudo": user.name}},
        upsert=True
    )

    # État actif pour les Interactions (pour réveiller le PNJ)
    if categorie == "Quêtes Interactions":
        etat = {
            "quest_id": quete["id"],
            "type": quete.get("type", "interaction"),
            "pnj": (quete.get("pnj") or "").strip(),
            "current_step": 1 if quete.get("type") == "multi_step" else None,
            "awaiting_reaction": False,
            "emoji": None
        }
        user_state.update_one({"_id": user_id}, {"$set": {"active_interaction": etat}}, upsert=True)

    # MP d’instructions identique au bouton
    if categorie == "Quêtes Énigmes":
        embed = discord.Embed(
            title="🧩 Quête Énigmes",
            description=f"**{quete['id']} – {quete['nom']}**",
            color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
        )
        img = quete.get("image_url")
        if img:
            embed.add_field(name="💬 Rébus", value="Observe bien ce symbole...", inline=False)
            embed.set_image(url=img)
        else:
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

    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        await ctx.reply("Je ne peux pas DM cette personne (MP fermés).", allowed_mentions=NO_MENTIONS)
        return

    await ctx.reply(f"✅ **{user.display_name}** a reçu la quête **{quest_id}** en DM.", allowed_mentions=NO_MENTIONS)

@bot.command(name="show_quete")
async def show_quete(ctx, quest_id: str = None):
    if not quest_id:
        await ctx.send("Usage : `!show_quete <ID>` (ex: `!show_quete QE012`)", allowed_mentions=NO_MENTIONS)
        return

    quest_id = quest_id.strip().upper()
    quete = charger_quete_par_id(quest_id)
    if not quete:
        await ctx.send(f"Je ne trouve pas la quête `{quest_id}`.", allowed_mentions=NO_MENTIONS)
        return

    categorie = categorie_par_id(quest_id)

    if categorie == "Quêtes Énigmes":
        embed = discord.Embed(
            title="🧩 Quête Énigmes (APERÇU)",
            description=f"**{quete['id']} – {quete['nom']}**",
            color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
        )
        img = quete.get("image_url")
        if img:
            embed.add_field(name="💬 Rébus", value="Observe bien ce symbole...", inline=False)
            embed.set_image(url=img)
        else:
            embed.add_field(name="💬 Énoncé", value=quete["enonce"], inline=False)
        embed.add_field(name="👉 Objectif", value="Trouve la réponse et réponds-moi ici.", inline=False)
        embed.set_footer(text=f"🏅 Récompense : {quete['recompense']} Lumes")

    elif categorie == "Quêtes Recherches":
        embed = discord.Embed(
            title=f"🔎 {categorie} (APERÇU)",
            description=f"**{quete['id']} – {quete['nom']}**",
            color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
        )
        embed.add_field(name="💬 Indice", value=quete["description"], inline=False)
        embed.add_field(name="👉 Objectif", value=quete["details_mp"], inline=False)
        embed.set_footer(text=f"🏅 Récompense : {quete['recompense']} Lumes")

    else:  # Interactions
        embed = discord.Embed(
            title=f"🤝 {categorie} (APERÇU)",
            description=f"**{quete['id']} – {quete['nom']}**",
            color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
        )
        embed.add_field(name="💬 Description", value=quete["description"], inline=False)
        embed.add_field(name="👉 Objectif", value=quete["details_mp"], inline=False)
        embed.set_footer(text=f"🏅 Récompense : {quete['recompense']} Lumes")

    await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

# ======================
#  COMMANDES JOUEURS
# ======================

@bot.command()
async def mes_quetes(ctx):
    """Affiche les quêtes en cours et terminées de l'utilisateur."""
    user_id = str(ctx.author.id)
    charger_toutes_les_quetes()
    toutes_quetes = [q for lst in QUETES_RAW.values() if isinstance(lst, list) for q in lst]

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
        cat = CATEGORIE_PAR_ID.get(quete.get("id"), "Autres")
        if cat not in categories:
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
    await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)


@bot.command()
async def bourse(ctx):
    """Affiche le nombre de Lumes du joueur."""
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

    await ctx.send(f"💰 {ctx.author.mention}, tu possèdes **{user.get('lumes', 0)} Lumes**.", allowed_mentions=NO_MENTIONS)


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

    groupes = charger_quetes_groupes()
    user_data = accepted_collection.find_one({"_id": user_id})
    if not user_data:
        return

    quetes_acceptees = user_data.get("quetes", [])
    toutes_quetes = [q for lst in groupes.values() for q in lst]

    for quete in toutes_quetes:
        if quete.get("type") != "reaction":
            continue
        ids_accept = [q["id"] if isinstance(q, dict) else q for q in quetes_acceptees]
        if quete["id"] not in ids_accept:
            continue

        liste_emojis = quete.get("emoji", [])
        if isinstance(liste_emojis, str):
            liste_emojis = [liste_emojis]

        if emoji in liste_emojis:
            # ✅ clear de l'état *avant* le return
            user_state.update_one({"_id": user_id}, {"$unset": {"active_interaction": ""}})
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

    # Réponses aux énigmes en MP
    if isinstance(message.channel, discord.DMChannel):
        user = message.author
        user_id = str(user.id)
        contenu = message.content.strip()

        groupes = charger_quetes_groupes()
        user_data = accepted_collection.find_one({"_id": user_id})
        if not user_data:
            return

        quetes_acceptees = user_data.get("quetes", [])
        toutes_quetes = [q for lst in groupes.values() for q in lst]

        for quete in toutes_quetes:
            ids_accept = [q["id"] if isinstance(q, dict) else q for q in quetes_acceptees]
            if quete["id"] not in ids_accept:
                continue

            bonne = normaliser(quete.get("reponse_attendue", ""))
            if normaliser(contenu) == bonne:
                accepted_collection.update_one({"_id": user_id}, {"$pull": {"quetes": {"id": quete["id"]}}})
                completed_collection.update_one(
                    {"_id": user_id},
                    {"$addToSet": {"quetes": {"id": quete["id"], "nom": quete["nom"], "categorie": quete["categorie"]}}},
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
        _scheduler.add_job(lambda: bot.loop.create_task(poster_journalieres()),
                           CronTrigger(hour=10, minute=30))
        _scheduler.add_job(lambda: bot.loop.create_task(poster_hebdo()),
                           CronTrigger(day_of_week='mon', hour=10, minute=31))
        if ANNOUNCE_CHANNEL_ID:
            _scheduler.add_job(lambda: bot.loop.create_task(annoncer_mise_a_jour()),
                               CronTrigger(day_of_week='mon', hour=10, minute=32))
        _scheduler.start()
        print("⏰ Scheduler démarré.")

# ======================
#  RUN
# ======================
if __name__ == "__main__":
    missing = []
    if not DISCORD_TOKEN:      missing.append("DISCORD_TOKEN")
    if not MONGO_URI:          missing.append("MONGO_URI")
    if not QUESTS_CHANNEL_ID:  missing.append("QUESTS_CHANNEL_ID")
    if missing:
        raise RuntimeError(f"Variables manquantes: {', '.join(missing)}")
    bot.run(DISCORD_TOKEN)
