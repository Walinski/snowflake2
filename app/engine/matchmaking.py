
from __future__ import annotations

from twisted.internet import reactor
from typing import List

from ..objects.collections import Players
from .penguin import Penguin
from .tusk import TuskGame
from .ai import PenguinAI
from .game import Game

import logging
import config
import time

class MatchmakingQueue:
    def __init__(self) -> None:
        self.players = Players()
        self.logger = logging.getLogger('Matchmaking')

    def add(self, player: Penguin) -> None:
        self.players.add(player)

        player.logger.info(f'Joined matchmaking queue with "{player.element}"')
        player.queue_time = time.time()
        player.in_queue = True

        if len(match := self.find_match(player)) >= 3:
            self.logger.info(f'Found match: {match}')

            match_types = {
                0: self.create_normal_game,
                1: self.create_tusk_game
            }

            return match_types[player.battle_mode](*match)

        reactor.callLater(
            config.MATCHMAKING_TIMEOUT,
            self.fill_queue, player
        )

    def remove(self, player: Penguin) -> None:
        if player in self.players:
            self.players.remove(player)

            player.logger.info('Left matchmaking queue')
            player.in_queue = False

    def find_match(self, player: Penguin) -> List[Penguin] | None:
        elements = ['snow', 'water', 'fire']
        elements.remove(player.element)

        players = [player]

        for element in elements:
            matches = self.players.with_element(
                element,
                player.battle_mode
            )

            if not matches:
                continue

            # Sort by closest rank
            players.sort(
                key=lambda other: abs(
                    player.object.snow_ninja_rank -
                    other.object.snow_ninja_rank
                )
            )

            # Add closest match
            players.append(matches[0])

        players.sort(key=lambda x: x.element)
        return players

    def create_normal_game(self, fire: Penguin, snow: Penguin, water: Penguin) -> None:
        game = Game(fire, snow, water)
        game.server.games.add(game)

        for client in game.clients:
            player_select = client.get_window(config.PLAYERSELECT_SWF)
            player_select.send_payload(
                'matchFound',
                {
                    1: fire.name if fire else None,
                    2: water.name if water else None,
                    4: snow.name if snow else None
                }
            )

            # Remove from matchmaking queue
            self.remove(client)

        # Start game loop
        game.server.runThread(game.start)

    def create_tusk_game(self, fire: Penguin, snow: Penguin, water: Penguin) -> None:
        game = TuskGame(fire, snow, water)
        game.server.games.add(game)

        for client in game.clients:
            player_select = client.get_window(config.PLAYERSELECT_SWF)
            player_select.send_payload(
                'matchFound',
                {
                    1: fire.name if fire else None,
                    2: water.name if water else None,
                    4: snow.name if snow else None
                }
            )

            # Remove from matchmaking queue
            self.remove(client)

        # Start game loop
        game.server.runThread(game.start)

    def insert_ai_players(self, players: List[Penguin]) -> List[Penguin]:
        elements = ['snow', 'water', 'fire']
        battle_mode = players[0].battle_mode

        for player in players:
            elements.remove(player.element)

        for element in elements:
            ai_player = PenguinAI(player.server, element, battle_mode)
            players.append(ai_player)

        players.sort(key=lambda x: x.element)
        return players

    def get_none_players(self, players: List[Penguin]) -> List[Penguin]:
        player_dict = {
            'fire': None,
            'snow': None,
            'water': None
        }

        for player in players:
            player_dict[player.element] = player

        return list(player_dict.values())

    def fill_queue(self, player: Penguin) -> None:
        if player.battle_mode == 0 and not config.ALLOW_FORCESTART_SNOW:
            # Singleplayer snow is disabled
            return

        if player.battle_mode == 1 and not config.ALLOW_FORCESTART_TUSK:
            # Singleplayer tusk is disabled
            return

        if player.in_game:
            # Player has found a match
            return

        # Find other players in queue
        players = self.find_match(player)

        for p in players:
            required_time = config.MATCHMAKING_TIMEOUT / 2
            time_in_queue = time.time() - p.queue_time

            if time_in_queue < required_time:
                # Player has not been in queue long enough
                players.remove(p)

        # Fill up missing players with bots
        players = self.insert_ai_players(players)

        self.logger.info(f'Found match: {players}')

        match_types = {
            0: self.create_normal_game,
            1: self.create_tusk_game
        }

        match_types[player.battle_mode](*players)
