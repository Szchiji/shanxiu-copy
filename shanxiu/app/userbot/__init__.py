"""Userbot (小号) support: listen with a real Telegram account so messages sent by OTHER
bots in a group (never delivered to bots by the Bot API) can be fed into group sync.

The userbot is strictly read-only: it never sends, joins, or marks anything as read.
All sending is done by the main bot.
"""
