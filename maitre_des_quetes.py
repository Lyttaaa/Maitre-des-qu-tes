@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return

    user_id = str(member.id)
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    emoji = str(payload.emoji)

    # Cherche les quêtes acceptées par cet utilisateur
    accepted = accepted_collection.find_one({"_id": user_id})
    if not accepted or not accepted.get("quetes"):
        return

    # Charge toutes les quêtes
    toutes_les_quetes = charger_quetes()

    # Vérifie toutes les quêtes acceptées de type "reaction"
    for categorie in toutes_les_quetes.values():
        for quete in categorie:
            if quete["nom"] in accepted["quetes"] and quete["type"] == "reaction":
                # Vérifie l’emoji
                liste_emojis = quete["emoji"]
                if isinstance(liste_emojis, str):
                    liste_emojis = [liste_emojis]
                if emoji not in liste_emojis:
                    continue

                # Si la quête cible un PNJ et un channel spécifiques
                if "pnj" in quete and "channel" in quete:
                    if channel.name != quete["channel"]:
                        continue

                    if not any(
                        quete["pnj"].lower() in (embed.description or "").lower()
                        for embed in message.embeds if embed
                    ):
                        continue

                # ✅ Quête validée
                accepted_collection.update_one(
                    {"_id": user_id},
                    {"$pull": {"quetes": quete["nom"]}}
                )

                user_data = db.utilisateurs.find_one({"_id": user_id})
                if user_data:
                    db.utilisateurs.update_one(
                        {"_id": user_id},
                        {"$inc": {"lumes": quete["recompense"]}}
                    )

                try:
                    await member.send(f"✅ Tu as complété la quête **{quete['nom']}** !\n🎉 Tu gagnes **{quete['recompense']} Lumes**.")
                except discord.Forbidden:
                    pass

                print(f"✅ Validation de la quête {quete['nom']} pour {member.name}")
