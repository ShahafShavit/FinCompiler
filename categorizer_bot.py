# import discord
# from discord.ext import commands
# import datetime
# import asyncio
#
#
# import config
#
# TOKEN = config.DISCORD_BOT_TOKEN
# USER_ID = config.DISCORD_USER_ID
#
#
# class DiscordBot:
#     def __init__(self, token, user_id):
#         intents = discord.Intents.default()
#         intents.messages = True
#         intents.message_content = True
#         self.bot = commands.Bot(command_prefix='!', intents=intents)
#         self.token = token
#         self.user_id = user_id
#         self.response_future = None
#
#         @self.bot.event
#         async def on_ready():
#             print(f'Bot connected as {self.bot.user}')
#
#         @self.bot.event
#         async def on_message(message):
#             print(f"Message from {message.author}: {message.content}")
#             if message.author.id == self.user_id:
#                 print("Message is from the expected user")
#                 if self.response_future is not None and not self.response_future.done():
#                     print("Setting the result for the future")
#                     self.response_future.set_result(message.content)
#
#     async def send(self, message_text):
#         try:
#             user = await self.bot.fetch_user(self.user_id)
#             await user.send(message_text)
#         except discord.errors.Forbidden:
#             print("Error: Cannot send messages to this user. Please check the user's privacy settings.")
#         except discord.errors.HTTPException as e:
#             print(f"HTTP error occurred: {e}")
#         except Exception as e:
#             print(f"An unexpected error occurred: {e}")
#
#     async def receive(self):
#         self.response_future = self.bot.loop.create_future()
#         print(f"Waiting for response, future: {self.response_future}")
#         response = await self.response_future
#         print(f"Response received: {response}")
#         return response
#
#     def run(self):
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
#         loop.create_task(self.bot.start(self.token))
#         loop.run_forever()
#
#     def stop(self):
#         asyncio.get_event_loop().stop()
