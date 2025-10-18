from abc import ABC, abstractmethod
import disnake
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import Player, Game


ROLE_REGISTRY: dict[str, "Role"] = {}


def register_role(cls):
    ROLE_REGISTRY[cls.__name__] = cls
    return cls


class Role(ABC):
    def __init__(self, name: str, team: str, colour: disnake.Colour) -> None:
        self.done = False
        self.name = name
        self.team = team
        self.colour = colour
        self.config = {"chance": 50, "count": 1}

    @abstractmethod
    async def night_action(self, player: "Player", game: "Game") -> None:
        pass

    async def assign_action(self, player: "Player", game: "Game") -> None:
        embed = disnake.Embed(
            title=f"You are a {self.name}",
            description="Game start in 5s",
            color=self.colour,
        )
        await player.member.send(embed=embed)

    def __str__(self) -> str:
        return self.name


@register_role
class Villager(Role):
    def __init__(self) -> None:
        super().__init__("Villager", "Village", disnake.Colour.yellow())
        self.config = {"chance": 0, "count": 1, "can_skip_vote": 1, "dead_see_roles": 1}

    async def night_action(
        self,
        player: "Player",
        game: "Game",
    ) -> None:
        await player.send("You sleep peacefully through the night...")


@register_role
class Seer(Role):
    def __init__(self):
        self.target: Player | None = None
        super().__init__("Seer", "Villagers", disnake.Colour.purple())

    async def night_action(self, player: "Player", game: "Game") -> None:
        targets = [p for p in game.players.values()]
        targets.remove(player)
        to_see = await game.vote(
            "Choose a player to see",
            disnake.Colour.purple(),
            str(player.id),
            [player],
            targets,
            False,
        )
        if to_see:
            await player.send(f"{to_see.name} is a {to_see.role.name}")


@register_role
class Medic(Role):
    def __init__(self):
        super().__init__("Medic", "Villagers", disnake.Colour.green())

    async def night_action(self, player: "Player", game: "Game") -> None:
        targets = [p for p in game.players.values() if p.is_alive]
        targets.remove(player)
        to_protect = await game.vote(
            "Choose a player to protect",
            disnake.Colour.green(),
            str(player.id),
            [player],
            targets,
            False,
        )
        if to_protect:
            game.safe_players.append(to_protect)
            await player.send(f"You protected {to_protect.name} from the wolves")
        pass


@register_role
class Werewolf(Role):
    def __init__(self):
        super().__init__("Werewolf", "Wolves", disnake.Colour.red())
        self.config = {"chance": 100, "count": 1, "can_skip_vote": 1}

    async def night_action(self, player: "Player", game: "Game") -> None:
        pass

    async def assign_action(self, player: "Player", game: "Game") -> None:
        wolves = [p.name for p in game.players.values() if p.role.team == "Wolves"]

        if len(wolves) == 1:
            embed = disnake.Embed(
                title=f"You are a {self.name}",
                description="Game start in 5s",
                color=disnake.Colour.red(),
            )
            await player.member.send(embed=embed)
        else:
            embed = disnake.Embed(
                title=f"There are {len(wolves)} wolves, you are one of them",
                description="Game start in 5s",
                color=disnake.Colour.red(),
            )
            for wolf in wolves:
                embed.add_field(name="Inline Title", value=wolf, inline=True)
            await player.member.send(embed=embed)