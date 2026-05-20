import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import asyncio
import random
import string
from datetime import datetime, timedelta
import logging

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("slot_bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ─── Data file helpers ───────────────────────────────────────────────────────
DATA_FILE = "slot_data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "config": {},
            "slots": {},
            "blacklist": [],
            "warnings": {},
            "history": [],
            "codes": {},
            "tickets": {}
        }
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

# ─── Bot setup ───────────────────────────────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="s!", intents=intents, help_command=None)
tree = bot.tree
# ─── OWNER LOCK — Username based ────────────────────────────────────────────
OWNER_USERNAME = "YOUR_USERNAME"

@bot.check
async def owner_only_global(ctx):
    if ctx.author.name != wannabeminato
        await ctx.send("❌ Yeh bot sirf owner ke liye hai.")
        return False
    return True

# ─── Helpers ─────────────────────────────────────────────────────────────────
def is_staff(member: discord.Member, data: dict) -> bool:
    staff_role_id = data.get("config", {}).get("staff_role_id")
    if member.guild_permissions.administrator:
        return True
    if staff_role_id:
        return any(r.id == int(staff_role_id) for r in member.roles)
    return False

def parse_duration(duration_str: str) -> timedelta | None:
    """Parse strings like '7d', '1m', '30d', '2m' into timedelta."""
    duration_str = duration_str.strip().lower()
    try:
        if duration_str.endswith("m"):
            months = int(duration_str[:-1])
            return timedelta(days=months * 30)
        elif duration_str.endswith("d"):
            days = int(duration_str[:-1])
            return timedelta(days=days)
        elif duration_str.endswith("h"):
            hours = int(duration_str[:-1])
            return timedelta(hours=hours)
    except ValueError:
        return None
    return None

def slot_embed(slot: dict, guild: discord.Guild, color=discord.Color.blurple()) -> discord.Embed:
    user = guild.get_member(int(slot["user_id"]))
    username = str(user) if user else f"Unknown ({slot['user_id']})"
    expires = datetime.fromisoformat(slot["expires_at"])
    remaining = expires - datetime.utcnow()
    days_rem = remaining.days
    hours_rem = remaining.seconds // 3600
    pings_left = slot["pings_allowed"] - slot["pings_used"]
    ping_bar = "▓" * min(pings_left, 20) + "░" * max(0, 20 - pings_left)

    embed = discord.Embed(title="🎰 Slot Information", color=color, timestamp=datetime.utcnow())
    embed.add_field(name="👤 Owner", value=username, inline=True)
    embed.add_field(name="📂 Category", value=slot.get("category", "General"), inline=True)
    embed.add_field(name="📊 Status", value=slot.get("status", "active").capitalize(), inline=True)
    embed.add_field(name="⏳ Expires", value=f"<t:{int(expires.timestamp())}:R>", inline=True)
    embed.add_field(name="📅 Time Left", value=f"{days_rem}d {hours_rem}h", inline=True)
    embed.add_field(name="🔔 Pings Left", value=f"{pings_left}/{slot['pings_allowed']}\n`{ping_bar}`", inline=False)
    if slot.get("on_hold"):
        embed.add_field(name="⏸️ Hold Reason", value=slot.get("hold_reason", "Under review"), inline=False)
    embed.set_footer(text=f"Channel ID: {slot['channel_id']}")
    return embed

async def log_action(bot, data, action: str, actor: discord.Member, target=None, details=""):
    log_channel_id = data.get("config", {}).get("log_channel_id")
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "actor": str(actor),
        "actor_id": str(actor.id),
        "target": str(target) if target else None,
        "details": details
    }
    data["history"].append(entry)
    save_data(data)
    logger.info(f"[{action}] by {actor} | {details}")
    if log_channel_id:
        channel = bot.get_channel(int(log_channel_id))
        if channel:
            embed = discord.Embed(
                title=f"📋 {action}",
                color=discord.Color.orange(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Actor", value=str(actor), inline=True)
            if target:
                embed.add_field(name="Target", value=str(target), inline=True)
            if details:
                embed.add_field(name="Details", value=details, inline=False)
            await channel.send(embed=embed)

# ─── Auto-expiry task ────────────────────────────────────────────────────────
@tasks.loop(minutes=60)
async def auto_expire_slots():
    data = load_data()
    now = datetime.utcnow()
    expired = []
    for ch_id, slot in list(data["slots"].items()):
        if slot.get("status") == "active" and not slot.get("on_hold"):
            expires = datetime.fromisoformat(slot["expires_at"])
            if now >= expires:
                expired.append(ch_id)
    for ch_id in expired:
        slot = data["slots"][ch_id]
        guild = bot.get_guild(int(slot["guild_id"]))
        if not guild:
            continue
        channel = guild.get_channel(int(ch_id))
        member = guild.get_member(int(slot["user_id"]))
        slot_role_id = data.get("config", {}).get("slot_role_id")
        if channel:
            await channel.set_permissions(guild.default_role, send_messages=False, view_channel=False)
            if member and slot_role_id:
                role = guild.get_role(int(slot_role_id))
                if role and role in member.roles:
                    await member.remove_roles(role)
            embed = discord.Embed(
                title="⏰ Slot Expired",
                description="This slot has expired and has been locked.",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            await channel.send(embed=embed)
            if member:
                try:
                    await member.send(embed=discord.Embed(
                        title="⏰ Your Slot Has Expired",
                        description=f"Your slot in **{guild.name}** (`{channel.name}`) has expired.",
                        color=discord.Color.red()
                    ))
                except Exception:
                    pass
        slot["status"] = "expired"
        logger.info(f"Auto-expired slot {ch_id}")
    save_data(data)

# ─── Ping reset task ─────────────────────────────────────────────────────────
@tasks.loop(hours=24)
async def reset_pings():
    data = load_data()
    for slot in data["slots"].values():
        if slot.get("status") == "active":
            slot["pings_used"] = 0   # Poori daily limit wapas milti hai (pings_allowed)
    save_data(data)
    logger.info("Daily ping reset complete.")

# ─── on_ready ────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    auto_expire_slots.start()
    reset_pings.start()
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="🎰 Slots | s!help"
    ))
    logger.info(f"Bot online as {bot.user} | Guilds: {len(bot.guilds)}")

# ─── Message guard: slot channel mein sirf owner message kar sakta hai ────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    data = load_data()
    slot = data["slots"].get(str(message.channel.id))
    if slot and slot.get("status") == "active":
        owner_id = int(slot["user_id"])
        # Agar sender owner nahi hai aur staff bhi nahi hai toh message delete karo
        if message.author.id != owner_id and not is_staff(message.author, data):
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await message.author.send(
                    f"❌ Aap **{message.channel.name}** mein message nahi kar sakte. Yeh sirf slot owner ka channel hai."
                )
            except Exception:
                pass
            return

    await bot.process_commands(message)

# ════════════════════════════════════════════════════════════════════════════
#  SETUP WIZARD
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def setup_wizard(ctx):
    data = load_data()
    embed = discord.Embed(
        title="⚙️ SlotBot Setup Wizard",
        description="Answer the following prompts to configure the bot.\nType `skip` to leave optional settings blank.",
        color=discord.Color.blurple()
    )
    await ctx.send(embed=embed)

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    async def ask(question, optional=False):
        suffix = " *(optional — type `skip`)*" if optional else ""
        await ctx.send(f"❓ **{question}**{suffix}")
        try:
            msg = await bot.wait_for("message", timeout=60, check=check)
            return None if msg.content.lower() == "skip" else msg.content
        except asyncio.TimeoutError:
            await ctx.send("⏰ Timed out. Run `s!setup` again.")
            return None

    staff_role = await ask("Mention or paste the ID of your **Staff Role**")
    log_channel = await ask("Mention or paste the ID of the **Log Channel**")
    slot_role = await ask("Mention or paste the ID of the **Slot Role** (given to slot owners)", optional=True)
    ticket_category = await ask("Paste the ID of the **Ticket Category** (for support tickets)", optional=True)
    transcript_channel = await ask("Paste the ID of the **Transcript Channel** for ticket logs", optional=True)
    default_pings = await ask("Default **ping limit** per slot (e.g. `10`)")

    def extract_id(val):
        if val is None:
            return None
        val = val.strip().lstrip("<#@&").rstrip(">")
        return val if val.isdigit() else None

    config = {
        "staff_role_id": extract_id(staff_role),
        "log_channel_id": extract_id(log_channel),
        "slot_role_id": extract_id(slot_role),
        "ticket_category_id": extract_id(ticket_category),
        "transcript_channel_id": extract_id(transcript_channel),
        "default_pings": int(default_pings) if default_pings and default_pings.isdigit() else 10
    }
    data["config"] = config
    save_data(data)

    summary = discord.Embed(title="✅ Setup Complete", color=discord.Color.green())
    for k, v in config.items():
        summary.add_field(name=k.replace("_", " ").title(), value=str(v) if v else "Not set", inline=True)
    await ctx.send(embed=summary)

# ════════════════════════════════════════════════════════════════════════════
#  SLOT CREATION
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="slot")
async def create_slot(ctx, member: discord.Member, duration: str, pings: int = None, *, category: str = "General"):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    if str(member.id) in data.get("blacklist", []):
        return await ctx.send(f"❌ **{member}** is blacklisted and cannot receive slots.")

    td = parse_duration(duration)
    if not td:
        return await ctx.send("❌ Invalid duration. Use formats like `7d`, `1m`, `2h`.")

    pings_allowed = pings if pings is not None else data["config"].get("default_pings", 10)
    expires_at = datetime.utcnow() + td

    # Create channel
    # default_role = view allowed but send_messages=False (sirf owner post kar sakta hai)
    guild = ctx.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
    }
    slot_channel = await guild.create_text_channel(
        name=f"🎰・{member.name.lower()}-slot",
        overwrites=overwrites,
        reason=f"Slot created for {member} by {ctx.author}"
    )

    # Give slot role
    slot_role_id = data["config"].get("slot_role_id")
    if slot_role_id:
        role = guild.get_role(int(slot_role_id))
        if role:
            await member.add_roles(role)

    slot_entry = {
        "guild_id": str(guild.id),
        "channel_id": str(slot_channel.id),
        "user_id": str(member.id),
        "category": category,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at.isoformat(),
        "pings_allowed": pings_allowed,
        "pings_used": 0,
        "last_ping": None,
        "status": "active",
        "on_hold": False,
        "hold_reason": None,
        "warnings": []
    }
    data["slots"][str(slot_channel.id)] = slot_entry
    save_data(data)

    # Welcome embed in slot channel
    welcome = discord.Embed(
        title="🎰 Your Slot is Ready!",
        description=f"Welcome {member.mention}! This is your personal slot channel.",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    welcome.add_field(name="⏳ Expires", value=f"<t:{int(expires_at.timestamp())}:F>")
    welcome.add_field(name="🔔 Pings Allowed", value=str(pings_allowed))
    welcome.add_field(name="📂 Category", value=category)
    welcome.set_footer(text="Use s!ping to ping | s!mystats for your stats")
    await slot_channel.send(member.mention, embed=welcome)

    # DM the user
    try:
        dm_embed = discord.Embed(
            title="🎰 Slot Created!",
            description=f"You've received a slot in **{guild.name}**!",
            color=discord.Color.green()
        )
        dm_embed.add_field(name="Channel", value=slot_channel.mention)
        dm_embed.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>")
        await member.send(embed=dm_embed)
    except Exception:
        pass

    confirm = discord.Embed(
        title="✅ Slot Created",
        description=f"Slot for {member.mention} in {slot_channel.mention}",
        color=discord.Color.green()
    )
    await ctx.send(embed=confirm)
    await log_action(bot, data, "SLOT CREATED", ctx.author, member, f"Channel: {slot_channel.name} | Duration: {duration} | Pings: {pings_allowed}")

# ════════════════════════════════════════════════════════════════════════════
#  SLOT RENEW
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="renew")
async def renew_slot(ctx, channel: discord.TextChannel, duration: str):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    slot = data["slots"].get(str(channel.id))
    if not slot:
        return await ctx.send("❌ No slot found for that channel.")

    td = parse_duration(duration)
    if not td:
        return await ctx.send("❌ Invalid duration.")

    new_expires = datetime.utcnow() + td
    slot["expires_at"] = new_expires.isoformat()
    slot["status"] = "active"
    save_data(data)

    embed = discord.Embed(title="🔄 Slot Renewed", color=discord.Color.green())
    embed.add_field(name="New Expiry", value=f"<t:{int(new_expires.timestamp())}:F>")
    await ctx.send(embed=embed)
    await channel.send(embed=discord.Embed(
        title="🔄 Slot Renewed!",
        description=f"Your slot has been renewed until <t:{int(new_expires.timestamp())}:F>!",
        color=discord.Color.green()
    ))
    await log_action(bot, data, "SLOT RENEWED", ctx.author, channel, f"New expiry: {new_expires}")

# ════════════════════════════════════════════════════════════════════════════
#  SLOT EXTEND
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="extend")
async def extend_slot(ctx, channel: discord.TextChannel, duration: str):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    slot = data["slots"].get(str(channel.id))
    if not slot:
        return await ctx.send("❌ No slot found.")

    td = parse_duration(duration)
    if not td:
        return await ctx.send("❌ Invalid duration.")

    current_expires = datetime.fromisoformat(slot["expires_at"])
    new_expires = current_expires + td
    slot["expires_at"] = new_expires.isoformat()
    save_data(data)

    embed = discord.Embed(title="⏳ Slot Extended", color=discord.Color.blue())
    embed.add_field(name="New Expiry", value=f"<t:{int(new_expires.timestamp())}:F>")
    await ctx.send(embed=embed)
    await channel.send(embed=discord.Embed(
        title="⏳ Slot Extended!",
        description=f"Your slot duration has been extended to <t:{int(new_expires.timestamp())}:F>!",
        color=discord.Color.blue()
    ))
    await log_action(bot, data, "SLOT EXTENDED", ctx.author, channel, f"Extended by {duration}")

# ════════════════════════════════════════════════════════════════════════════
#  SLOT TRANSFER
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="transfer")
async def transfer_slot(ctx, channel: discord.TextChannel, new_owner: discord.Member):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    slot = data["slots"].get(str(channel.id))
    if not slot:
        return await ctx.send("❌ No slot found.")

    old_owner = ctx.guild.get_member(int(slot["user_id"]))
    slot_role_id = data["config"].get("slot_role_id")

    # Update permissions
    if old_owner:
        await channel.set_permissions(old_owner, overwrite=None)
        if slot_role_id:
            role = ctx.guild.get_role(int(slot_role_id))
            if role and role in old_owner.roles:
                await old_owner.remove_roles(role)

    await channel.set_permissions(new_owner, view_channel=True, send_messages=True, embed_links=True, attach_files=True)
    if slot_role_id:
        role = ctx.guild.get_role(int(slot_role_id))
        if role:
            await new_owner.add_roles(role)

    slot["user_id"] = str(new_owner.id)
    save_data(data)

    await ctx.send(embed=discord.Embed(
        title="🔀 Slot Transferred",
        description=f"Slot transferred from {old_owner.mention if old_owner else 'Unknown'} to {new_owner.mention}",
        color=discord.Color.gold()
    ))
    await log_action(bot, data, "SLOT TRANSFERRED", ctx.author, new_owner,
                     f"Channel: {channel.name} | From: {old_owner}")

# ════════════════════════════════════════════════════════════════════════════
#  HOLD / UNHOLD
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="hold")
async def hold_slot(ctx, channel: discord.TextChannel, *, reason: str = "Under investigation"):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    slot = data["slots"].get(str(channel.id))
    if not slot:
        return await ctx.send("❌ No slot found.")

    slot["on_hold"] = True
    slot["hold_reason"] = reason
    save_data(data)

    await channel.set_permissions(ctx.guild.get_member(int(slot["user_id"])), send_messages=False)
    await channel.send(embed=discord.Embed(
        title="⏸️ Slot On Hold",
        description=f"**Reason:** {reason}\nContact staff for more information.",
        color=discord.Color.yellow()
    ))
    await ctx.send(f"⏸️ Slot `{channel.name}` placed on hold.")
    await log_action(bot, data, "SLOT HELD", ctx.author, channel, reason)

@bot.command(name="unhold")
async def unhold_slot(ctx, channel: discord.TextChannel):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    slot = data["slots"].get(str(channel.id))
    if not slot:
        return await ctx.send("❌ No slot found.")

    slot["on_hold"] = False
    slot["hold_reason"] = None
    save_data(data)

    member = ctx.guild.get_member(int(slot["user_id"]))
    if member:
        await channel.set_permissions(member, send_messages=True)
    await channel.send(embed=discord.Embed(
        title="▶️ Slot Resumed",
        description="Your slot hold has been lifted. You can post again!",
        color=discord.Color.green()
    ))
    await ctx.send(f"▶️ Hold removed from `{channel.name}`.")
    await log_action(bot, data, "SLOT UNHOLD", ctx.author, channel)

# ════════════════════════════════════════════════════════════════════════════
#  SLOT REVOKE
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="revoke")
async def revoke_slot(ctx, channel: discord.TextChannel, *, reason: str = "No reason provided"):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    slot = data["slots"].get(str(channel.id))
    if not slot:
        return await ctx.send("❌ No slot found.")

    member = ctx.guild.get_member(int(slot["user_id"]))
    slot_role_id = data["config"].get("slot_role_id")

    # Remove role & permissions
    if member:
        if slot_role_id:
            role = ctx.guild.get_role(int(slot_role_id))
            if role and role in member.roles:
                await member.remove_roles(role)
        await channel.set_permissions(member, view_channel=False, send_messages=False)
        try:
            await member.send(embed=discord.Embed(
                title="🚫 Slot Revoked",
                description=f"Your slot in **{ctx.guild.name}** has been revoked.\n**Reason:** {reason}",
                color=discord.Color.red()
            ))
        except Exception:
            pass

    slot["status"] = "revoked"
    save_data(data)

    await channel.send(embed=discord.Embed(
        title="🚫 Slot Revoked",
        description=f"**Reason:** {reason}",
        color=discord.Color.red()
    ))
    await ctx.send(f"🚫 Slot `{channel.name}` revoked.")
    await log_action(bot, data, "SLOT REVOKED", ctx.author, member, reason)

# ════════════════════════════════════════════════════════════════════════════
#  PING MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="ping")
@commands.cooldown(1, 3600, commands.BucketType.channel)
async def use_ping(ctx):
    data = load_data()
    slot = data["slots"].get(str(ctx.channel.id))
    if not slot:
        return await ctx.send("❌ This is not a slot channel.")

    if slot["user_id"] != str(ctx.author.id) and not is_staff(ctx.author, data):
        return await ctx.send("❌ Only the slot owner can use pings.")

    if slot.get("on_hold"):
        return await ctx.send("❌ Your slot is on hold.")

    if slot["pings_used"] >= slot["pings_allowed"]:
        return await ctx.send(f"❌ No pings remaining. Resets in 24 hours.")

    slot["pings_used"] += 1
    slot["last_ping"] = datetime.utcnow().isoformat()
    save_data(data)
    remaining = slot["pings_allowed"] - slot["pings_used"]
    await ctx.send(f"📢 Ping used! **{remaining}** ping(s) remaining today.")

@bot.command(name="setpings")
async def set_pings(ctx, channel: discord.TextChannel, amount: int):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")
    slot = data["slots"].get(str(channel.id))
    if not slot:
        return await ctx.send("❌ No slot found.")
    slot["pings_allowed"] = amount
    save_data(data)
    await ctx.send(f"✅ Ping limit for {channel.mention} set to **{amount}**.")

# ════════════════════════════════════════════════════════════════════════════
#  WARNINGS
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="warn")
async def warn_user(ctx, member: discord.Member, *, reason: str = "No reason"):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    uid = str(member.id)
    if uid not in data["warnings"]:
        data["warnings"][uid] = []

    warning = {
        "reason": reason,
        "by": str(ctx.author),
        "timestamp": datetime.utcnow().isoformat()
    }
    data["warnings"][uid].append(warning)
    save_data(data)

    try:
        await member.send(embed=discord.Embed(
            title="⚠️ Warning Received",
            description=f"**Reason:** {reason}\n**Server:** {ctx.guild.name}",
            color=discord.Color.yellow()
        ))
    except Exception:
        pass

    await ctx.send(embed=discord.Embed(
        title="⚠️ User Warned",
        description=f"{member.mention} now has **{len(data['warnings'][uid])}** warning(s).",
        color=discord.Color.yellow()
    ))
    await log_action(bot, data, "USER WARNED", ctx.author, member, reason)

@bot.command(name="warnings")
async def view_warnings(ctx, member: discord.Member):
    data = load_data()
    warns = data["warnings"].get(str(member.id), [])
    embed = discord.Embed(title=f"⚠️ Warnings for {member}", color=discord.Color.yellow())
    if not warns:
        embed.description = "No warnings."
    else:
        for i, w in enumerate(warns, 1):
            embed.add_field(name=f"#{i} — {w['by']}", value=f"{w['reason']}\n*{w['timestamp'][:10]}*", inline=False)
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════════════════════════════════
#  BLACKLIST
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="blacklist")
async def blacklist_user(ctx, member: discord.Member, *, reason: str = "No reason"):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    uid = str(member.id)
    if uid in data["blacklist"]:
        return await ctx.send(f"⚠️ {member} is already blacklisted.")

    # Auto-revoke any active slots
    for ch_id, slot in data["slots"].items():
        if slot["user_id"] == uid and slot["status"] == "active":
            slot["status"] = "revoked"
            channel = ctx.guild.get_channel(int(ch_id))
            if channel:
                slot_role_id = data["config"].get("slot_role_id")
                if slot_role_id:
                    role = ctx.guild.get_role(int(slot_role_id))
                    if role and role in member.roles:
                        await member.remove_roles(role)
                await channel.set_permissions(member, view_channel=False, send_messages=False)

    data["blacklist"].append(uid)
    save_data(data)

    await ctx.send(embed=discord.Embed(
        title="🚫 User Blacklisted",
        description=f"{member.mention} has been blacklisted. All active slots revoked.",
        color=discord.Color.red()
    ))
    await log_action(bot, data, "USER BLACKLISTED", ctx.author, member, reason)

@bot.command(name="unblacklist")
async def unblacklist_user(ctx, member: discord.Member):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    uid = str(member.id)
    if uid not in data["blacklist"]:
        return await ctx.send(f"⚠️ {member} is not blacklisted.")

    data["blacklist"].remove(uid)
    save_data(data)
    await ctx.send(f"✅ {member.mention} removed from blacklist.")

# ════════════════════════════════════════════════════════════════════════════
#  STATISTICS
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="stats")
async def slot_stats(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    data = load_data()
    slot = data["slots"].get(str(channel.id))
    if not slot:
        return await ctx.send("❌ No slot found for that channel.")

    member = ctx.guild.get_member(int(slot["user_id"]))
    embed = slot_embed(slot, ctx.guild)
    embed.title = "📊 Slot Statistics"
    embed.add_field(name="⚠️ Warnings", value=str(len(data["warnings"].get(slot["user_id"], []))), inline=True)
    await ctx.send(embed=embed)

@bot.command(name="mystats")
async def my_stats(ctx):
    data = load_data()
    uid = str(ctx.author.id)
    user_slots = [(ch_id, s) for ch_id, s in data["slots"].items() if s["user_id"] == uid]
    embed = discord.Embed(title=f"📊 Stats for {ctx.author}", color=discord.Color.blurple())
    embed.add_field(name="Total Slots", value=str(len(user_slots)), inline=True)
    active = sum(1 for _, s in user_slots if s["status"] == "active")
    embed.add_field(name="Active Slots", value=str(active), inline=True)
    embed.add_field(name="Warnings", value=str(len(data["warnings"].get(uid, []))), inline=True)
    for ch_id, s in user_slots:
        ch = ctx.guild.get_channel(int(ch_id))
        name = ch.name if ch else ch_id
        expires = datetime.fromisoformat(s["expires_at"])
        pings_left = s["pings_allowed"] - s["pings_used"]
        embed.add_field(
            name=f"#{name}",
            value=f"Status: `{s['status']}`\nExpires: <t:{int(expires.timestamp())}:R>\nPings: `{pings_left}/{s['pings_allowed']}`",
            inline=True
        )
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════════════════════════════════
#  LEADERBOARD
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard(ctx):
    data = load_data()
    active_slots = [(ch_id, s) for ch_id, s in data["slots"].items() if s["status"] == "active"]
    active_slots.sort(key=lambda x: datetime.fromisoformat(x[1]["expires_at"]), reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title="🏆 Slot Leaderboard", description="Ranked by remaining time", color=discord.Color.gold())
    for i, (ch_id, slot) in enumerate(active_slots[:10]):
        medal = medals[i] if i < 3 else f"**#{i+1}**"
        member = ctx.guild.get_member(int(slot["user_id"]))
        name = str(member) if member else slot["user_id"]
        expires = datetime.fromisoformat(slot["expires_at"])
        embed.add_field(
            name=f"{medal} {name}",
            value=f"Expires <t:{int(expires.timestamp())}:R>",
            inline=False
        )
    if not active_slots:
        embed.description = "No active slots."
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════════════════════════════════
#  ACTIVITY HISTORY
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="history")
async def view_history(ctx, limit: int = 10):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")
    history = data["history"][-limit:][::-1]
    embed = discord.Embed(title="📜 Recent Activity", color=discord.Color.blurple())
    for entry in history:
        embed.add_field(
            name=f"{entry['action']} — {entry['timestamp'][:16]}",
            value=f"By: {entry['actor']}" + (f"\nDetails: {entry['details']}" if entry['details'] else ""),
            inline=False
        )
    if not history:
        embed.description = "No history yet."
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════════════════════════════════
#  NUKE COMMAND
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="nuke")
async def nuke_channel(ctx):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    confirm_embed = discord.Embed(
        title="💣 Confirm Nuke",
        description="React with ✅ to confirm channel clear. All messages will be deleted except bot embeds.",
        color=discord.Color.red()
    )
    msg = await ctx.send(embed=confirm_embed)
    await msg.add_reaction("✅")

    def check(r, u):
        return u == ctx.author and str(r.emoji) == "✅" and r.message.id == msg.id

    try:
        await bot.wait_for("reaction_add", timeout=30, check=check)
    except asyncio.TimeoutError:
        return await ctx.send("Nuke cancelled.")

    # Clone channel and delete original
    new_channel = await ctx.channel.clone(reason=f"Nuked by {ctx.author}")
    await ctx.channel.delete()
    await new_channel.send(embed=discord.Embed(
        title="💣 Channel Nuked",
        description=f"Nuked by {ctx.author.mention}",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    ))
    await log_action(bot, data, "CHANNEL NUKED", ctx.author, ctx.channel)

# ════════════════════════════════════════════════════════════════════════════
#  ANNOUNCEMENTS
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="announce")
async def announce(ctx, channel: discord.TextChannel, *, message: str):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    embed = discord.Embed(
        title="📢 Announcement",
        description=message,
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=f"From: {ctx.author}")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Announcement sent to {channel.mention}.")

# ════════════════════════════════════════════════════════════════════════════
#  SERVER INFO
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="serverinfo")
async def server_info(ctx):
    g = ctx.guild
    embed = discord.Embed(title=f"🏠 {g.name}", color=discord.Color.blurple(), timestamp=datetime.utcnow())
    embed.set_thumbnail(url=g.icon.url if g.icon else "")
    embed.add_field(name="👑 Owner", value=str(g.owner), inline=True)
    embed.add_field(name="👥 Members", value=str(g.member_count), inline=True)
    embed.add_field(name="💬 Channels", value=str(len(g.channels)), inline=True)
    embed.add_field(name="🎭 Roles", value=str(len(g.roles)), inline=True)
    embed.add_field(name="📅 Created", value=f"<t:{int(g.created_at.timestamp())}:D>", inline=True)
    embed.add_field(name="🔒 Verification", value=str(g.verification_level), inline=True)
    data = load_data()
    active_slots = sum(1 for s in data["slots"].values() if s["status"] == "active" and s["guild_id"] == str(g.id))
    embed.add_field(name="🎰 Active Slots", value=str(active_slots), inline=True)
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════════════════════════════════
#  UPTIME
# ════════════════════════════════════════════════════════════════════════════
bot.start_time = datetime.utcnow()

@bot.command(name="uptime")
async def uptime(ctx):
    delta = datetime.utcnow() - bot.start_time
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)
    embed = discord.Embed(
        title="⏱️ Bot Uptime",
        description=f"`{hours}h {minutes}m {seconds}s`",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════════════════════════════════
#  TICKET SYSTEM
# ════════════════════════════════════════════════════════════════════════════
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.blurple, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        config = data.get("config", {})
        cat_id = config.get("ticket_category_id")
        category = interaction.guild.get_channel(int(cat_id)) if cat_id else None

        # Check existing ticket
        for ch_id, ticket in data.get("tickets", {}).items():
            if ticket["user_id"] == str(interaction.user.id) and ticket["status"] == "open":
                ch = interaction.guild.get_channel(int(ch_id))
                if ch:
                    return await interaction.response.send_message(
                        f"❌ You already have an open ticket: {ch.mention}", ephemeral=True
                    )

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        staff_role_id = config.get("staff_role_id")
        if staff_role_id:
            staff_role = interaction.guild.get_role(int(staff_role_id))
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        ticket_channel = await interaction.guild.create_text_channel(
            name=f"ticket-{interaction.user.name.lower()}",
            category=category,
            overwrites=overwrites
        )
        ticket_entry = {
            "user_id": str(interaction.user.id),
            "guild_id": str(interaction.guild.id),
            "opened_at": datetime.utcnow().isoformat(),
            "status": "open"
        }
        data["tickets"][str(ticket_channel.id)] = ticket_entry
        save_data(data)

        close_view = CloseTicketView()
        embed = discord.Embed(
            title="🎫 Support Ticket",
            description=f"Welcome {interaction.user.mention}! Staff will be with you shortly.\nClick **Close** to close this ticket.",
            color=discord.Color.blurple()
        )
        await ticket_channel.send(interaction.user.mention, embed=embed, view=close_view)
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        ticket = data["tickets"].get(str(interaction.channel.id))
        if not ticket:
            return await interaction.response.send_message("❌ Not a ticket.", ephemeral=True)

        # Collect transcript
        messages = []
        async for msg in interaction.channel.history(limit=200, oldest_first=True):
            messages.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M')}] {msg.author}: {msg.content}")
        transcript_text = "\n".join(messages)

        user = interaction.guild.get_member(int(ticket["user_id"]))
        if user:
            try:
                dm_embed = discord.Embed(
                    title="🎫 Ticket Closed",
                    description=f"Your ticket in **{interaction.guild.name}** has been closed.",
                    color=discord.Color.red()
                )
                dm_embed.add_field(name="Transcript Preview", value=transcript_text[:800] + "..." if len(transcript_text) > 800 else transcript_text)
                await user.send(embed=dm_embed)
            except Exception:
                pass

        # Save transcript
        transcript_ch_id = data["config"].get("transcript_channel_id")
        if transcript_ch_id:
            tr_ch = interaction.guild.get_channel(int(transcript_ch_id))
            if tr_ch:
                tr_embed = discord.Embed(title=f"📋 Ticket Transcript: {interaction.channel.name}", color=discord.Color.blurple())
                tr_embed.description = f"```{transcript_text[:3900]}```"
                await tr_ch.send(embed=tr_embed)

        ticket["status"] = "closed"
        save_data(data)
        await interaction.response.send_message("🔒 Closing ticket...")
        await asyncio.sleep(2)
        await interaction.channel.delete()

@bot.command(name="ticketpanel")
async def ticket_panel(ctx, channel: discord.TextChannel = None):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")
    channel = channel or ctx.channel
    embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Click the button below to open a support ticket.",
        color=discord.Color.blurple()
    )
    await channel.send(embed=embed, view=TicketView())
    await ctx.send(f"✅ Ticket panel sent to {channel.mention}.")

# ════════════════════════════════════════════════════════════════════════════
#  REDEEM CODES
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="gencode")
async def gen_code(ctx, duration: str, pings: int = None, uses: int = 1, *, category: str = "General"):
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    td = parse_duration(duration)
    if not td:
        return await ctx.send("❌ Invalid duration.")

    code = "SLOT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    data["codes"][code] = {
        "duration": duration,
        "pings": pings if pings else data["config"].get("default_pings", 10),
        "category": category,
        "uses_left": uses,
        "created_by": str(ctx.author.id),
        "created_at": datetime.utcnow().isoformat()
    }
    save_data(data)

    embed = discord.Embed(title="🎟️ Code Generated", color=discord.Color.green())
    embed.add_field(name="Code", value=f"`{code}`", inline=False)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Pings", value=str(data["codes"][code]["pings"]), inline=True)
    embed.add_field(name="Uses", value=str(uses), inline=True)
    await ctx.author.send(embed=embed)
    await ctx.send("✅ Code generated and sent to your DMs!")

@bot.command(name="redeem")
async def redeem_code(ctx, code: str):
    data = load_data()
    code_data = data["codes"].get(code.upper())
    if not code_data:
        return await ctx.send("❌ Invalid or expired code.")
    if code_data["uses_left"] <= 0:
        return await ctx.send("❌ This code has no uses remaining.")
    if str(ctx.author.id) in data.get("blacklist", []):
        return await ctx.send("❌ You are blacklisted.")

    code_data["uses_left"] -= 1
    save_data(data)

    # Simulate slot creation
    fake_ctx = ctx
    await create_slot(fake_ctx, ctx.author, code_data["duration"], code_data["pings"], category=code_data["category"])
    await log_action(bot, data, "CODE REDEEMED", ctx.author, None, f"Code: {code}")

# ════════════════════════════════════════════════════════════════════════════
#  DESTROY — Slot channel ko poori tarah delete karo
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="destroy")
async def destroy_slot(ctx, channel: discord.TextChannel = None):
    """
    s!destroy [#channel]
    Channel nahi diya toh current channel delete hoga.
    Slot data bhi saaf ho jaata hai.
    """
    data = load_data()
    if not is_staff(ctx.author, data):
        return await ctx.send("❌ Staff only.")

    target = channel or ctx.channel
    slot = data["slots"].get(str(target.id))

    # Slot owner ko DM bhejo agar slot tha
    if slot:
        guild = ctx.guild
        member = guild.get_member(int(slot["user_id"]))
        slot_role_id = data["config"].get("slot_role_id")
        if member:
            if slot_role_id:
                role = guild.get_role(int(slot_role_id))
                if role and role in member.roles:
                    await member.remove_roles(role)
            try:
                await member.send(embed=discord.Embed(
                    title="💥 Slot Destroy Ho Gaya",
                    description=f"Tumhara slot channel **{target.name}** ko `{ctx.author}` ne delete kar diya.",
                    color=discord.Color.dark_red()
                ))
            except Exception:
                pass
        # Data mein se hataao
        del data["slots"][str(target.id)]
        save_data(data)

    await log_action(bot, data, "SLOT DESTROYED", ctx.author, target, f"Channel: {target.name} permanently deleted")

    try:
        await target.delete(reason=f"Destroyed by {ctx.author}")
    except Exception as e:
        await ctx.send(f"❌ Channel delete nahi hua: {e}")
        return

    # Agar current channel tha toh wahan confirm nahi bhej sakte, log mein jayega
    if target.id != ctx.channel.id:
        await ctx.send(embed=discord.Embed(
            title="💥 Slot Destroy",
            description=f"`{target.name}` channel permanently delete kar diya gaya.",
            color=discord.Color.dark_red()
        ))

# ════════════════════════════════════════════════════════════════════════════
#  HELP COMMAND
# ════════════════════════════════════════════════════════════════════════════
@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(
        title="🎰 SlotBot Help",
        description="**Prefix:** `s!` | All commands listed below",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="🔧 Setup", value="`s!setup` — Run setup wizard", inline=False)
    embed.add_field(name="🎰 Slot Management (Staff)", value=
        "`s!slot @user <duration> [pings] [category]`\n"
        "`s!renew #channel <duration>`\n"
        "`s!extend #channel <duration>`\n"
        "`s!transfer #channel @user`\n"
        "`s!hold #channel [reason]`\n"
        "`s!unhold #channel`\n"
        "`s!revoke #channel [reason]`\n"
        "`s!setpings #channel <amount>`", inline=False)
    embed.add_field(name="📢 Pings (Slot Owner)", value="`s!ping` — Use a ping in your slot channel", inline=False)
    embed.add_field(name="⚠️ Warnings & Blacklist (Staff)", value=
        "`s!warn @user [reason]`\n"
        "`s!warnings @user`\n"
        "`s!blacklist @user [reason]`\n"
        "`s!unblacklist @user`", inline=False)
    embed.add_field(name="📊 Stats & Info", value=
        "`s!stats [#channel]`\n"
        "`s!mystats`\n"
        "`s!leaderboard`\n"
        "`s!history [limit]`\n"
        "`s!serverinfo`\n"
        "`s!uptime`", inline=False)
    embed.add_field(name="🎫 Tickets", value=
        "`s!ticketpanel [#channel]` — Send ticket panel (Staff)\n"
        "Click button to open a ticket", inline=False)
    embed.add_field(name="🎟️ Redeem Codes", value=
        "`s!gencode <duration> [pings] [uses] [category]` — Staff\n"
        "`s!redeem <CODE>` — Redeem a slot code", inline=False)
    embed.add_field(name="🛠️ Utilities (Staff)", value=
        "`s!destroy [#channel]` — Slot channel permanently delete karo\n"
        "`s!nuke` — Clear channel\n"
        "`s!announce #channel <message>`", inline=False)
    embed.add_field(name="⏱️ Duration Format", value="`7d` = 7 days | `1m` = 1 month | `2h` = 2 hours", inline=False)
    embed.set_footer(text="SlotBot | Built for Discord")
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════════════════════════════════
#  ERROR HANDLER
# ════════════════════════════════════════════════════════════════════════════
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Cooldown! Try again in **{error.retry_after:.0f}s**.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`. Use `s!help` for usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid argument. Check `s!help` for correct usage.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You lack the permissions to do this.")
    else:
        logger.error(f"Unhandled error: {error}")
        await ctx.send(f"❌ An error occurred: `{error}`")

# ════════════════════════════════════════════════════════════════════════════
#  RUN
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("ERROR: Set the DISCORD_BOT_TOKEN environment variable.")
    else:
        bot.run(TOKEN)
