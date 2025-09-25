from dataclasses import dataclass, field
from abc import ABC, abstractmethod
import disnake
from disnake.ext import commands
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import Player, Game
    

ROLE_REGISTRY: dict[str, "Role"] = {}

def register_role(cls):
    ROLE_REGISTRY[cls.__name__] = cls
    return cls



class Role(ABC):    
    def __init__(self, name: str, team:str) -> None:
        self.done = False
        self.name = name
        self.team = team
        self.config = {"chance": 50, "count": 1}
        
    
    @abstractmethod
    async def night_action(self, player: "Player", game: "Game") -> None:
        pass           
    
    async def assign_action(self, player: "Player", game: "Game") -> None:
        await player.send(f"You are a {self.name}\nGame start in 5s")
    def __str__(self) -> str:
        return self.name
        

@register_role
class Villager(Role):
    def __init__(self) -> None:
        super().__init__("Villager", "Village")
        self.config = {"chance": 0,
                       "count": 1,
                       "can_skip_vote": 1,
                       "dead_see_roles": 1}

    async def night_action(self, player: "Player", game: "Game",) -> None:
        await player.send("You sleep peacefully through the night...")

        

        
@register_role
class Seer(Role):
    def __init__(self):
        self.target: Player | None = None
        super().__init__("Seer", "Village")

    async def night_action(self, player: "Player", game: "Game") -> None:
        self.target = None
        self.done = False
        print("hello?")
        targets = [p for p in game.players.values()]
        targets.remove(player)
        to_see = await game.vote(
            "Choose a player to see", 
            disnake.Colour.purple(),
            "seer",
            [player],
            targets,
            False)
        if to_see:
            await player.send(f"{to_see.name} is a {to_see.role.name}")

@register_role
class Medic(Role):
    def __init__(self):
        super().__init__("Medic", "Village")

    async def night_action(self, player: "Player", game: "Game") -> None:
        pass


@register_role
class Werewolf(Role):
    def __init__(self):
        super().__init__("Werewolf", "Wolves")
        self.config = {"chance": 100, "count": 1}
    
    async def night_action(self, player: "Player", game: "Game") -> None:
        pass    
    
    async def assign_action(self, player: "Player", game: "Game") -> None:
    
        wolves = [p.name for p in game.players.values() if p.role.team == "Wolves"]
        
        if len(wolves) == 1:
            await player.send(f"You are a {self.name}\nGame start in 5s")
        else:
            await player.send(f"There are {len(wolves)} wolves, you are one of them. \n{'\n'.join(wolves)}\nGame start in 5s")