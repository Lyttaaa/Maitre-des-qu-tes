import discord
from discord.ext import commands
from discord.ui import View, Button
import json
import os
from pymongo import MongoClient
from random import choice
import re
import unicodedata

def normaliser(texte):
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
    "Quêtes Simples": "📦",
    "Quêtes de Recherche": "📜",
    "Quêtes Énigmes": "🧩"
}

# Couleurs par catégorie
COULEURS_PAR_CATEGORIE = {
    "Quêtes Journalières": 0x4CAF50,
    "Quêtes Simples": 0x2196F3,
    "Quêtes de Recherche": 0x9C27B0,
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

CHANNEL_ID = 1352143818929078322

def charger_quetes():
    with open("quetes.json", "r", encoding="utf-8") as f:
        return json.load(f)

async def envoyer_quete(channel, quete, categorie):
    emoji = EMOJI_PAR_CATEGORIE.get(categorie, "❓")
    couleur_embed = COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
    titre = f"{emoji} {categorie} : {quete['nom']}"

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
            await interaction.response.send_message("Tu as déjà accepté cette quête !", ephemeral=True)
            return

        deja_faite = completed_collection.find_one({"_id": user_id, "quetes": quete_id})
        if deja_faite and self.categorie != "Quêtes Journalières":
            try:
                await interaction.user.send(f"📪 Tu as déjà terminé cette quête (**{quete_id}**). Elle ne peut être accomplie qu’une seule fois.")
            except discord.Forbidden:
                await interaction.response.send_message("Tu as déjà terminé cette quête, mais je ne peux pas t’envoyer de MP !", ephemeral=True)
            return

        accepted_collection.update_one(
            {"_id": user_id},
            {"$addToSet": {"quetes": quete_id}},
            upsert=True
        )

        # Création de l'embed personnalisé
        embed = discord.Embed(
            title=f"{EMOJI_PAR_CATEGORIE.get(self.categorie, '📜')} {self.quete['nom']}",
            color=COULEURS_PAR_CATEGORIE.get(self.categorie, 0xCCCCCC)
        )
        embed.add_field(name="💬 Description", value=self.quete["resume"], inline=False)
        embed.add_field(name="👉 Objectif", value=self.quete["details_mp"], inline=False)
        embed.set_footer(text=f"🏅 Récompense : {self.quete['recompense']} Lumes")

        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message("Tu as accepté cette quête. Regarde tes MP !", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Je n'arrive pas à t'envoyer de MP !", ephemeral=True)

# Le reste du code reste inchangé et fonctionnera avec ce nouveau système
# Poster les quêtes, validation par réaction, on_message, commandes etc.

# Pense à adapter les autres parties du bot si tu veux intégrer les Quêtes Énigmes dans `poster_quetes()` par exemple
# Et à bien ajouter ces quêtes dans ton fichier quetes.json pour les tester

# 🚀 Lancement du bot
bot.run(os.getenv("DISCORD_TOKEN"))
