# ✅ Validation des quêtes via réaction
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
        if quete["type"] != "reaction":
            continue
        if quete["nom"] not in quetes_acceptees:
            continue

        liste_emojis = quete["emoji"]
        if isinstance(liste_emojis, str):
            liste_emojis = [liste_emojis]

        if emoji in liste_emojis:
            # Retire la quête de la liste
            accepted_collection.update_one(
                {"_id": user_id},
                {"$pull": {"quetes": quete["nom"]}}
            )

            # Ajout de Lumes
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

            # ✅ Envoi du message de succès en MP
            try:
                await user.send(
                    f"✅ Tu as terminé la quête **{quete['nom']}** et gagné **{quete['recompense']} Lumes** !"
                )
            except discord.Forbidden:
                # Si MP impossible, fallback dans le channel
                channel = bot.get_channel(payload.channel_id)
                await channel.send(
                    f"✅ {user.mention} a terminé la quête **{quete['nom']}** et gagné **{quete['recompense']} Lumes** ! (MP non reçu)"
                )
            return
