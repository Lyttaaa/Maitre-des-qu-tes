import discord
from discord.ext import commands
from discord.ui import View, Button
import json
import os
from pymongo import MongoClient
from random import choice

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

CHANNEL_ID = 1352143818929078322

def charger_quetes():
    with open("quetes.json", "r", encoding="utf-8") as f:
        return json.load(f)

async def envoyer_quete(channel, quete, categorie):
    # Choisir une couleur selon la catégorie
    couleurs = {
        "Quêtes Journalières": 0x4CAF50,   # Vert
        "Quêtes Simples": 0x2196F3,        # Bleu
        "Quêtes de Recherche": 0x9C27B0    # Violet
    }
    couleur_embed = couleurs.get(categorie, 0xCCCCCC)

    # Titre stylisé
    titre = f"Quête – {quete['nom']}"

    # Embed principal
    embed = discord.Embed(
        title=titre,
        description=quete["resume"],
        color=couleur_embed
    )

    # Champ type + récompense
    type_texte = f"{categorie} – {quete['recompense']} Lumes"
    embed.add_field(name="📌 Type & Récompense", value=type_texte, inline=False)

    embed.set_footer(text="Clique sur le bouton ci-dessous pour accepter la quête.")

    view = VueAcceptation(quete["nom"], quete["details_mp"])
    await channel.send(embed=embed, view=view)

class VueAcceptation(View):
    def __init__(self, quete_id, mp_message):
        super().__init__(timeout=None)
        self.quete_id = quete_id
        self.mp_message = mp_message

    @discord.ui.button(label="Accepter 📥", style=discord.ButtonStyle.green)
    async def accepter(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        quete_id = self.quete_id

        # Vérifie si déjà acceptée
        quete = accepted_collection.find_one({"_id": user_id})
        if quete and quete_id in quete.get("quetes", []):
            await interaction.response.send_message("Tu as déjà accepté cette quête !", ephemeral=True)
            return

        # Vérifie si déjà terminée (sauf pour les journalières)
        deja_faite = completed_collection.find_one({"_id": user_id, "quetes": quete_id})
        if deja_faite:
            quetes_data = charger_quetes()
            est_journaliere = any(
                quete_id == q["nom"] for q in quetes_data.get("Quêtes Journalières", [])
            )

            if not est_journaliere:
                await interaction.response.send_message(
                    "Tu as déjà terminé cette quête. Elle ne peut être accomplie qu’une seule fois.",
                    ephemeral=True
                )
                return

        # Ajout en base
        accepted_collection.update_one(
            {"_id": user_id},
            {"$addToSet": {"quetes": quete_id}},
            upsert=True
        )

        try:
            await interaction.user.send(f"📜 **Détails de la quête** :\n{self.mp_message}")
            await interaction.response.send_message("Tu as accepté cette quête. Regarde tes MP !", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP !", ephemeral=True)

                    break

        # Ajoute la quête comme acceptée
        accepted_collection.update_one(
            {"_id": user_id},
            {"$addToSet": {"quetes": quete_id}},
            upsert=True
        )

        try:
            await interaction.user.send(f"📜 **Détails de la quête** :\n{self.mp_message}")
            await interaction.response.send_message("Tu as accepté cette quête. Regarde tes MP !", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP !", ephemeral=True)

        try:
            await interaction.user.send(f"📜 **Nouvelle quête reçue !**\n{self.mp_message}")
            await interaction.response.send_message("Ta quête a été ajoutée ! Regarde tes MP. 📨", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP !", ephemeral=True)

@bot.command()
@commands.has_permissions(administrator=True)
async def poster_quetes(ctx):
    quetes_par_type = charger_quetes()
    channel = bot.get_channel(CHANNEL_ID)

    # 🔄 Supprimer les anciens messages du channel
    async for message in channel.history(limit=100):
        if message.author == bot.user:
            await message.delete()

    for quete in quetes_par_type.get("Quêtes Journalières", []):
        await envoyer_quete(channel, quete, "Quêtes Journalières")

    simples = quetes_par_type.get("Quêtes Simples", [])
    if simples:
        await envoyer_quete(channel, choice(simples), "Quêtes Simples")

    recherches = quetes_par_type.get("Quêtes de Recherche", [])
    if recherches:
        await envoyer_quete(channel, choice(recherches), "Quêtes de Recherche")

@bot.event
async def on_raw_reaction_add(payload):
    if payload.member is None or payload.member.bot:
        return

    user_id = str(payload.user_id)
    emoji = str(payload.emoji)
    quetes = charger_quetes()
    user_data = accepted_collection.find_one({"_id": user_id})
    if not user_data:
        return

    quetes_acceptees = user_data.get("quetes", [])
    toutes_quetes = [q for lst in quetes.values() for q in lst]

    for quete in toutes_quetes:
        if quete.get("type") != "reaction" or quete["nom"] not in quetes_acceptees:
            continue

        liste_emojis = quete.get("emoji", [])
        if isinstance(liste_emojis, str):
            liste_emojis = [liste_emojis]

        if emoji in liste_emojis:
            accepted_collection.update_one({"_id": user_id}, {"$pull": {"quetes": quete["nom"]}})
            completed_collection.update_one(
                {"_id": user_id}, {"$addToSet": {"quetes": quete["nom"]}}, upsert=True
            )

            user = payload.member
            utilisateurs.update_one(
                {"_id": user_id},
                {"$inc": {"lumes": quete["recompense"]}, "$setOnInsert": {"pseudo": user.name, "derniere_offrande": {}, "roles_temporaires": {}}},
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
        user_id = str(message.author.id)
        contenu = message.content.strip()
        quetes = charger_quetes()
        user_data = accepted_collection.find_one({"_id": user_id})
        if not user_data:
            return

        quetes_acceptees = user_data.get("quetes", [])
        toutes_quetes = [q for lst in quetes.values() for q in lst]

        for quete in toutes_quetes:
            if quete.get("type") != "texte" or quete["nom"] not in quetes_acceptees:
                continue

            bonne_reponse = quete.get("reponse_attendue", "").lower().strip()
            if contenu.lower() == bonne_reponse:
                accepted_collection.update_one({"_id": user_id}, {"$pull": {"quetes": quete["nom"]}})
                completed_collection.update_one(
                    {"_id": user_id}, {"$addToSet": {"quetes": quete["nom"]}}, upsert=True
                )

                utilisateurs.update_one(
                    {"_id": user_id},
                    {"$inc": {"lumes": quete["recompense"]}, "$setOnInsert": {"pseudo": message.author.name, "derniere_offrande": {}, "roles_temporaires": {}}},
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
    user_data = accepted_collection.find_one({"_id": user_id})

    if not user_data or not user_data.get("quetes"):
        await ctx.send(f"📭 {ctx.author.mention}, tu n'as actuellement aucune quête en cours.")
        return

    quetes = user_data["quetes"]
    liste = "\n".join(f"• {q}" for q in quetes)
    await ctx.send(f"📜 **Quêtes en cours pour {ctx.author.mention}** :\n{liste}")

@bot.command()
async def quetes_terminees(ctx):
    user_id = str(ctx.author.id)
    user_data = completed_collection.find_one({"_id": user_id})

    if not user_data or not user_data.get("quetes"):
        await ctx.send(f"🔍 {ctx.author.mention}, tu n'as encore terminé aucune quête.")
        return

    quetes = user_data["quetes"]
    liste = "\n".join(f"• {q}" for q in quetes)
    await ctx.send(f"🏅 **Quêtes terminées par {ctx.author.mention}** :\n{liste}")

bot.run(os.getenv("DISCORD_TOKEN"))
