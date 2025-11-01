import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

CHEMIN_QUETES = os.getenv("CHEMIN_QUETES", "quetes.json")

COULEURS_PAR_CATEGORIE = {
    "Quêtes Journalières": 0xFCE205,
    "Quêtes Interactions": 0x58C4DD,
    "Quêtes Simples": 0xC47AFF,
    "Quêtes de Recherche": 0x4EFFA2,
    "Quêtes Énigmes": 0xFF7A7A,
}

EMOJI_PAR_CATEGORIE = {
    "Quêtes Journalières": "🌞",
    "Quêtes Interactions": "💬",
    "Quêtes Simples": "🎯",
    "Quêtes de Recherche": "🔍",
    "Quêtes Énigmes": "🧩",
}


# =============================
#   LECTURE DU FICHIER QUETES
# =============================
def lire_quetes():
    with open(CHEMIN_QUETES, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================
#   EMBEDS PUBLIC / DM
# =============================
def build_public_embed(quete, categorie):
    titre = f"{EMOJI_PAR_CATEGORIE.get(categorie, '📜')} {categorie}"
    embed = discord.Embed(
        title=titre,
        description=f"**{quete['id']} – {quete['nom']}**",
        color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
    )
    resume = quete.get("resume") or quete.get("description") or "—"
    embed.add_field(name="💬 Résumé", value=resume, inline=False)

    if quete.get("type") == "multi_step":
        embed.add_field(name="🔁 Progression", value="Quête à plusieurs étapes", inline=False)

    embed.set_footer(text=f"🏅 Récompense : {quete.get('recompense', 0)} Lumes")
    return embed


def build_dm_embed(quete, categorie):
    titre = f"{EMOJI_PAR_CATEGORIE.get(categorie, '📜')} {categorie}"
    embed = discord.Embed(
        title=titre,
        description=f"**{quete['id']} – {quete['nom']}**",
        color=COULEURS_PAR_CATEGORIE.get(categorie, 0xCCCCCC)
    )

    if categorie == "Quêtes Énigmes":
        if quete.get("image_url"):
            embed.add_field(name="💬 Énigme", value="Observe bien ce symbole...", inline=False)
            embed.set_image(url=quete["image_url"])
        else:
            embed.add_field(name="💬 Énigme", value=quete.get("enonce", "—"), inline=False)
        embed.add_field(name="👉 Objectif", value="Trouve la réponse et réponds-moi ici.", inline=False)

    elif categorie == "Quêtes Interactions" and quete.get("type") == "multi_step":
        steps = quete.get("steps", [])
        step1 = steps[0] if steps else {}
        embed.add_field(name="💬 Description", value=quete.get("description", "—"), inline=False)

        lignes = []
        if step1.get("channel"):
            lignes.append(f"• **Lieu** : `#{step1['channel']}`")
        if step1.get("mots_cles"):
            lignes.append("• **Action** : écris un message contenant : " + ", ".join(f"`{m}`" for m in step1["mots_cles"]))
        if step1.get("emoji"):
            lignes.append(f"• **Validation** : réagis avec {step1['emoji']} sur le message du PNJ")

        embed.add_field(name="🚶 Étape 1", value="\n".join(lignes) or "Suis les indications du PNJ.", inline=False)
        embed.add_field(name="🔁 Progression", value="Les étapes suivantes te seront révélées au fur et à mesure.", inline=False)

    else:
        embed.add_field(name="💬 Description", value=quete.get("description", "—"), inline=False)
        if quete.get("details_mp"):
            embed.add_field(name="👉 Objectif", value=quete["details_mp"], inline=False)

    embed.set_footer(text=f"🏅 Récompense : {quete.get('recompense', 0)} Lumes")
    return embed


# =============================
#   VUE ACCEPTATION
# =============================
class VueAcceptation(discord.ui.View):
    def __init__(self, quete, categorie):
        super().__init__(timeout=None)
        self.quete = quete
        self.categorie = categorie

    @discord.ui.button(label="📜 Accepter la quête", style=discord.ButtonStyle.green)
    async def accepter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        embed_dm = build_dm_embed(self.quete, self.categorie)
        try:
            await interaction.user.send(embed=embed_dm)
            await interaction.followup.send("📬 Les détails de la quête t'ont été envoyés en MP !", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("⚠️ Impossible de t’envoyer un message privé.", ephemeral=True)


# =============================
#   ENVOI PUBLIC
# =============================
async def envoyer_quete(channel, quete, categorie):
    embed = build_public_embed(quete, categorie)
    await channel.send(embed=embed, view=VueAcceptation(quete, categorie))


# =============================
#   COMMANDES MANUELLES
# =============================
@bot.command(name="show_quete")
async def show_quete(ctx, quest_id: str):
    data = lire_quetes()
    for cat, quetes in data.items():
        for q in quetes:
            if q["id"].upper() == quest_id.upper():
                await envoyer_quete(ctx.channel, q, cat)
                return
    await ctx.send("❌ Quête introuvable.")


@bot.command(name="poster_quetes")
@commands.has_permissions(administrator=True)
async def poster_quetes(ctx):
    data = lire_quetes()
    for categorie, quetes in data.items():
        for quete in quetes:
            await envoyer_quete(ctx.channel, quete, categorie)
    await ctx.send("✅ Toutes les quêtes ont été postées (version publique).")


# =============================
#   ROTATION AUTO (chaque jour)
# =============================
@tasks.loop(hours=24)
async def poster_quetes_auto():
    channel_id = int(os.getenv("CHANNEL_QUETES_ID", "0"))
    if channel_id == 0:
        print("⚠️ Pas de channel défini pour poster automatiquement les quêtes.")
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        print("⚠️ Channel introuvable pour les quêtes.")
        return

    data = lire_quetes()
    for categorie, quetes in data.items():
        for quete in quetes:
            await envoyer_quete(channel, quete, categorie)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Quêtes postées automatiquement.")


@bot.event
async def on_ready():
    print(f"✅ Maître des Quêtes prêt : {bot.user}")
    if not poster_quetes_auto.is_running():
        poster_quetes_auto.start()


if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN_MAITRE"))
