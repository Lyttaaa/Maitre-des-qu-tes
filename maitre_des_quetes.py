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
completed_collection = db.quetes_terminees  # ✅ Ajout
utilisateurs = db.utilisateurs

# ID du salon de quêtes
CHANNEL_ID = 1352143818929078322

# Chargement des quêtes
def charger_quetes():
    with open("quetes.json", "r", encoding="utf-8") as f:
        return json.load(f)

# Envoi d'une quête dans un embed avec bouton
async def envoyer_quete(channel, quete, categorie):
    emoji = ""
    if isinstance(quete.get("emoji"), list):
        emoji = ''.join(quete["emoji"])
    elif isinstance(quete.get("emoji"), str):
        emoji = quete["emoji"]

    embed = discord.Embed(
        title=f"{emoji + ' ' if emoji else ''}Quête — {quete['nom']}",
        description=quete["resume"],
        color=0x4CAF50
    )
    embed.set_footer(text=categorie)

    view = VueAcceptation(quete["nom"], quete["details_mp"])
    await channel.send(embed=embed, view=view)

# Vue avec bouton Accepter
class VueAcceptation(View):
    def __init__(self, quete_id, mp_message):
        super().__init__(timeout=None)
        self.quete_id = quete_id
        self.mp_message = mp_message

    @discord.ui.button(label="Accepter 📥", style=discord.ButtonStyle.green)
    async def accepter(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        quete = accepted_collection.find_one({"_id": user_id})

        if quete and self.quete_id in quete.get("quetes", []):
            await interaction.response.send_message("Tu as déjà accepté cette quête !", ephemeral=True)
            return

        accepted_collection.update_one(
            {"_id": user_id},
            {"$addToSet": {"quetes": self.quete_id}},
            upsert=True
        )

        try:
            await interaction.user.send(f"📜 **Détails de la quête** :\n{self.mp_message}")
            await interaction.response.send_message("Tu as accepté cette quête. Regarde tes MP !", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP !", ephemeral=True)

# 📌 Poster les quêtes (commande admin)
@bot.command()
@commands.has_permissions(administrator=True)
async def poster_quetes(ctx):
    quetes_par_type = charger_quetes()
    channel = bot.get_channel(CHANNEL_ID)

    for quete in quetes_par_type.get("Quêtes Journalières", []):
        await envoyer_quete(channel, quete, "Quêtes Journalières")

    simples = quetes_par_type.get("Quêtes Simples", [])
    if simples:
        await envoyer_quete(channel, choice(simples), "Quêtes Simples")

    recherches = quetes_par_type.get("Quêtes de Recherche", [])
    if recherches:
        await envoyer_quete(channel, choice(recherches), "Quêtes de Recherche")

# ✅ Validation des quêtes "reaction"
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
    toutes_quetes = []
    for lst in quetes.values():
        toutes_quetes.extend(lst)

    for quete in toutes_quetes:
        if quete["type"] != "reaction" or quete["nom"] not in quetes_acceptees:
            continue

        liste_emojis = quete.get("emoji", [])
        if isinstance(liste_emojis, str):
            liste_emojis = [liste_emojis]

        if emoji in liste_emojis:
            accepted_collection.update_one({"_id": user_id}, {"$pull": {"quetes": quete["nom"]}})
            completed_collection.update_one(  # ✅ Ajout
                {"_id": user_id},
                {"$addToSet": {"quetes": quete["nom"]}},
                upsert=True
            )

            user = payload.member
            profil = utilisateurs.find_one({"_id": user_id})
            if not profil:
                utilisateurs.insert_one({
                    "_id": user_id,
                    "pseudo": user.name,
                    "lumes": quete["recompense"],
                    "derniere_offrande": {},
                    "roles_temporaires": {}
                })
            else:
                utilisateurs.update_one(
                    {"_id": user_id},
                    {"$inc": {"lumes": quete["recompense"]}}
                )

            try:
                await user.send(
                    f"✅ Tu as terminé la quête **{quete['nom']}** et gagné **{quete['recompense']} Lumes** !"
                )
            except discord.Forbidden:
                channel = bot.get_channel(payload.channel_id)
                await channel.send(
                    f"✅ {user.mention} a terminé la quête **{quete['nom']}** et gagné **{quete['recompense']} Lumes** ! (MP non reçu)"
                )
            return

# 📬 Validation des quêtes texte (MP)
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
        toutes_quetes = []
        for lst in quetes.values():
            toutes_quetes.extend(lst)

        for quete in toutes_quetes:
            if quete["type"] != "texte" or quete["nom"] not in quetes_acceptees:
                continue
            bonne_reponse = quete.get("reponse_attendue", "").lower().strip()

            if contenu.lower() == bonne_reponse:
                accepted_collection.update_one(
                    {"_id": user_id},
                    {"$pull": {"quetes": quete["nom"]}}
                )
                completed_collection.update_one(  # ✅ Ajout
                    {"_id": user_id},
                    {"$addToSet": {"quetes": quete["nom"]}},
                    upsert=True
                )

                profil = utilisateurs.find_one({"_id": user_id})
                if not profil:
                    utilisateurs.insert_one({
                        "_id": user_id,
                        "pseudo": message.author.name,
                        "lumes": quete["recompense"],
                        "derniere_offrande": {},
                        "roles_temporaires": {}
                    })
                else:
                    utilisateurs.update_one(
                        {"_id": user_id},
                        {"$inc": {"lumes": quete["recompense"]}}
                    )

                await message.channel.send(
                    f"✅ Ta réponse est correcte ! Tu as complété la quête **{quete['nom']}** et gagné **{quete['recompense']} Lumes** !"
                )
                return

    await bot.process_commands(message)

# 📜 Commande : Voir ses quêtes en cours
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

# 🏅 Commande : Voir ses quêtes terminées
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

# 🚀 Lancement
bot.run(os.getenv("DISCORD_TOKEN"))
