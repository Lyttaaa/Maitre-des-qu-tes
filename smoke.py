# smoke.py
import os, discord, logging

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"✅ SMOKE: connecté en tant que {client.user} (latence {client.latency*1000:.0f} ms)")

token = os.getenv("DISCORD_TOKEN")
if not token:
    print("❌ DISCORD_TOKEN manquant.")
else:
    client.run(token)
