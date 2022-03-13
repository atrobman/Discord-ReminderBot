import discord
import youtube_dl
from discord.ext import commands
import os
import asyncio
from async_timeout import timeout
import itertools
import random
import functools
import math
from cogs import utils
from datetime import date, datetime

def setup(bot):
    bot.add_cog(Music(bot))
    bot.add_cog(Permissions(bot))

class VoiceError(Exception):
    pass

class YTDLError(Exception):
    pass

class SongError(Exception):
    pass

def emb_color(query):

    if query in ['Now playing']:
        return discord.Color.blurple().value

    elif query in ['Queued']:
        return discord.Color.from_rgb(188, 191, 61).value
    
    elif query in ['Removed', 'Skipped', 'Error']:
        return discord.Color.dark_red().value
    else:
        return discord.Color.from_rgb(255, 255, 255).value

class DoNothingLogger(object):
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass

class PermissionsParser:

    def __init__(self, force_skip: bool = True, remove: bool = True, move: bool = True, play_next: bool = True, play: bool = True, pause_resume: bool = True, playlists: bool = True, shuffle: bool = True, leave: bool = True):
        
        self.force_skip = force_skip
        self.remove = remove
        self.move = move
        self.play_next = play_next
        self.play = play
        self.pause_resume = pause_resume
        self.playlists = playlists
        self.shuffle = shuffle
        self.leave = leave

    @classmethod
    def parse(cls, perms):

        force_skip =   bool(perms & (1 << 0))
        remove =       bool(perms & (1 << 1))
        move =         bool(perms & (1 << 2))
        play_next =    bool(perms & (1 << 3))
        play =         bool(perms & (1 << 4))
        pause_resume = bool(perms & (1 << 5))
        playlists =    bool(perms & (1 << 6))
        shuffle =      bool(perms & (1 << 7))
        leave =        bool(perms & (1 << 8))

        return cls(force_skip, remove, move, play_next, play, pause_resume, playlists, shuffle, leave)

    def to_int(self):
        ret = 0
        ret |= self.force_skip << 0
        ret |= self.remove << 1
        ret |= self.move << 2
        ret |= self.play_next << 3
        ret |= self.play << 4
        ret |= self.pause_resume << 5
        ret |= self.playlists << 6
        ret |= self.shuffle << 7
        ret |= self.leave << 8

        return ret

    def __str__(self):

        msg  = '`1. Force Skip: ' + f'{self.force_skip}'.rjust(25 - len('1. Force Skip: ')) + '`\n'
        msg += '`2. Remove: ' + f'{self.remove}'.rjust(25 - len('2. Remove: ')) + '`\n'
        msg += '`3. Move: ' + f'{self.move}'.rjust(25 - len('3. Move: ')) + '`\n'
        msg += '`4. Playnext: ' + f'{self.play_next}'.rjust(25 - len('4. Playnext: ')) + '`\n'
        msg += '`5️. Play: ' + f'{self.play}'.rjust(26 - len('5️. Play: ')) + '`\n'
        msg += '`6️. Pause/Resume: ' + f'{self.pause_resume}'.rjust(26 - len('6️. Pause/Resume: ')) + '`\n'
        msg += '`7️. Playlists: ' + f'{self.playlists}'.rjust(26 - len('7️. Playlists: ')) + '`\n'
        msg += '`8️. Shuffle: ' + f'{self.shuffle}'.rjust(26 - len('8️. Shuffle: ')) + '`\n'
        msg += '`9️. Leave: ' + f'{self.leave}'.rjust(26 - len('9️. Leave: ')) + '`\n'

        return msg

class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': False,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
        'nocachedir': True,
        'logger': DoNothingLogger()
        }

    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -loglevel fatal -nostats',
        'options': '-vn',
    }

    ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx: commands.Context, source: discord.FFmpegPCMAudio, *, data: dict):
        super().__init__(source, 1)

        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data

        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration_raw = int(data.get('duration'))
        self.duration = self.parse_duration(self.duration_raw)
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.dislikes = data.get('dislike_count')
        self.stream_url = data.get('url')

        self.time_played = 0.0
        self.last_time_updated = None

    def __str__(self):
        return f'**{self.title}** by **{self.uploader}**'

    @classmethod
    async def create_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        try:
            data = await loop.run_in_executor(None, partial)
        except youtube_dl.utils.DownloadError as e:
            raise YTDLError(str(e))

        if data is None:
            raise YTDLError(f'Couldn\'t find anything that matches `{search}`')

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError(f'Couldn\'t find anything that matches `{search}`')

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError(f'Couldn\'t fetch `{webpage_url}`')

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError(f'Couldn\'t retrieve any matches for `{webpage_url}`')

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **cls.FFMPEG_OPTIONS), data=info)

    @classmethod
    async def search_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None, bot):
        channel = ctx.channel
        loop = loop or asyncio.get_event_loop()

        cls.search_query = '%s%s:%s' % ('ytsearch', 10, ''.join(search))

        partial = functools.partial(cls.ytdl.extract_info, cls.search_query, download=False, process=False)
        info = await loop.run_in_executor(None, partial)

        cls.search = {}
        cls.search["title"] = f'Search results for:\n**{search}**'
        cls.search["type"] = 'rich'
        cls.search["color"] = emb_color('search')
        cls.search["author"] = {'name': f'{ctx.author.name}', 'url': f'{ctx.author.avatar_url}', 'icon_url': f'{ctx.author.avatar_url}'}
        
        lst = []
        url_lst = []
        for i, e in enumerate(info['entries']):
            VId = e.get('id')
            VUrl = 'https://www.youtube.com/watch?v=%s' % (VId)
            lst.append(f'`{i + 1}.` [{e.get("title")}]({VUrl})\n')
            url_lst.append(VUrl)

        lst.append('\n**Type a number to make a choice, Type `cancel` to exit**')
        cls.search["description"] = "\n".join(lst)

        em = discord.Embed.from_dict(cls.search)
        msg = await ctx.send(embed=em)

        def check(msg):
            return msg.content.isdigit() == True and msg.channel == channel or msg.content == 'cancel' or msg.content == 'Cancel'
        
        try:
            m = await bot.wait_for('message', check=check, timeout=45.0)
        except asyncio.TimeoutError:
            rtrn = 'timeout'

        else:
            if m.content.isdigit() == True:
                sel = int(m.content)
                if 0 < sel <= min(10, len(url_lst)):
                    VUrl = url_lst[sel - 1]
                    partial = functools.partial(cls.ytdl.extract_info, VUrl, download=False)
                    data = await loop.run_in_executor(None, partial)
                    rtrn = cls(ctx, discord.FFmpegPCMAudio(data['url'], **cls.FFMPEG_OPTIONS), data=data)
                else:
                    rtrn = 'sel_invalid'
            elif m.content == 'cancel':
                rtrn = 'cancel'
            else:
                rtrn = 'sel_invalid'
        
        await ctx.message.delete()
        await m.delete()
        await msg.delete()
        return rtrn

    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = []
        if days > 0:
            duration.append(f'{days} days')
        if hours > 0:
            duration.append(f'{hours} hours')
        if minutes > 0:
            duration.append(f'{minutes} minutes')
        if seconds >= 0:
            duration.append(f'{seconds} seconds')

        return ', '.join(duration)

class Song:
    __slots__ = ('source', 'requester')

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self, title='Now playing', show_progress=False, show_eta=False, show_eta_ctx=None):
        if show_progress:
            num_segments = 20

            seg_time = self.source.duration_raw / num_segments
            before_segments = math.floor(self.source.time_played / seg_time)
            p_bar = "`" + "▬" * before_segments + "🔘" + "▬" * (num_segments - 1 - before_segments) + "`"

            embed = (discord.Embed(title=title,
                                description=f'[{self.source.title}]({self.source.url})\n\n{p_bar}\n',
                                color=emb_color(title))
                    .add_field(name='Duration', value=f"{YTDLSource.parse_duration(round(self.source.time_played))} / {self.source.duration}", inline=False)
                    .add_field(name='Requested by', value=self.requester.mention)
                    .add_field(name='Uploader', value=f'[{self.source.uploader}]({self.source.uploader_url})')
                    .set_thumbnail(url=self.source.thumbnail))
        else:
            embed = (discord.Embed(title=title,
                                description=f'[{self.source.title}]({self.source.url})',
                                color=emb_color(title))
                    .add_field(name='Duration', value=f"{self.source.duration}", inline=False)
                    .add_field(name='Requested by', value=self.requester.mention)
                    .add_field(name='Uploader', value=f'[{self.source.uploader}]({self.source.uploader_url})')
                    .set_thumbnail(url=self.source.thumbnail))

        if show_eta:
            if not show_eta_ctx:
                raise SongError("*show_eta* set to True but no context was provided")
            embed.set_footer(text=f"ETA: {YTDLSource.parse_duration(sum(x.source.duration_raw for x in show_eta_ctx.voice_state.queue) + show_eta_ctx.voice_state.current_song.source.duration_raw)}")
        
        return embed

class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self._queue)

    def remove(self, index: int):
        del self._queue[index]
        

class MusicManager:
    def __init__(self, bot, ctx):
        self.ctx= ctx
        self.bot = bot
        self.queue = SongQueue()
        self.current_song = None
        self.next = asyncio.Event()
        self.voice_client = None
        self.exists = True
        self.player = self.bot.loop.create_task(self.music_player_task())
        self.skip_votes = set()

    def __del__(self):
        self.player.cancel()

    async def music_player_task(self):
        while True:
            self.next.clear()
            self.current_song = None

            try:
                async with timeout(600): #wait 10 minutes for a song to show up in queue
                    self.current_song = await self.queue.get()
            except asyncio.TimeoutError:
                await self.ctx.send(f"No activity for `10 minutes`. Leaving **{self.voice_client.channel.name}**")
                self.bot.loop.create_task(self.stop())
                self.exists = False
                return

            self.voice_client.play(self.current_song.source, after=self.play_next_song)
            self.current_song.source.last_time_updated = datetime.utcnow()
            await self.ctx.send(embed=self.current_song.create_embed())

            await self.next.wait()

    async def stop(self):
        self.queue.clear()

        if self.voice_client is not None:
            await self.voice_client.disconnect()
            self.voice_client = None
            
    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))
        
        td = datetime.utcnow() - self.current_song.source.last_time_updated
        self.current_song.source.time_played += td.total_seconds()
        self.next.set()
        self.skip_votes.clear()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice_client.stop()

    @property
    def is_playing(self):
        return self.voice_client and self.current_song

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    def get_voice_state(self, ctx):
        player = self.players.get(ctx.guild.id)
        if not player or not player.exists:
            player = MusicManager(self.bot, ctx)
            self.players[ctx.guild.id] = player

        return player

    def cog_unload(self):
        for player in self.players.values():
            self.bot.loop.create_task(player.stop())

    def cog_check(self, ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage("This command can't be used in DM channels")

        return True

    async def cog_before_invoke(self, ctx):
        ctx.voice_state = self.get_voice_state(ctx)

        for user_role in ctx.author.roles[::-1]:
            self.bot.cursor.execute("SELECT * FROM perms WHERE RoleID=?", (user_role.id,))
            ret = self.bot.cursor.fetchone()

            if ret:
                ctx.user_permissions = PermissionsParser.parse(ret[1])
                return
        
        ctx.user_permissions = PermissionsParser()

    @commands.command(pass_context=True, name="join", aliases=["summon, start"])
    async def join(self, ctx):
        '''
        Join the user's Voice Channel
        '''

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send('You are not connected to any voice channel.')
            return

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                await ctx.send('Bot is already in a voice channel.')
                return

        dest = ctx.author.voice.channel
        if ctx.voice_state.voice_client:
            await ctx.voice_state.voice_client.move_to(dest)
            return
        
        ctx.voice_state.voice_client = await dest.connect()
        await ctx.send(f"Joined **{dest.name}**")

    @commands.command(pass_context=True, name="leave", aliases=["quit", "exit"])
    async def leave(self, ctx):
        '''
        Clear the queue and leave the Voice Channel
        '''

        if not ctx.voice_state.voice_client:
            await ctx.send('Not connected to a voice channel')
            return

        if ctx.user_permissions.leave or len(ctx.voice_state.voice_client.channel.members) == 2:
            await ctx.voice_state.stop()
            del self.players[ctx.guild.id]
        else:
            await ctx.send('ERROR: Missing permission `leave`')

    @commands.command(pass_context=True, name="now", aliases=["np", "current", "n"])
    async def now(self, ctx):
        '''
        Display the currently playing song, if there is one
        '''

        if ctx.voice_state.current_song:
            if ctx.voice_state.voice_client.is_playing():
                td = datetime.utcnow() - ctx.voice_state.current_song.source.last_time_updated
                ctx.voice_state.current_song.source.last_time_updated = datetime.utcnow()
                ctx.voice_state.current_song.source.time_played += td.total_seconds()
            await ctx.send(embed=ctx.voice_state.current_song.create_embed(show_progress=True))
        else:
            await ctx.send(f"Nothing is playing right now")

    @commands.command(pass_context=True, name="pause")
    async def pause(self, ctx):
        '''
        Pause the player
        '''

        if ctx.voice_state.is_playing:
            if ctx.user_permissions.pause_resume:
                if ctx.voice_state.voice_client.is_playing():
                    td = datetime.utcnow() - ctx.voice_state.current_song.source.last_time_updated
                    ctx.voice_state.current_song.source.last_time_updated = datetime.utcnow()
                    ctx.voice_state.current_song.source.time_played += td.total_seconds()
                    ctx.voice_state.voice_client.pause()
                    await ctx.send("Player paused")
                else:
                    await ctx.send("Player already paused")
            else:
                await ctx.send(f"ERROR: Missing permission `pause_resume`")
        else:
            await ctx.send("Nothing is playing right now")
    
    @commands.command(pass_context=True, name="resume", aliases=["continue", "start"])
    async def resume(self, ctx):
        '''
        Unpause the player
        '''

        if ctx.voice_state.is_playing:
            if ctx.user_permissions.pause_resume:
                if ctx.voice_state.voice_client.is_paused():
                    ctx.voice_state.voice_client.resume()
                    ctx.voice_state.current_song.source.last_time_updated = datetime.utcnow()
                    await ctx.send("Player resumed")
                else:
                    await ctx.send("Player is not paused")
            else:
                await ctx.send(f"ERROR: Missing permission `pause_resume`")
        else:
            await ctx.send("Nothing is playing right now")

    @commands.command(pass_context=True, name="skip")
    async def skip(self, ctx):
        '''
        Skip the currently playing song
        '''

        if not ctx.voice_state.is_playing:
            await ctx.send("Not playing anything right now")
            return

        voter = ctx.message.author
        if voter == ctx.voice_state.current_song.requester:
            await ctx.send(embed=ctx.voice_state.current_song.create_embed(title="Skipped", show_progress=False))
            ctx.voice_state.skip()
            return
        
        if voter.id not in ctx.voice_state.skip_votes:
            ctx.voice_state.skip_votes.add(voter.id)
            total_votes = len(ctx.voice_state.skip_votes)

            if total_votes >= 3:
                await ctx.send(embed=ctx.voice_state.current_song.create_embed(title="Skipped", show_progress=False))
                ctx.voice_state.skip()
            else:
                await ctx.send(f'Skip vote added, currently at **{total_votes}/3**')
        
        else:
            await ctx.send("You have already voted to skip this song")

    @commands.command(pass_context=True, name="force_skip", aliases=["forceskip", "fs"])
    async def force_skip(self, ctx):

        if not ctx.voice_state.is_playing:
            await ctx.send("Not playing anything right now")
            return

        if ctx.user_permissions.force_skip:
            await ctx.send(embed=ctx.voice_state.current_song.create_embed(title="Skipped", show_progress=False))
            ctx.voice_state.skip()
        else:
            await ctx.send("ERROR: Missing permission `force_skip`")

    @commands.command(pass_context=True, name="queue", aliases=["q", "list", "songs", "playlist"])
    async def queue(self, ctx, page: int = 1):
        '''
        Display the queue. If there are multiple pages, you can specify page number.
        '''

        if len(ctx.voice_state.queue) == 0:
            return await ctx.send("Queue is empty")

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.queue) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''

        if ctx.voice_state.current_song:
            queue += f'__Now Playing__\n'
            queue += f'[**{ctx.voice_state.current_song.source.title}**]({ctx.voice_state.current_song.source.url}) | `{ctx.voice_state.current_song.source.duration} Requested by: {ctx.voice_state.current_song.source.requester.name}#{ctx.voice_state.current_song.source.requester.discriminator}`\n\n'

        queue += f'__Up Next:__\n'
        for i, song in enumerate(ctx.voice_state.queue[start:end], start=start):
            queue += f'`{i + 1}.` [{song.source.title}]({song.source.url}) | `{song.source.duration} Requested by: {song.source.requester.name}#{song.source.requester.discriminator}`\n'

        embed = (discord.Embed(description=queue)
                    .set_footer(text=f'Viewing page {page}/{pages}  |  Queue Length: {YTDLSource.parse_duration(sum(x.source.duration_raw for x in ctx.voice_state.queue))}'))
        await ctx.send(embed=embed)

    @commands.command(pass_context=True, name="shuffle")
    async def shuffle(self, ctx):
        '''
        Shuffle the queue randomly
        '''

        if ctx.user_permissions.shuffle:
            if len(ctx.voice_state.queue) == 0:
                return await ctx.send("Queue is empty")
            
            ctx.voice_state.queue.shuffle()
            await ctx.send("Queue shuffled")
        else:
            await ctx.send("ERROR: Missing permission `shuffle`")

    @commands.command(pass_context=True, name="remove", aliases=["r", "d", "delete", "del", "rm", "rem"])
    async def remove(self, ctx, index: int):
        '''
        Remove the song at given index
        '''

        if len(ctx.voice_state.queue) == 0:
            return await ctx.send("Queue is empty")

        if index > 0 and index <= len(ctx.voice_state.queue):
            song_to_del = ctx.voice_state.queue[index - 1]

            if song_to_del.requester == ctx.author or ctx.user_permissions.remove:
                await ctx.send(embed=song_to_del.create_embed(title="Removed"))
                ctx.voice_state.queue.remove(index - 1)
            else:
                await ctx.send("ERROR: Missing permission `remove`")
        else:
            await ctx.send("Index out of range")

    @commands.command(pass_context=True, name="clear")
    async def clear(self, ctx):
        '''
        Empty the queue
        '''

        if ctx.user_permissions.force_skip:
            if len(ctx.voice_state.queue) == 0:
                return await ctx.send("Queue is empty")

            ctx.voice_state.queue.clear()
            await ctx.send("Queue cleared")
        else:
            await ctx.send("ERROR: Missing permission `force_skip`")
    
    @commands.command(pass_context=True, name="move", aliases=["m",])
    async def move(self, ctx, from_index: int, to_index: int):
        '''
        Move a song from **a** to **b**
        
        Note: This does not swap, it removes the song and inserts it at the new position
        '''

        if ctx.user_permissions.move:
            if len(ctx.voice_state.queue) < 2:
                return await ctx.send("Queue is too short for moving")

            if 0 < from_index <= len(ctx.voice_state.queue) and 0 < to_index <= len(ctx.voice_state.queue):
                temp = ctx.voice_state.queue._queue[from_index - 1]
                del ctx.voice_state.queue._queue[from_index - 1]
                ctx.voice_state.queue._queue.insert(to_index - 1, temp)
                await ctx.send(f"Moved song from position `{from_index}`` to position `{to_index}`")
            else:
                await ctx.send(f"Invalid indices")
        else:
            await ctx.send("ERROR: Missing permission `move`")

    @commands.command(pass_context=True, name="play", aliases=["p", "pl"])
    async def play(self, ctx, *, search: str):
        '''
        Add a song to the queue. Works with any youtube-dl compatible site, and also works with Playlists.

        If a link is not given, will search and play the first result found on Youtube.
        '''

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send('You are not connected to any voice channel.')
            await ctx.message.delete()
            return

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                await ctx.send('Bot is already in a voice channel.')
                await ctx.message.delete()
                return

        if not ctx.voice_state.voice_client:
            await ctx.invoke(self.join)

        if ctx.user_permissions.play:
            if '?list=' in search:
                if ctx.user_permissions.playlists:
                    m = await ctx.send(":clock2: One moment! Processing playlists can take a bit... :clock2:")
                    async with ctx.typing():
                        playlist, playlistTitle = self._playlist(search)
                        songlist = []
                        for _title, _link in playlist.items():
                            try:
                                source = await YTDLSource.create_source(ctx, _link, loop=self.bot.loop)
                            except YTDLError as e:
                                emb = discord.Embed(title="An Error Occurred", description=str(e), colour=emb_color('Error'))
                                await ctx.send(embed=emb)
                            else:
                                if source.duration_raw >= 15600: #4 hours and 20 minutes
                                    emb = discord.Embed(title="An Error Occurred", description=f"**{source.title}** is too long! Please keep song requests under 4 hours and 20 minutes", colour=emb_color('Error'))
                                    await ctx.send(embed=emb)
                                    return
                                song = Song(source)
                        
                                songlist.append(song)

                    await m.delete()
                    if ctx.voice_state.current_song:
                        emb = (discord.Embed(title="Queued",
                                            description=f'[{playlistTitle}]({search})',
                                            color=emb_color("Queued"))
                                .add_field(name='Requested by', value=ctx.author.mention)
                                .set_thumbnail(url=songlist[0].source.thumbnail))
                        await ctx.send(embed=emb)
                    for song in songlist:
                        await ctx.voice_state.queue.put(song)
                else:
                    await ctx.send("ERROR: Missing permission `playlists`")
            else:
                async with ctx.typing():
                    try:
                        source = await YTDLSource.create_source(ctx, search.strip("<>"), loop=self.bot.loop)
                    except YTDLError as e:
                        emb = discord.Embed(title="An Error Occurred", description=str(e), colour=emb_color('Error'))
                        await ctx.send(embed=emb)
                    else:
                        if source.duration_raw >= 15600: #4 hours and 20 minutes
                            emb = discord.Embed(title="An Error Occurred", description=f"**{source.title}** is too long! Please keep song requests under 4 hours and 20 minutes", colour=emb_color('Error'))
                            await ctx.send(embed=emb)
                            await ctx.message.delete()
                            return
                        song = Song(source)
                        
                        if ctx.voice_state.current_song:
                            await ctx.send(embed=song.create_embed(title='Queued', show_eta=True, show_eta_ctx=ctx))
                        await ctx.voice_state.queue.put(song)
        else:
            await ctx.send("ERROR: Missing permission `play`")

        await ctx.message.delete()

    @commands.command(pass_context=True, name="playnext", aliases=["pn",])
    async def playnext(self, ctx, *, search: str):
        '''
        Add a song to the top of the queue. Works with any youtube-dl compatible site, and also works with Playlists.

        If a link is not given, will search and play the first result found on Youtube.
        '''

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send('You are not connected to any voice channel.')
            await ctx.message.delete()
            return

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                await ctx.send('Bot is already in a voice channel.')
                await ctx.message.delete()
                return

        if not ctx.voice_state.voice_client:
            await ctx.invoke(self.join)

        if "?list=" in search:
            await ctx.send("Sorry! You can't skip the queue with playlists")

        if len(ctx.voice_state.queue) == 0 or ctx.user_permissions.play_next:
            if ctx.user_permissions.play:
                async with ctx.typing():
                    try:
                        source = await YTDLSource.create_source(ctx, search.strip("<>"), loop=self.bot.loop)
                    except YTDLError as e:
                        emb = discord.Embed(title="An Error Occurred", description=str(e), colour=emb_color('Error'))
                        await ctx.send(embed=emb)
                    else:
                        if source.duration_raw >= 15600: #4 hours and 20 minutes
                            emb = discord.Embed(title="An Error Occurred", description=f"**{source.title}** is too long! Please keep song requests under 4 hours and 20 minutes", colour=emb_color('Error'))
                            await ctx.send(embed=emb)
                            await ctx.message.delete()
                            return
                        song = Song(source)
                        
                        if ctx.voice_state.current_song:
                            ctx.voice_state.queue._queue.appendleft(song)
                            await ctx.send(embed=song.create_embed(title='Queued', show_eta=True, show_eta_ctx=ctx))
                        else:
                            await ctx.voice_state.queue.put(song)
            else:
                await ctx.send("ERROR: Missing permission `play`")
        else:
            await ctx.send("ERROR: Missing permission `play_next`")

        await ctx.message.delete()

    @commands.command(pass_context=True, name="search", aliases=["s",])
    async def search(self, ctx, *, search: str):
        '''
        Search on Youtube for a song to add to the queue
        '''

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send('You are not connected to any voice channel.')
            return

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                await ctx.send('Bot is already in a voice channel.')
                return

        if not ctx.voice_state.voice_client:
            await ctx.invoke(self.join)

        if ctx.user_permissions.play:
            async with ctx.typing():
                try:
                    source = await YTDLSource.search_source(ctx, search.strip("<>"), loop=self.bot.loop, bot=self.bot)
                except YTDLError as e:
                    emb = discord.Embed(title="An Error Occurred", description=str(e), colour=emb_color('Error'))
                    await ctx.send(embed=emb)
                else:
                    if source == 'sel_invalid':
                        await ctx.send('Invalid Selection', delete_after=15.0)
                    elif source == 'cancel':
                        await ctx.send('Selection canceled', delete_after=15.0)
                    elif source == 'timeout':
                        await ctx.send('Selection timed out', delete_after=15.0)
                    else:
                        if source.duration_raw >= 15600: #4 hours and 20 minutes
                            emb = discord.Embed(title="An Error Occurred", description=f"**{source.title}** is too long! Please keep song requests under 4 hours and 20 minutes", colour=emb_color('Error'))
                            await ctx.send(embed=emb)
                            return
                        song = Song(source)
                        await ctx.voice_state.queue.put(song)
                        if ctx.voice_state.current_song:
                            await ctx.send(embed=song.create_embed(title='Queued', show_eta=True, show_eta_ctx=ctx))
        else:
            await ctx.send("ERROR: Missing permission `play`")

    def _playlist(self, search: str):

        with YTDLSource.ytdl as ydl:
            playlist_dict = ydl.extract_info(search.strip("<>"), download=False)

            playlistTitle = playlist_dict['title']

            playlist = dict()
            for video in playlist_dict['entries']:

                if not video:
                    raise YTDLError(f'Failed to download all songs in playlist ({search})')
                
                playlist[video.get('title')] = 'https://www.youtube.com/watch?v=' + video.get('id')
        
        return playlist, playlistTitle

class Permissions(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(pass_context=True, name="add_role", aliases=["ar", "addrole"], hidden=True)
    @commands.has_permissions(manage_guild=True)
    async def add_role(self, ctx, role : utils.AdvRoleConverter):
        '''
        Set role permissions for a role and add it to the database
        '''

        if role is None:
            await ctx.send("Role not found")
            return

        self.bot.cursor.execute("SELECT RoleID FROM perms")

        if (role.id,) not in self.bot.cursor.fetchall():
            perms = PermissionsParser()
            not_finished = True
            cancel = False
            emojis = ('1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '✅', '❌')

            def check(reaction, user):
                if user is None or user.id != ctx.author.id:
                    return False

                for emoji in emojis:
                    if reaction.emoji == emoji:
                        return True

                return False
            
            m = await ctx.send('`' + f'Role: {role.name}'.center(25) + '`\n' + str(perms))
            for emoji in emojis:
                try:
                    await m.add_reaction(emoji)
                except:
                    pass
            
            while not_finished:
                try:
                    react, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
                    if react.message.id == m.id:
                        if react is None:
                            not_finished = False
                            try:
                                await m.clear_reactions()
                            except:
                                pass
                        
                        else:
                            try:
                                await m.remove_reaction(react.emoji, user)
                            except:
                                pass

                            if react.emoji == '1️⃣':
                                perms.force_skip = not perms.force_skip
                            elif react.emoji == '2️⃣':
                                perms.remove = not perms.remove
                            elif react.emoji == '3️⃣':
                                perms.move = not perms.move
                            elif react.emoji == '4️⃣':
                                perms.play_next = not perms.play_next
                            elif react.emoji == '5️⃣':
                                perms.play = not perms.play
                            elif react.emoji == '6️⃣':
                                perms.pause_resume = not perms.pause_resume
                            elif react.emoji == '7️⃣':
                                perms.playlists = not perms.playlists
                            elif react.emoji == '8️⃣':
                                perms.shuffle = not perms.shuffle
                            elif react.emoji == '9️⃣':
                                perms.leave = not perms.leave
                            elif react.emoji == '✅':
                                not_finished = False
                                await m.clear_reactions()
                                continue
                            elif react.emoji == '❌':
                                not_finished = False
                                await m.clear_reactions()
                                cancel = True
                                continue

                            await m.edit(content='`' + f'Role: {role.name}'.center(25) + '`\n' + str(perms))

                except asyncio.TimeoutError:
                    await ctx.send("Timed out")
                    await m.clear_reactions()
                    not_finished = False
                    cancel = True
                    continue
            
            if cancel:
                await ctx.send("Cancelled role add")
            else:
                self.bot.cursor.execute("INSERT INTO perms VALUES (?, ?)", (role.id, perms.to_int()))
                self.bot.db.commit()
                await ctx.send("Role added")

        else:
            await ctx.send("Role already added")

    @commands.command(pass_context=True, name="delete_role", aliases=["dr", "deleterole"], hidden=True)
    @commands.has_permissions(manage_guild=True)
    async def delete_role(self, ctx, role : utils.AdvRoleConverter):
        '''
        Remove custom role permissions and revert role to default permissions
        '''

        if role is None:
            await ctx.send("Role not found")
            return

        self.bot.cursor.execute("SELECT RoleID FROM perms")

        if (role.id,) not in self.bot.cursor.fetchall():
            await ctx.send("Role not added yet")
            return

        else:
            self.bot.cursor.execute("DELETE FROM perms WHERE RoleID=?", (role.id,))
            self.bot.db.commit()
            await ctx.send("Role deleted")
        
    @commands.command(pass_context=True, name="check_role", aliases=["cr", "checkrole"], hidden=True)
    @commands.has_permissions(manage_guild=True)
    async def check_role(self, ctx, role : utils.AdvRoleConverter = None):
        '''
        Check either your own current role permissions or the permissions of a target role
        '''

        if role is None:
            for user_role in ctx.author.roles[::-1]:
                self.bot.cursor.execute("SELECT * FROM perms WHERE RoleID=?", (user_role.id,))
                ret = self.bot.cursor.fetchone()

                if ret:
                    await ctx.send('`' + f'Role: {user_role.name}'.center(25) + '`\n' + str(PermissionsParser.parse(ret[1])))
                    return
            
            await ctx.send('`' + 'Role: @everyone'.center(25) + '`\n' + str(PermissionsParser()))

        else:
            self.bot.cursor.execute("SELECT * FROM perms WHERE RoleID=?", (role.id,))
            ret = self.bot.cursor.fetchone()

            if ret:
                await ctx.send('`' + f'Role: {role.name}'.center(25) + '`\n' + str(PermissionsParser.parse(ret[1])))
                return
            
            await ctx.send('`' + f'Role: {role.name}'.center(25) + '`\n' + str(PermissionsParser()))

    @commands.command(pass_context=True, name="list_roles", aliases=["lr", "listroles", "listrole", "list_role"], hidden=True)
    @commands.has_permissions(manage_guild=True)
    async def list_roles(self, ctx):
        '''
        List all roles that have non-default permissions
        '''

        self.bot.cursor.execute("SELECT * FROM perms")
        ret = self.bot.cursor.fetchall()

        msg  = f"```\n"
        for (rid, _) in ret:
            role = discord.utils.get(ctx.guild.roles, id=rid)
            msg += f"{role.name}\n"
        
        msg += f"```"

        await ctx.send(msg)