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
VILLAGER_KILL_MESSAGES = ["You were exiled by the villagers", "You were burnt at the stake by the villagers", "You were executed by the villagers", "Zeph left a note at your door so you decided to leave"]

games: dict[int, "Game"] = {}
class GameStartedError(Exception):
    pass

class Player():
    def __init__(self, user:disnake.Member) -> None:
        self.id = 0
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
    

class Game():
    
    def __init__(self, channel:disnake.TextChannel, start_message:disnake.Message) -> None:
        self.channel = channel
        self.start_message_id = start_message.id
        self.game_running = False
        self.id = channel.guild.id
        self.players: dict[int, Player] = {}
        self.config = load_config(channel.guild.id)
        
        self.players_to_kill: dict[Player, str] = {}
        self.safe_players: list[Player] = []
    
    
    async def start(self, inter: disnake.MessageInteraction) -> None:
        if self.game_running:
            raise(GameStartedError("Game has already started"))
        self.game_running = True
        await self.assign_roles()
        await inter.send("Roles assigned check DM's")
        await asyncio.sleep(5)
    
        while self.game_running:
            await self.night_phase()
            self.win_check()
            if self.game_running:
                await self.day_phase()
                self.win_check()
        
    async def kill_players(self, msg:str):
        for player in self.players_to_kill.keys():
            if player not in self.safe_players:
                await player.kill(self.players_to_kill[player])

                recipients = [p for p in self.players.values() if p != player]

                await self.message_all(recipients, msg.format(name = player.name))
        self.players_to_kill = {}
        self.safe_players = []
    
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
        
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.wolf_vote())
            for player in self.players.values():
                tg.create_task(player.role.night_action(player, self))
        print("FinishedNightActions")
        
        print(self.safe_players)
        await self.kill_players("{name} was killed")
        print("FinishedNightPhase")
    
    async def wolf_vote(self):
        wolves = [p for p in self.players.values() if p.role.team == "Wolves" and p.is_alive]
        targets = [p for p in self.players.values() if p.role.team != "Wolves" and p.is_alive]
        to_kill = await self.vote(
            "Chose a player to kill",
            disnake.Colour.red(),
            "wolf",
            wolves,
            targets
        )
        if to_kill:
            self.players_to_kill[to_kill] = random.choice(WOLF_KILL_MESSAGES)
            
    async def message_all(self, players:list[Player], msg:str):
        async with asyncio.TaskGroup() as tg:
            for player in players:
                tg.create_task(player.send(msg))
        
    async def day_phase(self):
        alive = [p for p in self.players.values() if p.is_alive]

        voted = await self.vote("Choose a player to exile", disnake.Colour.yellow(), "vote", alive, alive, True, True)

        if voted:
            self.players_to_kill[voted] = random.choice(VILLAGER_KILL_MESSAGES)
        else:
            await self.message_all(alive, f"No one was killed")
            
        await self.kill_players("{name} was voted out")

        
        print("FinishedDayPhase")
        
        
        
    def win_check(self):
        pass
    
    async def vote(self, title:str, colour:disnake.Colour, vote_id:str, voters: list[Player], options: list[Player], update=True, skippable = False) -> Player | None:
        """Creates a vote of players and returns the winner of the vote
        Args:
            title: The title for the vote
            colour: the colour to use for the embed
            game: game object that players are associated with
            vote_id: string that identifies the vote and is embedded in the msg
            voters: list of players that receive the vote
            options: list of players to choose from
            role: wether or not this is the roll class calling the vote
            update: wether or not the vote call an update function
            skippable: wether the vote can be skipped
        Returns:
            player: the player object of the player that was chosen or none if tie"""

        embed_ids: list[disnake.Message] = []
        targets: dict[str, str] = {p.name: str(p.id) for p in options} 
        if skippable:
            targets["Skip"] = "0"
        votes: dict[int, int|None] = {} # voter_id -> target_id
        confirmed: list[int] = [] #voter_id
        vote_event = asyncio.Event()

        for player in voters:
            votes[player.id] = None
        
        embed = disnake.Embed(
            title=title,
            colour=colour
        )

        if update:
            for voter in voters:
                embed.add_field(name=voter.name, value="No Vote", inline=True)


        async with asyncio.TaskGroup() as tg:
            for voter in voters:
                embed_ids.append( await voter.member.send(embed=embed))
                await voter.member.send(
                        components=[
                            disnake.ui.StringSelect(
                                options=targets,
                                custom_id=f"Select {vote_id} {self.start_message_id} {voter.id}"),
                            disnake.ui.Button(
                                label="Confirm",
                                style=disnake.ButtonStyle.success,
                                custom_id=f"Confirm {vote_id} {self.start_message_id} {voter.id}"),])
        async def update_message():
            embed = disnake.Embed(
            title=title,
            colour=colour
        )
            nonlocal votes
            nonlocal confirmed
            
            value: str
            for player in voters:
                if votes[player.id] is None:
                    value = "No Vote"
                elif votes[player.id] == 0:
                    value = "Skip"
                else:
                    value = self.players[votes[player.id]].name

                if player.id in confirmed:
                    value = value + " ✅"
                
                embed.add_field(name=player.name, value=value, inline=True)
            async with asyncio.TaskGroup() as tg:
                for embed_id in embed_ids:
                    tg.create_task(embed_id.edit(embed=embed))
            

        @bot.listen("on_dropdown")
        async def handle_dropdown(inter: disnake.MessageInteraction):
            print("Dropdown")
            nonlocal votes
            if not inter.data.custom_id.startswith(f"Select {vote_id}"):
                return
            _, _, game_id, voter_id = inter.data.custom_id.split(" ")
            if int(game_id) != self.start_message_id:
                return
            if not inter.data.values:
                return
            votes[int(voter_id)] = int(inter.data.values[0])
            await inter.response.defer(with_message=False)
            if update:
                await update_message()

        @bot.listen("on_button_click")
        async def handle_confirm(inter: disnake.MessageInteraction):
            nonlocal votes
            nonlocal confirmed
            if not inter.data.custom_id.startswith(f"Confirm {vote_id}"):
                return
            _, _, game_id, voter_id = inter.data.custom_id.split(" ")
            if int(game_id) != self.start_message_id:
                return
            
                
            if votes[int(voter_id)] == 0:
                await inter.send("Skipped vote")
                confirmed.append(int(voter_id))
            elif votes[int(voter_id)]:
                await inter.send(f"Selected {self.players[votes[int(voter_id)]].name}")
                confirmed.append(int(voter_id))
            else:
                await inter.send("Please select an option", ephemeral=True)
            if update:
                await update_message()
            # when all voters have picked, trigger event
            if len(confirmed) == len(voters):
                vote_event.set()

        await asyncio.wait_for(vote_event.wait(), timeout=None)

        bot.remove_listener(handle_dropdown, "on_dropdown")
        bot.remove_listener(handle_confirm, "on_button_click")

        if not votes:
            return None
        tally = Counter(votes.values())
        max_votes = max(tally.values())
        winners = [target for target, count in tally.items() if count == max_votes]

        if len(winners) > 1:
            return None

        if winners[0] == 0:
            return None
        return self.players[winners[0]]


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
        await inter.send(f"{role}: {parameter.title()} is {config[role][parameter]}", ephemeral=True)
        return
    
    if role not in config:
        await inter.send(f"Unknown role {role}", ephemeral=True)
    
    if parameter not in config[role]:
        await inter.send(f"Unknown parameter {parameter}", ephemeral=True)
    
    config[role][parameter] = value
    
    save_config(id, config)
    await inter.send(f"Changed {role}: {parameter.title()} to {value}", ephemeral=True)
    
    

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
        
if __name__ == "__main__":
    with open("token.txt", "r") as f:
        token = f.read()
    bot.run(token) 
    