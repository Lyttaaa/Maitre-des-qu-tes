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
    for categorie, quetes in data.items():
        for quete in quetes:
            quete["categorie"] = categorie
    return data

async def purger_messages_categorie(
