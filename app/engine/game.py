
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List

if TYPE_CHECKING:
    from .penguin import Penguin

from app.data.constants import KeyModifier, KeyTarget, KeyInput, Phase
from app.objects.collections import ObjectCollection, AssetCollection
from app.objects.ninjas import WaterNinja, SnowNinja, FireNinja
from app.objects.enemies import Sly, Scrap, Tank
from app.objects.gameobject import GameObject
from app.objects.sound import Sound
from app.objects.asset import Asset

from twisted.python.failure import Failure
from twisted.internet import reactor

from .timer import Timer
from .grid import Grid

import logging
import random
import config
import time

class Game:
    def __init__(self, fire: "Penguin", snow: "Penguin", water: "Penguin") -> None:
        self.server = fire.server
        self.water = water
        self.fire = fire
        self.snow = snow

        self.bonus_cirteria = random.choice(['no_ko', 'under_time', 'full_health'])
        self.game_start = time.time()

        self.map = random.randrange(1, 3)
        self.round = 0

        self.objects = ObjectCollection()
        self.timer = Timer(self)
        self.grid = Grid()

        self.logger = logging.getLogger('game')

    @property
    def clients(self) -> List["Penguin"]:
        return [self.fire, self.snow, self.water]

    @property
    def ninjas(self) -> List[GameObject]:
        return [
            self.objects.by_name('Water'),
            self.objects.by_name('Snow'),
            self.objects.by_name('Fire')
        ]

    @property
    def enemies(self) -> List[GameObject]:
        return [
            *self.objects.with_name('Sly'),
            *self.objects.with_name('Scrap'),
            *self.objects.with_name('Tank')
        ]

    @property
    def backgrounds(self) -> List[GameObject]:
        return {
            1: [
                GameObject.from_asset('env_mountaintop_bg', self, x=4.5, y=-1.1)
            ],
            2: [
                GameObject.from_asset('forest_bg', self, x=4.5, y=-1.1),
                GameObject.from_asset('forest_fg', self, x=4.5, y=6.1)
            ],
            3: [
                GameObject.from_asset('cragvalley_bg', self, x=4.5, y=-1.1),
                GameObject.from_asset('cragvalley_fg', self, x=4.5, y=6)
            ]
        }[self.map]

    @property
    def bonus_cirteria_met(self) -> bool:
        return {
            'no_ko': all(not player.was_ko for player in self.clients),
            'full_health': all(ninja.hp == 100 for ninja in self.ninjas),
            'under_time': (time.time() < self.game_start + 300)
        }[self.bonus_cirteria]

    def start(self) -> None:
        self.fire.game = self
        self.snow.game = self
        self.water.game = self

        self.fire.in_game = True
        self.snow.in_game = True
        self.water.in_game = True

        # Wait for "prepare to battle" screen to end
        time.sleep(3)

        for client in self.clients:
            player_select = client.window_manager.get_window('cardjitsu_snowplayerselect.swf')
            player_select.close()

        # This will trigger the loading transition
        self.send_tag(
            'W_PLACE',
            '1:10001', # PlaceId
            8,         # PlaceObjectId
            1          # PlaceInstanceId
        )

        # Register "/use" event
        self.register_input(
            command='/use',
            input_id='/use',
            script_id='4375706:1',
            target=KeyTarget.TILE,
            event=KeyInput.MOUSE_UP,
            key_modifier=KeyModifier.NONE
        )

        # Scale screen up to 100
        self.send_tag('P_TILESIZE', 100)

        self.initialize_objects()
        self.wait_for_players(lambda player: player.is_ready)

        # Play background music
        Sound.from_name('mus_mg_201303_cjsnow_gamewindamb', looping=True).play(self)

        self.show_background()
        self.spawn_ninjas()

        for client in self.clients:
            # Close loading screen
            player_select = client.window_manager.get_window('cardjitsu_snowplayerselect.swf')
            player_select.send_action('closeCjsnowRoomToRoom')

            # Load exit button
            close_button = client.window_manager.get_window('cardjitsu_snowclose.swf')
            close_button.layer = 'bottomLayer'
            close_button.load(
                loadDescription="",
                assetPath="",
                xPercent=1,
                yPercent=0
            )

        time.sleep(1)

        # Reset game time
        self.game_start = time.time() + 1

        self.display_round_title()
        time.sleep(1.6)

        self.spawn_enemies()
        time.sleep(1)

        self.load_ui()
        self.send_tip(Phase.MOVE)

        # Run game loop until game ends
        self.run_game_loop()

        # TODO: Handle payout

    def run_game_loop(self) -> None:
        while True:
            self.wait_for_timer()
            self.round += 1

            if (self.round > 2) and (not self.bonus_cirteria_met):
                break

            if (self.round > 3):
                break

            # Remove any existing enemies
            self.remove_enemies()

            # Enemies can spawn anywhere now
            self.grid.enemy_spawns = [range(9), range(5)]

            self.display_round_title()
            time.sleep(1.6)

            # Create new enemies
            self.create_enemies()
            self.spawn_enemies()

            time.sleep(1)
            # TODO: Show grid

    def send_tag(self, tag: str, *args) -> None:
        for player in self.clients:
            player.send_tag(tag, *args)

    def wait_for_players(self, condition: Callable) -> None:
        """Wait for all players to finish loading the game"""
        for player in self.clients:
            while not condition(player):
                pass

    def wait_for_timer(self) -> None:
        """Wait for the timer to finish"""
        self.enable_cards()
        self.timer.run()
        self.disable_cards()

    def error_callback(self, failure: Failure) -> None:
        self.logger.error(
            f'Failed to execute game thread: {failure.getBriefTraceback()}',
            exc_info=failure.tb
        )

        for client in self.clients:
            client.send_to_room()
            client.close_connection()

    def register_input(
        self,
        input_id: str,
        script_id: int,
        target: KeyTarget,
        event: KeyInput,
        key_modifier: KeyModifier,
        command: str
    ) -> None:
        self.send_tag(
            'W_INPUT',
            input_id,
            script_id,
            target.value,
            event.value,
            key_modifier.value,
            command
        )

    def initialize_objects(self) -> None:
        """Initialize all game objects"""
        self.create_ninjas()
        self.create_enemies()
        self.create_background()

        # Load sprites
        for object in self.objects:
            object.load_sprites()

    def create_ninjas(self) -> None:
        water = WaterNinja(self, x=0, y=0)
        water.place_object()

        snow = SnowNinja(self, x=0, y=2)
        snow.place_object()

        fire = FireNinja(self, x=0, y=4)
        fire.place_object()

    def create_enemies(self) -> None:
        max_enemies = {
            0: range(1, 4),
            1: range(1, 4),
            2: range(1, 4),
            3: range(4, 5),
        }[self.round]

        amount_enemies = random.choice(max_enemies)
        enemy_classes = (Sly, Scrap, Tank)

        for _ in range(amount_enemies):
            enemy_class = random.choice(enemy_classes)
            enemy = enemy_class(self)
            enemy.place_object()

    def create_background(self) -> None:
        for background in self.backgrounds:
            background.place_object()

    def spawn_ninjas(self) -> None:
        water = self.objects.by_name('Water')
        water.place_object()
        water.animate_object(
            'waterninja_idle_anim',
            play_style='loop',
            reset=True
        )

        snow = self.objects.by_name('Snow')
        snow.place_object()
        snow.animate_object(
            'snowninja_idle_anim',
            play_style='loop',
            reset=True
        )

        fire = self.objects.by_name('Fire')
        fire.place_object()
        fire.animate_object(
            'fireninja_idle_anim',
            play_style='loop',
            reset=True
        )

        # TODO: Health bar

    def spawn_enemies(self) -> None:
        """Spawn enemies for the current round"""
        for enemy in self.enemies:
            # Choose spawn location on grid
            x, y = self.grid.enemy_spawn_location()

            self.grid[x, y] = enemy
            enemy.place_object()

            # Play spawn animation
            enemy.animate_object('snowman_spawn_anim', play_style='play_once')
            enemy.play_sound('sfx_mg_2013_cjsnow_snowmenappear')

            # Play idle animation
            enemy.animate_object(
                f'{enemy.name.lower()}_idle_anim',
                play_style='loop'
            )

            # TODO: Health bar

    def remove_enemies(self) -> None:
        for enemy in self.enemies:
            enemy.remove_object()

    def remove_ninjas(self) -> None:
        for ninja in self.ninjas:
            ninja.remove_object()

    def show_background(self) -> None:
        for background in self.backgrounds:
            obj = self.objects.by_name(background.name)
            obj.place_sprite(background.name)

    def load_ui(self) -> None:
        for client in self.clients:
            snow_ui = client.window_manager.get_window('cardjitsu_snowui.swf')
            snow_ui.layer = 'bottomLayer'
            snow_ui.load(
                {
                    'cardsAssetPath': f'http://{config.MEDIA_LOCATION}/game/mpassets//minigames/cjsnow/en_US/deploy/',
                    'element': client.element,
                    'isMember': client.is_member,
                },
                loadDescription="",
                assetPath="",
                xPercent=0.5,
                yPercent=1
            )

    def send_tip(self, phase: Phase, client: "Penguin" | None = None) -> None:
        clients = [client] if client else self.clients

        for client in clients:
            if not client.tip_mode:
                continue

            infotip = client.window_manager.get_window('cardjitsu_snowinfotip.swf')
            infotip.layer = 'bottomLayer'
            infotip.load(
                {
                    'element': client.element,
                    'phase': phase.value,
                },
                loadDescription="",
                assetPath="",
                xPercent=0.1,
                yPercent=0
            )

            reactor.callLater(10, infotip.send_payload, 'disable')

    def enable_cards(self) -> None:
        for client in self.clients:
            snow_ui = client.window_manager.get_window('cardjitsu_snowui.swf')
            snow_ui.send_payload('enableCards')

    def disable_cards(self) -> None:
        for client in self.clients:
            snow_ui = client.window_manager.get_window('cardjitsu_snowui.swf')
            snow_ui.send_payload('disableCards')

    def display_round_title(self) -> None:
        for client in self.clients:
            round_title = client.window_manager.get_window('cardjitsu_snowrounds.swf')
            round_title.layer = 'bottomLayer'
            round_title.load(
                {
                    'bonusCriteria': self.bonus_cirteria,
                    'remainingTime': ((self.game_start + 300) - time.time()) * 1000,
                    'roundNumber': self.round
                },
                loadDescription="",
                assetPath=""
            )
