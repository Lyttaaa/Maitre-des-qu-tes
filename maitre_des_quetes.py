import discord
from discord.ext import commands
from discord.ui import View, Button
import json
import os
from pymongo import MongoClient

# Configuration du bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# MongoDB
mongo_uri = os.getenv("MONGO_URI")
client = MongoClient(mongo_uri)
db = client.lumharel_bot
accepted_collection = db.quetes_acceptees

# Channel cible pour poster les quêtes
CHANNEL_ID = 1352143818929078322  # Remplace par l'ID de ton channel #🎯tableau-des-quêtes

# Chargement des quêtes depuis le fichier JSON
def charger_quetes():
    with open("quetes.json", "r", encoding="utf-8") as f:
        return json.load(f)

# Vue personnalisée avec bouton "Accepter"
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

# Commande pour poster les quêtes dans le channel
@bot.command()
@commands.has_permissions(administrator=True)
async def poster_quetes(ctx):
    quetes = charger_quetes()
    channel = bot.get_channel(CHANNEL_ID)

    for quete in quetes:
        embed = discord.Embed(title=quete["titre"], description=quete["resume"], color=0x4CAF50)
        embed.set_footer(text=quete["type"])

        if quete.get("accepter"):
            view = VueAcceptation(quete["titre"], quete["mp"])
            await channel.send(embed=embed, view=view)
        else:
            await channel.send(embed=embed)

# Lancement du bot
bot.run(os.getenv("DISCORD_TOKEN"))
