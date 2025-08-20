import roles as R
import json
import random
import typing
import disnake
import asyncio
from disnake.ext import commands
from collections import Counter


intents = disnake.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True  # Required for managing roles


command_sync_flags = commands.CommandSyncFlags.default()
command_sync_flags.sync_commands_debug = True

bot = commands.InteractionBot(
    test_guilds=[1012004094564646992, 873251935602499654],
    command_sync_flags=command_sync_flags,
)

WOLF_KILL_MESSAGES = ["You were killed by the wolves", "You were torn to shreds by the wolves", "You've been eaten by the wolves"]
VILLAGER_KILL_MESSAGES = ["You were exiled by the villagers", "You were burnt at the stake by the villagers", "You were executed by the villagers"]

games: dict[int, "Game"] = {}
class GameStartedError(Exception):
    pass

class Player():
    def __init__(self, user:disnake.Member) -> None:
        self.name = user.global_name
        self.member: disnake.Member = user
        self.id = user.id
        self.role: R.Role
        self.is_alive = True
        self.ready_event = asyncio.Event()
        
        #Vote Handling
        self.target: Player | None  = None
        self.done = False
        self.embed_id = disnake.Message

    
    async def send(self, msg) -> None:
        await self.member.send(msg)

    async def kill(self, reason):
        self.is_alive = False
        embed = disnake.Embed(
            title="You have been killed",
            description=f"{reason}",
            color=disnake.Color.red()
        )
        await self.member.send(embed=embed)
    
    async def send_vote(self, game:"Game"):
        self.target = None
        self.done = False
        targets = {p.name:  str(p.member.id) for p in game.players.values() if p.is_alive}
        targets["Skip"] = "0"
        embed = disnake.Embed(
            title="Choose a player to vote out",
            color=disnake.Colour.gold()
        )
        
        for player in game.players.values():
            embed.add_field(name=player.name, value="Skip", inline=True)
        self.embed_id = await self.member.send(embed=embed)
        
        await self.member.send(
                    components=[
                        disnake.ui.StringSelect(
                            options=targets,
                            custom_id=f"VoteSelect {game.start_message} {self.id}"),
                        disnake.ui.Button(
                            label="Confirm",
                            style=disnake.ButtonStyle.success,
                            custom_id=f"VoteConfirm {game.start_message} {self.id}"),])
    
    async def vote_confirm(self, player: "Player", game:"Game") ->  None:
        if not self.target:
            await player.send(F"You decided to skip")
        elif not self.done:
            game.votes.append(self.target)
            await player.send(F"You voted for {self.target.name}")
        player.ready_event.set()
        self.done = True
            
    async def update_message(self, game: "Game"):
        embed = disnake.Embed(
            title="Chose a person to kill",
            color=disnake.Colour.gold()
        )
        for player in game.players.values():
            if player.target is None:
                value = "Skip"
            elif player.done:
                value = player.target.name + " ✅"
            else:
                value = player.target.name
            embed.add_field(name=player.name, value=value, inline=True)
        
        await self.embed_id.edit(embed=embed)
    

class Game():
    
    def __init__(self, channel:disnake.TextChannel, start_message:disnake.Message) -> None:
        self.channel = channel
        self.start_message = start_message.id
        self.game_running = False
        self.id = channel.guild.id
        self.players: dict[int, Player] = {}
        
        self.wolf_kills: list[Player] = []
        self.wolves_have_killed = False
        self.players_to_kill: dict[int, str] = {}
        
        self.votes = list[Player]
        self.vote_concluded = False
        
    async def start(self, inter: disnake.MessageInteraction) -> None:
        if self.game_running:
            raise(GameStartedError("Game has already started"))
        self.game_running = True
        await self.assign_roles()
        await inter.send("Roles assigned check DM's")
        await asyncio.sleep(5)
    
        while self.game_running:
            #await self.night_phase()
            #await self.resolve_night()
            self.win_check()
            if self.game_running:
                await self.day_phase()
                await self.resolve_day()
                self.win_check()
        
    
    
    async def assign_roles(self) -> None:
        config = load_config(self.id)
        roles = parse_config(config)
        players = list(self.players.values())

        # Trim roles safely
        roles = trim_roles(roles, players)

        # Fill with villagers if needed
        if len(roles) < len(players):
            roles.extend(R.ROLE_REGISTRY["Villager"]() for _ in range(len(players) - len(roles)))

        # Shuffle and assign
        random.shuffle(players)
        random.shuffle(roles)
        for player, role in zip(players, roles):
            player.role = role

        # Role-specific setup
        async with asyncio.TaskGroup() as tg:
            for player in self.players.values():
                tg.create_task(player.role.assign_action(player, self))
        
    
    async def night_phase(self):
        # Reset state
        self.wolf_kills = []
        self.wolves_have_killed = False
        # Collect tasks for all alive players
        tasks = []
        for player in self.players.values():
            if player.is_alive:
                player.ready_event.clear()
                task = asyncio.create_task(player.role.night_action(player, self))
                tasks.append(task)

        # Wait until ALL roles report "done"
        await asyncio.gather(*(p.ready_event.wait() for p in self.players.values() if p.is_alive))
        # At this point everyone has acted
        print("FinishedNightPhase")
    
    async def  check_wolf_kill(self):
        wolfs = [p for p in self.players.values() if p.role.team == "Wolves" and p.is_alive]
        wolfs_ready = all(player.role.done for player in wolfs)
        if wolfs_ready and not self.wolves_have_killed:
            votes = [p.role.target for p in wolfs]
            vote_counts = Counter(votes)
            max_votes = max(vote_counts.values())
            most_voted_players = [player for player, count in vote_counts.items() if count == max_votes]
            if len(most_voted_players) == 1:
                # Single player with most votes
                chosen_player = most_voted_players[0]
            else:
                # Tie
                chosen_player = random.choice(most_voted_players)
            self.players_to_kill[chosen_player.id] = random.choice(WOLF_KILL_MESSAGES)

            self.wolves_have_killed = True
            for wolf in wolfs:
                await wolf.send(f"{chosen_player.name} was killed")
                
    async def  check_votes(self):
        players = [p for p in self.players.values() if p.is_alive]
        players_ready = all(player.role.done for player in players)
        if players_ready and not self.vote_concluded:
            votes = [p.role.target for p in players]
            vote_counts = Counter(votes)
            max_votes = max(vote_counts.values())
            most_voted_players = [player for player, count in vote_counts.items() if count == max_votes]
            if len(most_voted_players) == 1:
                # Single player with most votes
                chosen_player = most_voted_players[0]
            else:
                # Tie
                chosen_player = random.choice(most_voted_players)
            self.players_to_kill[chosen_player.id] = random.choice(VILLAGER_KILL_MESSAGES)

            self.vote_concluded = True
            
            for player in players:
                await player.send(f"{chosen_player.name} was killed")
            
            
        
    async def resolve_night(self):
        
        for player in self.players_to_kill.keys():
            await self.players[player].kill(self.players_to_kill[player])
    
    async def resolve_day(self):
        
        for player in self.players_to_kill.keys():
            await self.players[player].kill(self.players_to_kill[player])
        
    async def message_all(self, players:list[Player], msg:str):
        async with asyncio.TaskGroup() as tg:
            for player in players:
                tg.create_task(player.send(msg))
        
    async def day_phase(self):
        alive = [p for p in self.players.values() if p.is_alive]
        if not self.players_to_kill:
            await self.message_all(alive, "No one died")
        else:
            killed = [self.players[p].name for p in self.players_to_kill.keys()]
            print(killed)
        self.players_to_kill = {}
        
        async with asyncio.TaskGroup() as tg:
            for player in self.players.values():
                player.ready_event.clear()
                tg.create_task(player.send_vote(self))
                
        await asyncio.gather(*(p.ready_event.wait() for p in self.players.values() if p.is_alive))
        # At this point everyone has acted
        print("FinishedDayPhase")
        
        
        
    def win_check(self):
        pass
    
# Config Handling

def load_config(guild_id: int) -> dict[str, dict[str, int]]:
    """Loads config for a guild, falling back to a default if missing/corrupt."""
    try:
        with open(f"{guild_id}.json", "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return gen_config(guild_id)
    
def gen_config(id: int) -> dict[str, dict[str, int]]:
    config = {}
    for role_cls in R.ROLE_REGISTRY.values():
        role = role_cls()
        config[role.name] = role.config.copy()
        
              
    save_config(id, config)
    return config        


def save_config(guild_id: int, config: dict[str, dict[str, int]]) -> None:
    """Saves config back to disk."""
    with open(f"{guild_id}.json", "w") as f:
        json.dump(config, f, indent=4) 
        
        
# Role Logic
def trim_roles(roles: list[R.Role], players: list[Player]) -> list[R.Role]:
    """Trim roles to match player count, always ensuring at least one Werewolf."""
    max_roles = len(players)
    
    # Find all werewolves in the roles
    werewolves = [r for r in roles if r.name == "Werewolf"]
    
    if not werewolves:
        # No werewolf in config? Inject one
        werewolves = [R.ROLE_REGISTRY["Werewolf"]()]
        roles.append(werewolves[0])
        
    # Pick at least one werewolf
    chosen = [random.choice(werewolves)]
    # Fill the rest randomly, excluding the chosen werewolf
    remaining = [r for r in roles if r not in chosen]
    
    if len(roles) > max_roles:
        extra = random.sample(remaining, max_roles - len(chosen))
        chosen.extend(extra)
    else:
        chosen.extend(remaining)
    return chosen
    
        
def parse_config(config: dict[str, dict[str, int]]) -> list[R.Role]:
    """Converts config file into list of role objects
    
    Args:
        config (dict): the loaded config file to generate from
    Returns:
        list: list of role objects"""
    roles = []
    for role_def in config.keys():
        if role_def not in R.ROLE_REGISTRY:
            raise ValueError(f"Unknown role: {role_def}")
        
        count = config[role_def]["count"]
        
        if random.randint(0,100) <= config[role_def]["chance"]:
            for _ in range(count):
                roles.append(R.ROLE_REGISTRY[role_def]())
    return roles 

@bot.slash_command(description="Starts the werwolf game")
async def start(inter: disnake.ApplicationCommandInteraction):
    if not isinstance(inter.channel, disnake.channel.TextChannel):
        await inter.send(str(type(inter.channel)))
        return
    
    msg = await inter.channel.send("Current Players: 1",
                                components=[
                                    disnake.ui.Button(
                                        label="Join",
                                        style=disnake.ButtonStyle.success,
                                        custom_id="join"),
                                    disnake.ui.Button(
                                        label="Start",
                                        style=disnake.ButtonStyle.blurple,
                                        custom_id="start"
                                    )
                                ])

    games[msg.id] = Game(inter.channel, msg)
    games[msg.id].players[inter.user.id] = Player(inter.user)  # pyright: ignore[reportArgumentType]
    await inter.send("Created & joined game", ephemeral=True)


async def autocomp_roles(inter: disnake.ApplicationCommandInteraction, user_input: str):
    if not isinstance(inter.channel, disnake.channel.TextChannel):
        return []
    config = load_config(inter.channel.guild.id)
    return [role for role in config if user_input.lower() in role.lower()]

async def autocomp_parameter(inter: disnake.ApplicationCommandInteraction, user_input: str):
    if not isinstance(inter.channel, disnake.channel.TextChannel):
        return []
    config = load_config(inter.channel.guild.id)

    role = inter.filled_options.get("role")
    if not role or role not in config:
        return []

    return [key for key in config[role].keys() 
            if user_input.lower() in key.lower()]
    
@bot.slash_command()
async def config(inter: disnake.AppCommandInteraction,
                role: str = commands.param(autocomplete=autocomp_roles),
                parameter: str = commands.param(autocomplete=autocomp_parameter),
                value: int | None = None):
    """
    Changes the config option for the server

    Parameters
    ----------
    role: The roll to change the config for
    parameter: The config to change
    value: The number to set the config to
    """
    if not isinstance(inter.channel, disnake.channel.TextChannel):
        await inter.send(str(type(inter.channel)))
        return
    
    id = inter.channel.guild.id
    config = load_config(id)
    
    if value == None:
        await inter.send(f"{role}: {parameter.title()} is {config[role][parameter]}")
        return
    
    if role not in config:
        await inter.send(f"Unknown role {role}")
    
    if parameter not in config[role]:
        await inter.send(f"Unknown parameter {parameter}")
    
    config[role][parameter] = value
    
    save_config(id, config)
    await inter.send(f"Changed {role}: {parameter.title()} to {value}")
    
    

@bot.listen("on_button_click")
async def handle_button_click(inter: disnake.MessageInteraction):
    if inter.data.custom_id == "join":
        if inter.user.id in games[inter.message.id].players.keys():
            await inter.send("Already in game", ephemeral=True)
            return
        games[inter.message.id].players[inter.user.id] = Player(inter.user) # pyright: ignore[reportArgumentType]
        await inter.send("Joined", ephemeral=True)
        players = len(games[inter.message.id].players)
        await inter.message.edit(f"Current Players: {players}",
                                components=[
                                    disnake.ui.Button(
                                        label="Join",
                                        style=disnake.ButtonStyle.success,
                                        custom_id="join"),
                                    disnake.ui.Button(
                                        label="Start",
                                        style=disnake.ButtonStyle.blurple,
                                        custom_id="start"
                                    )
                                ])
        return
    
    
    if inter.data.custom_id == "start":
        try:
            await inter.response.defer(with_message=False)
            await games[inter.message.id].start(inter)
        except KeyError:
            await inter.send("Could Not find game", ephemeral=True)
        except GameStartedError as e:
            await inter.send(str(e), ephemeral=True)
        return
    
    if inter.data.custom_id.startswith("WolfConfirm"):
        _, game_id, player_id = inter.data.custom_id.split(" ")
        game_id, player_id = int(game_id), int(player_id) 
        game = games[game_id]
        await inter.response.defer(with_message=False)
        await game.players[player_id].role.night_action_confirm(game.players[player_id], game)
        
        async with asyncio.TaskGroup() as tg:
            for player in game.players.values():
                if isinstance(player.role, R.Werewolf):
                    tg.create_task(player.role.update_message())
        return
        
        
    if inter.data.custom_id.startswith("SeerConfirm"):
        _, game_id, player_id = inter.data.custom_id.split(" ")
        game_id, player_id = int(game_id), int(player_id) 
        game = games[game_id]
        await game.players[player_id].role.night_action_confirm(game.players[player_id], game)
        await inter.response.defer(with_message=False)
        return
    
    if inter.data.custom_id.startswith("VoteConfirm"):
        _, game_id, player_id = inter.data.custom_id.split(" ")
        game_id, player_id = int(game_id), int(player_id) 
        game = games[game_id]
        await game.players[player_id].vote_confirm(game.players[player_id], game)
        await inter.response.defer(with_message=False)
        async with asyncio.TaskGroup() as tg:
            for player in game.players.values():
                tg.create_task(player.update_message(game))
        return
    

        
        
@bot.listen("on_dropdown")
async def handle_dropdown_click(inter: disnake.MessageInteraction):
    if inter.data.custom_id.startswith("WolfSelect"):
        
        if not inter.data.values:
            return
        target_id = int(inter.data.values[0])
        _, game_id, player_id = inter.data.custom_id.split(" ")

        game = games[int(game_id)]
        wolf = game.players[int(player_id)]
        wolf.role.target = game.players[target_id]
        await inter.response.defer(with_message=False)
        
        async with asyncio.TaskGroup() as tg:
            for player in game.players.values():
                if isinstance(player.role, R.Werewolf):
                    tg.create_task(player.role.update_message())
        return
            
    if inter.data.custom_id.startswith("SeerSelect"):
        
        if not inter.data.values:
            return
        target_id = int(inter.data.values[0])
        _, game_id, player_id = inter.data.custom_id.split(" ")

        game = games[int(game_id)]
        player = game.players[int(player_id)]
        player.role.target = game.players[target_id]
        await inter.response.defer(with_message=False)
        return
    
    if inter.data.custom_id.startswith("VoteSelect"):
        
        if not inter.data.values:
            return
        target_id = int(inter.data.values[0])
        _, game_id, player_id = inter.data.custom_id.split(" ")
        
            
        
        game = games[int(game_id)]
        player = game.players[int(player_id)]
        if target_id == 0:
            player.target = None
        else:
            player.target = game.players[target_id]
        await inter.response.defer(with_message=False)
        async with asyncio.TaskGroup() as tg:
            for player in game.players.values():
                tg.create_task(player.update_message(game))
        return
        
        
if __name__ == "__main__":
    with open("token.txt", "r") as f:
        token = f.read()
    bot.run(token) 
    