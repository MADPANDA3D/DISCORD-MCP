import asyncio
import os
from datetime import timezone

import aiohttp
import discord
from discord.ext import commands
from mcp.server.fastmcp import FastMCP


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DEFAULT_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
MCP_HTTP_PORT = int(os.getenv("MCP_HTTP_PORT", "8085"))
MCP_BIND_ADDRESS = os.getenv("MCP_BIND_ADDRESS", "0.0.0.0")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
mcp = FastMCP(name="discord-mcp", stateless_http=True, json_response=True)


async def ensure_ready():
    if not bot.is_ready():
        await bot.wait_until_ready()


def resolve_guild_id(guild_id: str | None) -> int:
    if (not guild_id or not guild_id.strip()) and DEFAULT_GUILD_ID:
        guild_id = DEFAULT_GUILD_ID
    if not guild_id:
        raise ValueError("guildId cannot be null")
    return int(guild_id)


async def get_guild(guild_id: str | None) -> discord.Guild:
    await ensure_ready()
    resolved_id = resolve_guild_id(guild_id)
    guild = bot.get_guild(resolved_id)
    if guild is None:
        guild = await bot.fetch_guild(resolved_id)
    if guild is None:
        raise ValueError("Discord server not found by guildId")
    return guild


async def get_text_channel(channel_id: str) -> discord.TextChannel:
    await ensure_ready()
    if not channel_id:
        raise ValueError("channelId cannot be null")
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        channel = await bot.fetch_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Channel not found by channelId")
    return channel


async def get_dm_channel(user_id: str) -> discord.DMChannel:
    await ensure_ready()
    if not user_id:
        raise ValueError("userId cannot be null")
    user = await bot.fetch_user(int(user_id))
    if user is None:
        raise ValueError("User not found by userId")
    return await user.create_dm()


@mcp.on_startup
async def startup():
    asyncio.create_task(bot.start(DISCORD_TOKEN))


@mcp.on_shutdown
async def shutdown():
    if bot.is_ready():
        await bot.close()


@mcp.tool()
async def get_server_info(guild_id: str | None = None) -> str:
    guild = await get_guild(guild_id)
    owner = await guild.fetch_owner()
    creation_date = guild.created_at.astimezone(timezone.utc).date().isoformat()
    boost_count = guild.premium_subscription_count or 0
    boost_tier = str(guild.premium_tier)

    return "\n".join([
        f"Server Name: {guild.name}",
        f"Server ID: {guild.id}",
        f"Owner: {owner.name}",
        f"Created On: {creation_date}",
        f"Members: {guild.member_count}",
        "Channels: ",
        f" - Text: {len(guild.text_channels)}",
        f" - Voice: {len(guild.voice_channels)}",
        f"  - Categories: {len(guild.categories)}",
        "Boosts: ",
        f" - Count: {boost_count}",
        f" - Tier: {boost_tier}",
    ])


@mcp.tool()
async def send_message(channel_id: str, message: str) -> str:
    if not message:
        raise ValueError("message cannot be null")
    channel = await get_text_channel(channel_id)
    sent_message = await channel.send(message)
    return f"Message sent successfully. Message link: {sent_message.jump_url}"


@mcp.tool()
async def edit_message(channel_id: str, message_id: str, new_message: str) -> str:
    if not new_message:
        raise ValueError("newMessage cannot be null")
    channel = await get_text_channel(channel_id)
    msg = await channel.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    edited = await msg.edit(content=new_message)
    return f"Message edited successfully. Message link: {edited.jump_url}"


@mcp.tool()
async def delete_message(channel_id: str, message_id: str) -> str:
    channel = await get_text_channel(channel_id)
    msg = await channel.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    await msg.delete()
    return "Message deleted successfully"


@mcp.tool()
async def read_messages(channel_id: str, count: str | None = None) -> str:
    channel = await get_text_channel(channel_id)
    limit = int(count) if count else 100
    messages = [m async for m in channel.history(limit=limit)]
    formatted = []
    for msg in messages:
        formatted.append(
            f"- (ID: {msg.id}) **[{msg.author.name}]** `{msg.created_at}`: ```{msg.content}```"
        )
    return f"**Retrieved {len(messages)} messages:** \n" + "\n".join(formatted)


@mcp.tool()
async def add_reaction(channel_id: str, message_id: str, emoji: str) -> str:
    if not emoji:
        raise ValueError("emoji cannot be null")
    channel = await get_text_channel(channel_id)
    msg = await channel.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    await msg.add_reaction(emoji)
    return f"Added reaction successfully. Message link: {msg.jump_url}"


@mcp.tool()
async def remove_reaction(channel_id: str, message_id: str, emoji: str) -> str:
    if not emoji:
        raise ValueError("emoji cannot be null")
    channel = await get_text_channel(channel_id)
    msg = await channel.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    await msg.remove_reaction(emoji, bot.user)
    return f"Removed reaction successfully. Message link: {msg.jump_url}"


@mcp.tool()
async def get_user_id_by_name(username: str, guild_id: str | None = None) -> str:
    if not username:
        raise ValueError("username cannot be null")
    guild = await get_guild(guild_id)
    name = username
    discriminator = None
    if "#" in username:
        idx = username.rfind("#")
        name = username[:idx]
        discriminator = username[idx + 1 :]

    members = [m for m in guild.members if m.name.lower() == name.lower()]
    if discriminator:
        members = [m for m in members if m.discriminator == discriminator]

    if not members:
        raise ValueError(f"No user found with username {username}")
    if len(members) > 1:
        user_list = ", ".join(
            f"{m.name}#{m.discriminator} (ID: {m.id})" for m in members
        )
        raise ValueError(
            f"Multiple users found with username '{username}'. List: {user_list}. "
            "Please specify the full username#discriminator."
        )
    return str(members[0].id)


@mcp.tool()
async def send_private_message(user_id: str, message: str) -> str:
    if not message:
        raise ValueError("message cannot be null")
    dm = await get_dm_channel(user_id)
    sent = await dm.send(message)
    return f"Message sent successfully. Message link: {sent.jump_url}"


@mcp.tool()
async def edit_private_message(user_id: str, message_id: str, new_message: str) -> str:
    if not new_message:
        raise ValueError("newMessage cannot be null")
    dm = await get_dm_channel(user_id)
    msg = await dm.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    edited = await msg.edit(content=new_message)
    return f"Message edited successfully. Message link: {edited.jump_url}"


@mcp.tool()
async def delete_private_message(user_id: str, message_id: str) -> str:
    dm = await get_dm_channel(user_id)
    msg = await dm.fetch_message(int(message_id))
    if msg is None:
        raise ValueError("Message not found by messageId")
    await msg.delete()
    return "Message deleted successfully"


@mcp.tool()
async def read_private_messages(user_id: str, count: str | None = None) -> str:
    dm = await get_dm_channel(user_id)
    limit = int(count) if count else 100
    messages = [m async for m in dm.history(limit=limit)]
    formatted = []
    for msg in messages:
        formatted.append(
            f"- (ID: {msg.id}) **[{msg.author.name}]** `{msg.created_at}`: ```{msg.content}```"
        )
    return f"**Retrieved {len(messages)} messages:** \n" + "\n".join(formatted)


@mcp.tool()
async def create_text_channel(guild_id: str | None, name: str, category_id: str | None = None) -> str:
    if not name:
        raise ValueError("name cannot be null")
    guild = await get_guild(guild_id)
    category = None
    if category_id:
        category = discord.utils.get(guild.categories, id=int(category_id))
        if category is None:
            category = await guild.fetch_channel(int(category_id))
        if not isinstance(category, discord.CategoryChannel):
            raise ValueError("Category not found by categoryId")
    channel = await guild.create_text_channel(name, category=category)
    if category:
        return f"Created new text channel: {channel.name} (ID: {channel.id}) in category: {category.name}"
    return f"Created new text channel: {channel.name} (ID: {channel.id})"


@mcp.tool()
async def delete_channel(guild_id: str | None, channel_id: str) -> str:
    guild = await get_guild(guild_id)
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        channel = await guild.fetch_channel(int(channel_id))
    if channel is None:
        raise ValueError("Channel not found by channelId")
    channel_type = channel.type.name
    channel_name = channel.name
    await channel.delete()
    return f"Deleted {channel_type} channel: {channel_name}"


@mcp.tool()
async def find_channel(guild_id: str | None, channel_name: str) -> str:
    if not channel_name:
        raise ValueError("channelName cannot be null")
    guild = await get_guild(guild_id)
    channels = [c for c in guild.channels if c.name.lower() == channel_name.lower()]
    if not channels:
        raise ValueError(f"No channels found with name {channel_name}")
    if len(channels) > 1:
        channel_list = "\n".join(
            f"- {c.type.name} channel: {c.name} (ID: {c.id})" for c in channels
        )
        return f"Retrieved {len(channels)} channels:\n{channel_list}"
    channel = channels[0]
    return f"Retrieved {channel.type.name} channel: {channel.name} (ID: {channel.id})"


@mcp.tool()
async def list_channels(guild_id: str | None = None) -> str:
    guild = await get_guild(guild_id)
    channels = guild.channels
    if not channels:
        raise ValueError("No channels found by guildId")
    channel_list = "\n".join(
        f"- {c.type.name} channel: {c.name} (ID: {c.id})" for c in channels
    )
    return f"Retrieved {len(channels)} channels:\n{channel_list}"


@mcp.tool()
async def create_category(guild_id: str | None, name: str) -> str:
    if not name:
        raise ValueError("name cannot be null")
    guild = await get_guild(guild_id)
    category = await guild.create_category(name)
    return f"Created new category: {category.name}"


@mcp.tool()
async def delete_category(guild_id: str | None, category_id: str) -> str:
    guild = await get_guild(guild_id)
    category = discord.utils.get(guild.categories, id=int(category_id))
    if category is None:
        category = await guild.fetch_channel(int(category_id))
    if not isinstance(category, discord.CategoryChannel):
        raise ValueError("Category not found by categoryId")
    name = category.name
    await category.delete()
    return f"Deleted category: {name}"


@mcp.tool()
async def find_category(guild_id: str | None, category_name: str) -> str:
    if not category_name:
        raise ValueError("categoryName cannot be null")
    guild = await get_guild(guild_id)
    categories = [c for c in guild.categories if c.name.lower() == category_name.lower()]
    if not categories:
        raise ValueError(f"Category {category_name} not found")
    if len(categories) > 1:
        channel_list = ", ".join(f"**{c.name}** - `{c.id}`" for c in categories)
        raise ValueError(
            f"Multiple channels found with name {category_name}.\n"
            f"List: {channel_list}.\nPlease specify the channel ID."
        )
    category = categories[0]
    return f"Retrieved category: {category.name}, with ID: {category.id}"


@mcp.tool()
async def list_channels_in_category(guild_id: str | None, category_id: str) -> str:
    guild = await get_guild(guild_id)
    category = discord.utils.get(guild.categories, id=int(category_id))
    if category is None:
        category = await guild.fetch_channel(int(category_id))
    if not isinstance(category, discord.CategoryChannel):
        raise ValueError("Category not found by categoryId")
    channels = category.channels
    if not channels:
        raise ValueError("Category not contains any channels")
    channel_list = "\n".join(
        f"- {c.type.name} channel: {c.name} (ID: {c.id})" for c in channels
    )
    return f"Retrieved {len(channels)} channels:\n{channel_list}"


@mcp.tool()
async def create_webhook(channel_id: str, name: str) -> str:
    if not name:
        raise ValueError("webhook name cannot be null")
    channel = await get_text_channel(channel_id)
    webhook = await channel.create_webhook(name)
    return f"Created {name} webhook: {webhook.url}"


@mcp.tool()
async def delete_webhook(webhook_id: str) -> str:
    await ensure_ready()
    webhook = await bot.fetch_webhook(int(webhook_id))
    if webhook is None:
        raise ValueError("Webhook not found by webhookId")
    name = webhook.name
    await webhook.delete()
    return f"Deleted {name} webhook"


@mcp.tool()
async def list_webhooks(channel_id: str) -> str:
    channel = await get_text_channel(channel_id)
    webhooks = await channel.webhooks()
    if not webhooks:
        raise ValueError("No webhooks found")
    formatted = [
        f"- (ID: {w.id}) **[{w.name}]** ```{w.url}```" for w in webhooks
    ]
    return f"**Retrieved {len(formatted)} messages:** \n" + "\n".join(formatted)


@mcp.tool()
async def send_webhook_message(webhook_url: str, message: str) -> str:
    if not webhook_url:
        raise ValueError("webhookUrl cannot be null")
    if not message:
        raise ValueError("message cannot be null")
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(webhook_url, session=session)
        sent = await webhook.send(message, wait=True)
        if sent is None:
            return "Message sent successfully."
        return f"Message sent successfully. Message link: {sent.jump_url}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=MCP_BIND_ADDRESS, port=MCP_HTTP_PORT)
