import discord
from discord.ext import commands
from discord.ui import View, Button
import json
import os
from pymongo import MongoClient
from random import choice
import re
import unicodedata
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

def ids_quetes(liste):
    return [q["id"] if isinstance(q, dict) else q for q in liste]

def normaliser(texte):
    if not isinstance(texte, str):
        return ""
    texte = texte.lower().strip()
    texte = unicodedata.normalize("NFKD", texte)
    texte = ''.join(c for c in texte if not unicodedata.combining(c))
    texte = texte.replace("’", "'")
    texte = re.sub(r"[“”«»]", '"', texte)
    texte = re.sub(r"\s+", " ", texte)
    texte = texte.replace("\u200b", "")
    return texte

# Emojis par catégorie
EMOJI_PAR_CATEGORIE = {
    "Quêtes Journalières": "🕘",
    "Quêtes Interactions": "🕹️",
    "Quêtes Recherches": "🔍",
    "Quêtes Énigmes": "🧩"
}

# Couleurs par catégorie
COULEURS_PAR_CATEGORIE = {
    "Quêtes Journalières": 0x4CAF50,
    "Quêtes Interactions": 0x2196F3,
    "Quêtes Recherches": 0x9C27B0,
    "Quêtes Énigmes": 0xFFC107
}

# Configuration du bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

# MongoDB
mongo_uri = os.getenv("MONGO_URI")
client = MongoClient(mongo_uri)
db = client.lumharel_bot
accepted_collection = db.quetes_acceptees
completed_collection = db.quetes_terminees
utilisateurs = db.utilisateurs
rotation_collection = db.rotation_quetes

CHANNEL_ID = 1352143818929078322

def charger_quetes():
    with open("quetes.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # Injecte la catégorie dans chaque quête
    for categorie, quetes in data.items():
        for quete in quetes:
            quete["categorie"] = categorie  # ← magique ici ✨

    return data

async def envoyer_quete(channel, quete, categorie):
    emoji = EMOJI_PAR_CATEGORIE.get(categorie, "❓")
    couleur_embed = COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
    titre = f"{emoji} {categorie}\n– {quete['id']} {quete['nom']}"

    embed = discord.Embed(
        title=titre,
        description=quete["resume"],
        color=couleur_embed
    )

    type_texte = f"{categorie} – {quete['recompense']} Lumes"
    embed.add_field(name="📌 Type & Récompense", value=type_texte, inline=False)
    embed.set_footer(text="Clique sur le bouton ci-dessous pour accepter la quête.")

    view = VueAcceptation(quete, categorie)
    await channel.send(embed=embed, view=view)

class VueAcceptation(View):
    def __init__(self, quete, categorie):
        super().__init__(timeout=None)
        self.quete = quete
        self.categorie = categorie

    @discord.ui.button(label="Accepter 📥", style=discord.ButtonStyle.green)
    async def accepter(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        quete_id = self.quete["nom"]

        quete_data = accepted_collection.find_one({"_id": user_id})
        if quete_data and quete_id in quete_data.get("quetes", []):
            await interaction.response.send_message("Tu as déjà accepté cette quête ! Consulte tes quêtes en cours ou terminées: !mes_quetes", ephemeral=True)
            return

        deja_faite = completed_collection.find_one({
            "_id": user_id,
            "quetes": {"$elemMatch": {"id": self.quete["id"]}}
        })
        if deja_faite and self.categorie != "Quêtes Journalières":
            try:
                await interaction.user.send(f"📪 Tu as déjà terminé cette quête (**{quete_id}**). Elle ne peut être accomplie qu’une seule fois. Consulte tes quêtes en cours ou terminées: !mes_quetes")
            except discord.Forbidden:
                await interaction.response.send_message("Tu as déjà terminé cette quête, mais je ne peux pas t’envoyer de MP !", ephemeral=True)
            return

        accepted_collection.update_one(
            {"_id": user_id},
            {
                "$addToSet": {
                    "quetes": {
                        "categorie": self.categorie,
                        "id": self.quete["id"],
                        "nom": self.quete["nom"]
                    }
                },
                "$set": {"pseudo": interaction.user.name}
            },
            upsert=True
        )

        # Création de l'embed personnalisé
        if self.categorie == "Quêtes Énigmes":
            embed = discord.Embed(
                title="🧩 Quête Énigmes",
                description=f"**{self.quete['id']} – {self.quete['nom']}**",
                color=COULEURS_PAR_CATEGORIE.get(self.categorie, 0xCCCCCC)
            )
            embed.add_field(name="💬 Énoncé", value=self.quete["enonce"], inline=False)
            embed.add_field(
                name="👉 Objectif",
                value="Trouve la réponse à cette énigme et réponds-moi quand tu as trouvé !",
                inline=False
            )
            embed.set_footer(text=f"🏅 Récompense : {self.quete['recompense']} Lumes")
        else:
            titre_embed = f"{EMOJI_PAR_CATEGORIE.get(self.categorie, '📜')} {self.categorie}"
            nom_quete = f"**{self.quete['id']} – {self.quete['nom']}**"

            embed = discord.Embed(
                title=titre_embed,
                description=nom_quete,
                color=COULEURS_PAR_CATEGORIE.get(self.categorie, 0xCCCCCC)
            )
            embed.add_field(name="💬 Description", value=self.quete["description"], inline=False)
            embed.add_field(name="👉 Objectif", value=self.quete["details_mp"], inline=False)
            embed.set_footer(text=f"🏅 Récompense : {self.quete['recompense']} Lumes")

        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message("Tu as accepté cette quête. Regarde tes MP ! Consulte tes quêtes en cours ou terminées: !mes_quetes", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP !", ephemeral=True)

# ... tes imports + fonctions existantes ...

def get_quete_non_postee(categorie, quetes_possibles):
    collection_rotation = db.rotation_quetes

    print(f"\n🔄 Catégorie demandée : {categorie}")

    # Récupérer les quêtes déjà postées pour la catégorie
    doc = collection_rotation.find_one({"_id": categorie})
    deja_postees = doc["postees"] if doc else []

    print(f"🗃️ Déjà postées : {deja_postees}")
    print(f"📋 Total dans le JSON : {len(quetes_possibles)}")

    restantes = [q for q in quetes_possibles if q["id"] not in deja_postees]
    print(f"🧮 Restantes à tirer : {len(restantes)}")

    # Si tout a été posté, on reset
    if not restantes:
        print("♻️ Toutes les quêtes ont été postées. Réinitialisation de la rotation.")
        restantes = quetes_possibles
        deja_postees = []

    # Choisir la quête
    quete = choice(restantes)
    print(f"🎯 Quête choisie : {quete['id']} - {quete['nom']}")

    # Mettre à jour MongoDB
    collection_rotation.update_one(
        {"_id": categorie},
        {"$set": {"postees": deja_postees + [quete["id"]]}},
        upsert=True
    )

    print(f"✅ Mise à jour MongoDB pour '{categorie}' avec : {quete['id']}")
    return quete
    
@bot.command()
@commands.has_permissions(administrator=True)
async def poster_quetes(ctx):
    quetes_par_type = charger_quetes()
    channel = bot.get_channel(CHANNEL_ID)

    print("📦 Début de la commande !poster_quetes")
    print(f"🔎 Clés trouvées dans le JSON : {list(quetes_par_type.keys())}")

    # 🔄 Supprimer les anciens messages du channel
    async for message in channel.history(limit=100):
        if message.author == bot.user:
            await message.delete()

    # 🕘 Poster les 2 quêtes journalières (pas de rotation pour celles-ci)
    print("📌 Poste les quêtes journalières")
    for quete in quetes_par_type.get("Quêtes Journalières", [])[:2]:
        print(f"🕘 Quête journalière : {quete['nom']}")
        await envoyer_quete(channel, quete, "Quêtes Journalières")

    # 🕹️ Quête interaction avec rotation
    interactions = quetes_par_type.get("Quêtes Interactions", [])
    print(f"🕹️ Quêtes Interactions trouvées : {len(interactions)}")
    if interactions:
        quete_interaction = get_quete_non_postee("Quêtes Interactions", interactions)
        print(f"🎯 Interaction choisie : {quete_interaction['nom']}")
        await envoyer_quete(channel, quete_interaction, "Quêtes Interactions")

    # 🔍 Quête de recherches avec rotation
    recherches = quetes_par_type.get("Quêtes Recherches", [])
    print(f"🔍 Quêtes Recherches trouvées : {len(recherches)}")
    if recherches:
        quete_recherches = get_quete_non_postee("Quêtes Recherches", recherches)
        print(f"📖 Recherche choisie : {quete_recherches['nom']}")
        await envoyer_quete(channel, quete_recherches, "Quêtes Recherches")

    # 🧩 Quête énigme avec rotation
    enigmes = quetes_par_type.get("Quêtes Énigmes", [])
    print(f"🧩 Quêtes Énigmes trouvées : {len(enigmes)}")
    if enigmes:
        quete_enigme = get_quete_non_postee("Quêtes Énigmes", enigmes)
        print(f"🧠 Énigme choisie : {quete_enigme['nom']}")
        await envoyer_quete(channel, quete_enigme, "Quêtes Énigmes")

    print("✅ Fin de la commande !poster_quetes")

@bot.event
async def on_raw_reaction_add(payload):
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
    ids_acceptees = ids_quetes(quetes_acceptees)

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
                {
                    "$addToSet": {
                        "quetes": {
                            "id": quete["id"],
                            "nom": quete["nom"],
                            "categorie": quete["categorie"]
                         }
                     },
                     "$set": {"pseudo": user.name}
                },
                upsert=True
             )

            utilisateurs.update_one(
                {"_id": user_id},
                {
                    "$inc": {"lumes": quete["recompense"]},
                    "$setOnInsert": {
                        "pseudo": user.name,
                        "derniere_offrande": {},
                        "roles_temporaires": {}
                    }
                },
                upsert=True
            )

            try:
                await user.send(f"✨ Tu as terminé la quête **{quete['nom']}** et gagné **{quete['recompense']} Lumes** !")
            except discord.Forbidden:
                await bot.get_channel(payload.channel_id).send(f"✅ {user.mention} a terminé la quête **{quete['nom']}** ! (MP non reçu)")
            return
            
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        user = message.author
        user_id = str(message.author.id)
        contenu = message.content.strip()
        quetes = charger_quetes()
        user_data = accepted_collection.find_one({"_id": user_id})
        if not user_data:
            return

        quetes_acceptees = user_data.get("quetes", [])
        ids_acceptees = ids_quetes(quetes_acceptees)

        toutes_quetes = [q for lst in quetes.values() for q in lst]

        for quete in toutes_quetes:
            if quete["id"] not in [q["id"] if isinstance(q, dict) else q for q in quetes_acceptees]:
                continue

            bonne_reponse = normaliser(quete.get("reponse_attendue", ""))
            if normaliser(contenu) == bonne_reponse:
                accepted_collection.update_one({"_id": user_id}, {"$pull": {"quetes": {"id": quete["id"]}}})
                completed_collection.update_one(
                    {"_id": user_id},
                    {
                        "$addToSet": {
                            "quetes": {
                                "id": quete["id"],
                                "nom": quete["nom"],
                                "categorie": quete["categorie"]
                    }
                },
                "$set": {"pseudo": user.name}
            },
            upsert=True
        )
                utilisateurs.update_one(
                    {"_id": user_id},
                    {
                        "$inc": {"lumes": quete["recompense"]},
                        "$setOnInsert": {
                            "pseudo": message.author.name,
                            "derniere_offrande": {},
                            "roles_temporaires": {}
                        }
                    },
                    upsert=True
                )

                await message.channel.send(
                    f"✅ Parfait ! Tu as complété la quête **{quete['nom']}** et gagné **{quete['recompense']} Lumes** !"
                )
                return

    await bot.process_commands(message)
    
@bot.command()
async def mes_quetes(ctx):
    user_id = str(ctx.author.id)
    toutes_quetes = [q for lst in charger_quetes().values() for q in lst]

    user_accept = accepted_collection.find_one({"_id": user_id}) or {}
    user_done = completed_collection.find_one({"_id": user_id}) or {}

    quetes_accept = user_accept.get("quetes", [])
    quetes_done = user_done.get("quetes", [])

    # Convertir en sets d'IDs pour comparaison rapide
    ids_accept = set(q["id"] if isinstance(q, dict) else q for q in quetes_accept)
    ids_done = set(q.get("id") if isinstance(q, dict) else q for q in quetes_done)

    # Tri par catégorie
    categories = {
        "Quêtes Journalières": {"emoji": "🕘", "encours": [], "terminees": []},
        "Quêtes Interactions": {"emoji": "🕹️", "encours": [], "terminees": []},
        "Quêtes Recherches": {"emoji": "🔍", "encours": [], "terminees": []},
        "Quêtes Énigmes": {"emoji": "🧩", "encours": [], "terminees": []}
    }

    for quete in toutes_quetes:
        cat = quete.get("categorie", None)
        if not cat or cat not in categories:
            continue

        ligne = f"• {quete['id']} – {quete['nom']}"
        if quete["id"] in ids_done:
            categories[cat]["terminees"].append(ligne)
        elif quete["id"] in ids_accept:
            categories[cat]["encours"].append(ligne)

    embed = discord.Embed(
        title=f"📘 **Quêtes de {ctx.author.display_name}**",
        color=0xA86E2A  # Marron
    )

    # 📜 Quêtes en cours
    description = "📜 **Quêtes en cours**\n"
    for cat, data in categories.items():
        description += f"{data['emoji']} __{cat.replace('Quêtes ', '')} :__\n"
        if data["encours"]:
            description += "\n".join(data["encours"]) + "\n"
        else:
            description += "*Aucune*\n"

    # 🏅 Quêtes terminées
    description += "\n🏅 **Quêtes terminées**\n"
    for cat, data in categories.items():
        description += f"{data['emoji']} __{cat.replace('Quêtes ', '')} :__\n"
        if data["terminees"]:
            description += "\n".join(data["terminees"]) + "\n"
        else:
            description += "*Aucune*\n"

    embed.description = description
    await ctx.send(embed=embed)

# Commande !bourse
@bot.command()
async def bourse(ctx):
    user_id = str(ctx.author.id)
    user_data = database.find_one({"_id": user_id})
    if not user_data:
        database.insert_one({"_id": user_id, "pseudo": ctx.author.name, "lumes": 0, "derniere_offrande": {}, "roles_temporaires": {}})
        user_data = database.find_one({"_id": user_id})

    await ctx.send(f"💰 {ctx.author.mention}, tu possèdes actuellement **{user_data['lumes']} Lumes**.")
    
@bot.event
async def on_ready():
    print(f"✅ Le bot est prêt : {bot.user}")
    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Europe/Paris"))

    # Publier les quêtes tous les lundis à 10h30
    scheduler.add_job(poster_et_annonce_quetes, CronTrigger(day_of_week='mon', hour=10, minute=30))
    scheduler.start()
    
async def poster_quetes_automatique():
    channel = bot.get_channel(CHANNEL_ID)
    # simulateur de contexte pour réutiliser ta fonction existante
    class DummyCtx:
        def __init__(self, channel):
            self.channel = channel
    await poster_quetes(DummyCtx(channel))

async def poster_et_annonce_quetes():
    channel_quetes = bot.get_channel(CHANNEL_ID)  # ton channel #🎯tableau-des-quetes
    channel_annonce = bot.get_channel(ID_DU_CHANNEL_ANNONCE)  # remplace par l’ID du #📣annonce

    ctx_faux = type("Ctx", (), {"send": lambda self, m: None})()  # simule un ctx bidon pour `poster_quetes`
    await poster_quetes(ctx_faux)

    # Message d’annonce RP
    message = (
        "👋 Oye Oye! @everyone 🥾 de Lumharel, les Quêtes **Journalières** & **Hebdomadaires** "
        "ont été mises à jour sur le <#1352143818929078322> !!\n"
        "En vous souhaitant une excellente semaine chers.ères ami.es, puissent les Souffles vous être favorables 🌬️ !"
    )
    await channel_annonce.send(message)

# 🚀 Lancement du bot
bot.run(os.getenv("DISCORD_TOKEN"))
