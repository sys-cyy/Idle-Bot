import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import threading
import json
import asyncio
import sys
import datetime
from flask import Flask, render_template, request, jsonify

# Load environment variables from .env for local development
load_dotenv()

# --- Global State & Configuration Storage ---

CONFIG_FILE = 'server_configs.json'

def load_configs():
    """Loads server configurations from the JSON file, ensuring defaults are present."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                content = f.read()
                configs = json.loads(content) if content else {}
        except json.JSONDecodeError:
            print("Warning: JSON config file is corrupted. Starting new config.")
            configs = {}
    else:
        configs = {}
        
    for guild_id in list(configs.keys()):
        config = configs[guild_id]
        if 'allowed_users' not in config:
            config['allowed_users'] = []
    return configs

def save_configs(configs):
    """Saves server configurations to the JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(configs, f, indent=4)

SERVER_CONFIGS = load_configs()

# --- Global Bot State & Logging ---
bot_thread = None
bot_instance = None
bot_loop = None 
global_logs = [] 

def log_to_global(message):
    """Adds a timestamped message to the global log list."""
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("[%Y-%m-%d %H:%M:%S UTC]")
    global_logs.append(f"{timestamp} {message}")
    if len(global_logs) > 50: # Keep log size manageable
        global_logs.pop(0)

# --- Custom Permission Check ---

def is_admin_or_allowed():
    """Check if the user has Administrator permission OR is explicitly allowed."""
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator: return True
        guild_id_str = str(ctx.guild.id)
        if guild_id_str in SERVER_CONFIGS:
            allowed_users = SERVER_CONFIGS[guild_id_str].get('allowed_users', [])
            if ctx.author.id in allowed_users: return True
        return False
    return commands.check(predicate)

# --- Discord Bot Setup ---

def get_bot_client(token):
    """Initializes and configures the discord.py bot with prefix commands."""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True 
    intents.guilds = True       
    
    bot = commands.Bot(command_prefix='.', intents=intents)

    @bot.event
    async def on_ready():
        log_to_global(f'✅ Logged in as {bot.user}!')
        global bot_loop
        bot_loop = bot.loop
        log_to_global("🤖 Bot is operational.")

    # --- PREFIX COMMANDS (Unchanged) ---
    
    # (All .adduser, .vcchannelid, .joinvc, .leavevc, .help, and on_command_error functions remain unchanged)
    # ... (Omitted for brevity, but they are still here) ...
    @bot.command(name="adduser", help="[OWNER ONLY] Adds a user who can use the bot's voice commands.")
    @commands.is_owner()
    async def add_user_to_config(ctx: commands.Context, member: discord.Member):
        guild_id_str = str(ctx.guild.id)
        if guild_id_str not in SERVER_CONFIGS:
            SERVER_CONFIGS[guild_id_str] = {'channel_id': None, 'allowed_users': []}
        allowed_users = SERVER_CONFIGS[guild_id_str]['allowed_users']
        if member.id in allowed_users:
            return await ctx.send(f"❌ **{member.display_name}** is already allowed to use the commands.")
        allowed_users.append(member.id)
        save_configs(SERVER_CONFIGS)
        await ctx.send(f"✅ **{member.display_name}** can now use the voice configuration commands.")

    @bot.command(name="vcchannelid", help="[ADMIN/ALLOWED] Sets the default Voice Channel ID for this server.")
    @is_admin_or_allowed()
    async def set_vc_channel_id(ctx: commands.Context, channel_id: str):
        try: target_id = int(channel_id)
        except ValueError: return await ctx.send("❌ Error: The Channel ID must be a valid number.")
        guild_id_str = str(ctx.guild.id)
        if guild_id_str not in SERVER_CONFIGS:
             SERVER_CONFIGS[guild_id_str] = {'channel_id': target_id, 'allowed_users': []}
        else:
             SERVER_CONFIGS[guild_id_str]['channel_id'] = target_id
        save_configs(SERVER_CONFIGS)
        try:
            channel = bot.get_channel(target_id) or await bot.fetch_channel(target_id)
            await ctx.send(f"✅ Success! Default voice channel for this server is now set to **{channel.name}** (`{target_id}`).")
        except (discord.NotFound, discord.Forbidden):
            await ctx.send(f"✅ Success! Channel ID `{target_id}` is saved, but I could not find or access that channel.")

    @bot.command(name="joinvc", help="[ADMIN/ALLOWED] Makes the bot join the configured voice channel.")
    @is_admin_or_allowed()
    async def join_vc(ctx: commands.Context):
        guild_id_str = str(ctx.guild.id)
        if guild_id_str not in SERVER_CONFIGS or SERVER_CONFIGS[guild_id_str].get('channel_id') is None:
            return await ctx.send("❌ Error: A voice channel must be set first. Use `.vcchannelid <channel_id>`.")
        target_id = SERVER_CONFIGS[guild_id_str]['channel_id']
        channel = bot.get_channel(target_id)
        if not channel or not isinstance(channel, discord.VoiceChannel):
            return await ctx.send("❌ Error: The configured channel is invalid or not a voice channel. Please set a new ID.")
        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
            return await ctx.send(f"🎤 Moving to **{channel.name}** and going idle!")
        else:
            try:
                await channel.connect()
                await ctx.send(f"🎤 Joined **{channel.name}** and going idle!")
            except Exception as e:
                log_to_global(f"Voice join error: {e}")
                await ctx.send("❌ Error joining VC. Check bot permissions (Connect, Speak).")

    @bot.command(name="leavevc", help="[ADMIN/ALLOWED] Makes the bot leave the voice channel.")
    @is_admin_or_allowed()
    async def leave_vc(ctx: commands.Context):
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.send("👋 Left the voice channel.")
        else:
            await ctx.send("❌ Error: I'm not in a voice channel!")
            
    bot.remove_command('help')
    @bot.command(name="help")
    async def custom_help(ctx: commands.Context):
        embed = discord.Embed(title="Idle Bot Command Guide 🤖", description="All commands use the prefix **`.`**", color=discord.Color.blue())
        embed.add_field(name="⚙️ Server Owner Commands", value="**`.adduser <@user>`**\n-> Allows a designated member to use the configuration commands (`.vcchannelid`, `.joinvc`, `.leavevc`).", inline=False)
        embed.add_field(name="🎤 Admin/Allowed User Commands", value="**`.vcchannelid <ID>`**\n-> Sets the specific voice channel ID.\n**`.joinvc`**\n-> Makes the bot connect to the saved voice channel ID.\n**`.leavevc`**\n-> Forces the bot to disconnect.", inline=False)
        embed.set_footer(text="Configuration commands require the Administrator permission or being added by the server owner.")
        await ctx.send(embed=embed)

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.MissingPermissions): await ctx.send(f"❌ You need the **Administrator** permission to use this command.")
        elif isinstance(error, commands.NotOwner): await ctx.send("❌ Only the **Server Owner** can use the `.adduser` command.")
        elif isinstance(error, commands.CheckFailure): await ctx.send("❌ You must have the **Administrator** permission or be explicitly added by the server owner to use this command.")
        elif isinstance(error, commands.CommandNotFound): pass 
        else: log_to_global(f"Unhandled command error: {error}")
    
    return bot

# --- Flask Web Server Setup ---

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('control_panel.html')

@app.route('/api/status')
def status():
    guild_list = []
    if bot_instance and bot_instance.is_ready():
        for guild in bot_instance.guilds:
            voice_client = guild.voice_client
            vc_status = f"Connected to {voice_client.channel.name}" if voice_client else "Not connected"
            guild_list.append({
                'id': guild.id,
                'name': guild.name,
                'member_count': guild.member_count,
                'vc_status': vc_status
            })
    
    return jsonify({
        'status': 'Running' if bot_instance and bot_instance.is_ready() else 'Offline',
        'guilds': guild_list,
        'logs': global_logs
    })

# --- NEW: Get Voice Channels Endpoint ---
@app.route('/api/get_voice_channels')
def get_voice_channels():
    if not bot_instance or not bot_instance.is_ready():
        return jsonify({"success": False, "message": "Bot is offline."}), 400

    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({"success": False, "message": "Guild ID is required."}), 400

    try:
        guild = bot_instance.get_guild(int(guild_id))
        if not guild:
            return jsonify({"success": False, "message": "Guild not found."}), 404
        
        channel_list = []
        
        # Get Voice Channels where bot can view and connect
        for channel in guild.voice_channels:
            perms = channel.permissions_for(guild.me)
            if perms.view_channel and perms.connect:
                channel_list.append({"id": channel.id, "name": f"🔊 {channel.name}"})
        
        # Sort by name for a clean list
        channel_list.sort(key=lambda x: x['name'].lower())
        
        return jsonify({"success": True, "channels": channel_list})

    except Exception as e:
        log_to_global(f"Error getting channels: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# --- Send Message Endpoint (Reverted) ---
@app.route('/api/send_message', methods=['POST'])
def send_message():
    if not bot_instance or not bot_instance.is_ready():
        return jsonify({"success": False, "message": "Bot is offline."}), 400
        
    data = request.json
    channel_id = data.get('channel_id')
    content = data.get('content')
    image_url = data.get('image_url')

    if not channel_id:
        return jsonify({"success": False, "message": "Channel ID is required."}), 400
    if not content and not image_url:
        return jsonify({"success": False, "message": "Message content or Image URL is required."}), 400

    async def fetch_and_send():
        """Asynchronously fetches the channel and sends the message."""
        try:
            channel_id_int = int(channel_id)
            channel = await bot_instance.fetch_channel(channel_id_int)

            if not channel.permissions_for(channel.guild.me).send_messages:
                 return False, "Bot does not have permission to send messages in that channel."

            embed_to_send = None
            if image_url:
                embed_to_send = discord.Embed(color=discord.Color.blue())
                embed_to_send.set_image(url=image_url)
            
            await channel.send(content=content if content else None, embed=embed_to_send if embed_to_send else None)
            
            log_to_global(f"Sent message to #{channel.name} in {channel.guild.name}.")
            return True, f"Message sent successfully to #{channel.name}."
            
        except discord.NotFound:
            log_to_global(f"Error: Channel ID {channel_id} not found.")
            return False, "Channel not found. Check the ID."
        except discord.Forbidden:
            log_to_global(f"Error: Bot missing permissions for Channel ID {channel_id}.")
            return False, "Bot does not have permission to send messages in that channel."
        except Exception as e:
            log_to_global(f"Error sending message: {e}")
            return False, f"An error occurred: {e}"

    # Safely run the asynchronous coroutine in the bot's event loop
    try:
        future = asyncio.run_coroutine_threadsafe(fetch_and_send(), bot_loop)
        success, message = future.result(timeout=10) 
        
        if success:
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "message": message}), 500

    except asyncio.TimeoutError:
         log_to_global(f"Error: Send message operation timed out for channel {channel_id}.")
         return jsonify({"success": False, "message": "Operation timed out."}), 500
    except Exception as e:
         log_to_global(f"Critical error running coroutine: {e}")
         return jsonify({"success": False, "message": f"Critical error: {e}"}), 500

# --- Owner Set VC Channel Endpoint ---
@app.route('/api/set_vc_channel', methods=['POST'])
def set_vc_channel_api():
    if not bot_instance or not bot_instance.is_ready():
        return jsonify({"success": False, "message": "Bot is offline."}), 400
    
    data = request.json
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')

    if not guild_id or not channel_id:
        return jsonify({"success": False, "message": "Guild ID and Channel ID are required."}), 400

    async def fetch_and_set():
        """Asynchronously validates the channel and saves the config."""
        try:
            target_id_int = int(channel_id)
            guild_id_str = str(guild_id) # Configs use string keys

            # 1. Validate the channel
            channel = await bot_instance.fetch_channel(target_id_int)
            if not isinstance(channel, discord.VoiceChannel):
                return False, f"Error: #{channel.name} is not a Voice Channel."

            # 2. Save the config
            if guild_id_str not in SERVER_CONFIGS:
                SERVER_CONFIGS[guild_id_str] = {'channel_id': target_id_int, 'allowed_users': []}
            else:
                SERVER_CONFIGS[guild_id_str]['channel_id'] = target_id_int
            
            save_configs(SERVER_CONFIGS)
            
            log_to_global(f"Owner set VC for {channel.guild.name} to {channel.name}.")
            return True, f"Success! Default VC for {channel.guild.name} set to {channel.name}."

        except discord.NotFound:
            return False, "Error: Channel ID not found."
        except ValueError:
            return False, "Error: Channel ID must be a number."
        except Exception as e:
            return False, f"An error occurred: {e}"

    # Run it in the bot's loop
    try:
        future = asyncio.run_coroutine_threadsafe(fetch_and_set(), bot_loop)
        success, message = future.result(timeout=10)
        if success:
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "message": message}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Critical error: {e}"}), 500

# --- Owner Force Join VC Endpoint (Patched for better errors) ---
@app.route('/api/force_join_vc', methods=['POST'])
def force_join_vc_api():
    if not bot_instance or not bot_instance.is_ready():
        return jsonify({"success": False, "message": "Bot is offline."}), 400
    
    data = request.json
    guild_id = data.get('guild_id')
    channel_id = data.get('channel_id')

    if not guild_id or not channel_id:
        return jsonify({"success": False, "message": "Guild ID and Channel ID are required."}), 400

    async def fetch_and_join():
        """Asynchronously fetches the guild/channel and joins."""
        try:
            target_id_int = int(channel_id)
            guild_id_int = int(guild_id)
        except ValueError:
            return False, "Error: Channel/Guild ID must be a number."

        try:
            guild = await bot_instance.fetch_guild(guild_id_int)
        except discord.NotFound:
            return False, f"Error: Guild ID {guild_id} not found. (Bot may not be in this server)"
        except discord.Forbidden:
             return False, "Error: Bot forbidden from fetching guild (permissions issue)."

        if not guild:
            return False, "Guild not found."

        try:
            channel = await bot_instance.fetch_channel(target_id_int)
        except discord.NotFound:
            return False, f"Error: Channel ID {channel_id} not found. (Check ID or bot's 'View Channel' permissions)"
        except discord.Forbidden:
            return False, "Error: Bot forbidden from fetching channel (permissions issue)."

        if not isinstance(channel, discord.VoiceChannel):
            return False, f"Error: {channel.name} is not a Voice Channel."

        if channel.guild.id != guild.id:
            return False, f"Error: Channel {channel.name} is not in the selected server {guild.name}."

        perms = channel.permissions_for(guild.me)
        if not perms.view_channel:
             return False, f"Error: Bot does not have 'View Channel' permission for {channel.name}."
        if not perms.connect:
            return False, f"Error: Bot does not have 'Connect' permission for {channel.name}."

        voice_client = guild.voice_client
        if voice_client:
            await voice_client.move_to(channel)
            return True, f"Moved to {channel.name}."
        else:
            await channel.connect()
            return True, f"Joined {channel.name}."

    # Run it in the bot's loop
    try:
        future = asyncio.run_coroutine_threadsafe(fetch_and_join(), bot_loop)
        success, message = future.result(timeout=10)
        if success:
            log_to_global(f"Owner forced join to {message} in guild {guild_id}")
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "message": message}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Critical error: {e}"}), 500

# --- Utility to Update .env File ---
def update_dotenv_token(new_token):
    """Reads .env content, updates DISCORD_TOKEN, and rewrites the file."""
    dotenv_path = os.path.join(os.getcwd(), '.env')
    
    if os.path.exists(dotenv_path):
        with open(dotenv_path, 'r') as f: lines = f.readlines()
    else: lines = ['# .env file generated by bot panel\n', 'DISCORD_TOKEN=YOUR_BOT_TOKEN_HERE\n']

    updated_lines = []
    token_updated = False
    
    for line in lines:
        if line.startswith('DISCORD_TOKEN='):
            updated_lines.append(f'DISCORD_TOKEN={new_token}\n')
            token_updated = True
        else:
            updated_lines.append(line)
            
    if not token_updated: updated_lines.append(f'\nDISCORD_TOKEN={new_token}\n')

    try:
        with open(dotenv_path, 'w') as f: f.writelines(updated_lines)
        log_to_global("💾 Successfully updated DISCORD_TOKEN in the .env file.")
    except Exception as e:
        log_to_global(f"❌ ERROR: Failed to write to .env file: {e}")

@app.route('/api/restart', methods=['POST'])
def restart_bot_api():
    global bot_thread
    data = request.json
    new_token = data.get('token')
    
    if not new_token:
        return jsonify({"success": False, "message": "No new token provided."}), 400
        
    stop_bot_client()
    
    bot_thread = threading.Thread(target=lambda: run_bot_client(new_token), daemon=True)
    bot_thread.start()
    
    update_dotenv_token(new_token)
    
    log_to_global("🔄 Bot restart initiated with new token...")
    return jsonify({"success": True, "message": "Bot restart initiated. Check logs for status."})

# --- Running Bot and Web Server Functions ---
def stop_bot_client():
    """Stops the Discord bot safely."""
    global bot_thread, bot_instance, bot_loop
    if bot_instance and bot_loop:
        try:
            future = asyncio.run_coroutine_threadsafe(bot_instance.close(), bot_loop)
            future.result(5)
            bot_thread.join(timeout=5)
        except Exception as e:
            log_to_global(f"🛑 Error stopping bot: {e}")
        finally:
            bot_thread = None
            bot_instance = None
            bot_loop = None
            log_to_global("🛑 Bot client stopped.")
            return True
    return True

def run_bot_client(token):
    """Runs the Discord bot client in its own thread."""
    global bot_instance
    
    if not token or 'YOUR_BOT_TOKEN_HERE' in token:
        log_to_global("❌ ERROR: DISCORD_TOKEN not configured. Bot will not run.")
        return

    bot_instance = get_bot_client(token)
    try:
        bot_instance.run(token)
    except Exception as e:
        log_to_global(f"FATAL BOT ERROR: {e}")

def run_web_server():
    """Runs the Flask web server."""
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port) 

if __name__ == '__main__':
    initial_token = os.getenv('DISCORD_TOKEN')
    bot_thread = threading.Thread(target=lambda: run_bot_client(initial_token), daemon=True)
    bot_thread.start()
    run_web_server()
