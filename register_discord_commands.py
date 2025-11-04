#!/usr/bin/env python3
"""
Discord Slash Command Registration Script

This script registers slash commands with Discord.
Run once after setting up your bot, or whenever you need to update commands.

Usage:
    python register_discord_commands.py

Requires:
    - DISCORD_BOT_TOKEN environment variable
    - DISCORD_APPLICATION_ID environment variable (or CLIENT_ID from OAuth URL)
"""

import os
import requests
import json
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get bot token from environment
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ ERROR: DISCORD_BOT_TOKEN environment variable not set")
    print("   Set it with: export DISCORD_BOT_TOKEN='your-token-here'")
    sys.exit(1)

# Get application ID from environment or extract from OAuth URL
APPLICATION_ID = os.environ.get("DISCORD_APPLICATION_ID")
if not APPLICATION_ID:
    # Try to extract from OAuth URL if provided
    oauth_url = os.environ.get("DISCORD_OAUTH_URL")
    if oauth_url:
        try:
            # Extract client_id from OAuth URL
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(oauth_url)
            params = parse_qs(parsed.query)
            APPLICATION_ID = params.get("client_id", [None])[0]
        except:
            pass
    
    if not APPLICATION_ID:
        print("❌ ERROR: DISCORD_APPLICATION_ID environment variable not set")
        print("   Set it with: export DISCORD_APPLICATION_ID='your-application-id'")
        print("   Or set DISCORD_OAUTH_URL and we'll extract it")
        sys.exit(1)

# Discord API base URL
DISCORD_API = "https://discord.com/api/v10"

# Headers for Discord API requests
headers = {
    "Authorization": f"Bot {BOT_TOKEN}",
    "Content-Type": "application/json"
}

# Define slash commands
commands = [
    {
        "name": "dev-mode",
        "description": "Enable or disable dev mode (uses dev webhook, bypasses filters)",
        "options": [
            {
                "name": "enabled",
                "description": "Enable dev mode?",
                "type": 5,  # BOOLEAN
                "required": True
            }
        ]
    },
    {
        "name": "test-mode",
        "description": "Enable test mode (bypasses time/weekend filters for testing)"
    },
    {
        "name": "status",
        "description": "Show current system status (dev mode, filters, etc.)"
    }
]

def register_commands():
    """Register slash commands with Discord"""
    url = f"{DISCORD_API}/applications/{APPLICATION_ID}/commands"
    
    print(f"📡 Registering {len(commands)} slash commands...")
    print(f"   Application ID: {APPLICATION_ID}")
    print(f"   URL: {url}\n")
    
    try:
        response = requests.put(url, headers=headers, json=commands)
        
        if response.status_code == 200:
            registered = response.json()
            print("✅ Successfully registered commands:")
            for cmd in registered:
                print(f"   • /{cmd['name']} - {cmd['description']}")
            print("\n💡 Commands are now available in your Discord server!")
            print("   Note: Global commands can take up to 1 hour to appear")
            print("   For immediate testing, use guild commands (register to specific server)")
            return True
        else:
            print(f"❌ Failed to register commands: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error registering commands: {e}")
        return False

def list_commands():
    """List currently registered commands"""
    url = f"{DISCORD_API}/applications/{APPLICATION_ID}/commands"
    
    try:
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            commands = response.json()
            print(f"📋 Currently registered commands ({len(commands)}):")
            for cmd in commands:
                print(f"   • /{cmd['name']} - {cmd['description']}")
            return True
        else:
            print(f"❌ Failed to list commands: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error listing commands: {e}")
        return False

def delete_all_commands():
    """Delete all registered commands (for testing)"""
    url = f"{DISCORD_API}/applications/{APPLICATION_ID}/commands"
    
    try:
        response = requests.put(url, headers=headers, json=[])
        
        if response.status_code == 200:
            print("✅ All commands deleted")
            return True
        else:
            print(f"❌ Failed to delete commands: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error deleting commands: {e}")
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Register Discord slash commands")
    parser.add_argument("--list", action="store_true", help="List currently registered commands")
    parser.add_argument("--delete", action="store_true", help="Delete all commands")
    
    args = parser.parse_args()
    
    if args.list:
        list_commands()
    elif args.delete:
        confirm = input("⚠️  Are you sure you want to delete all commands? (yes/no): ")
        if confirm.lower() == "yes":
            delete_all_commands()
        else:
            print("Cancelled")
    else:
        register_commands()

