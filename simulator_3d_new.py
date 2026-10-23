"""
GestureFly 3D - kid-friendly simulator.

Hand-gesture (MediaPipe) hook
-----------------------------
Pass any object with a get_command() method as `controller`:

    simulator = Simulator3D(drone, controller=my_gesture_controller)

Every frame the simulator calls controller.get_command(). Return None when
no gesture is recognised, or one of these strings:

    "TAKEOFF", "LAND", "FORWARD", "BACKWARD",
    "LEFT" / "ROTATE LEFT", "RIGHT" / "ROTATE RIGHT",
    "HOVER" / "STOP", "RESET"

(case, underscores and dashes don't matter). If the controller also has a
`last_gesture` attribute (for example "OPEN PALM"), it is shown on screen so
kids can see what the camera saw. Repeated commands are rate-limited, so it is
fine to return the same command on every frame while a gesture is held.

You can also call simulator.execute_command("FORWARD", source="HANDS")
directly from your own code. If no controller is given, the game runs on the
keyboard alone.
"""

import asyncio
import colorsys
import math
import random
import sys
from array import array

import pygame


# =============================================================
# LAYOUT AND COLOURS
# =============================================================

CENTER_X, CENTER_Y, SCALE = 450, 425, 27   # isometric projection
FIELD = 8                                  # ground runs from -8 to 8
HOME_RADIUS = 2.0                          # how close to H counts as "home"
COLLECT_RADIUS = 1.25                      # generous, for small hands

INK = (38, 52, 96)
INK_SOFT = (90, 108, 155)
WHITE = (255, 255, 255)
YELLOW = (255, 206, 60)
ORANGE = (255, 140, 60)
GREEN = (60, 180, 100)
SKY_TOP = (110, 190, 255)
SKY_BOTTOM = (205, 238, 255)
GRASS_A = (132, 214, 106)
GRASS_B = (118, 201, 94)
CONFETTI = [(255, 99, 132), (255, 206, 60), (80, 200, 255),
            (130, 220, 100), (190, 130, 255), (255, 150, 60)]

DRONE_SKINS = [
    ("Sky Blue", (90, 175, 255), (45, 110, 200)),
    ("Bubblegum", (255, 140, 195), (210, 80, 150)),
    ("Lime", (155, 222, 80), (90, 160, 40)),
    ("Sunny", (255, 205, 70), (215, 145, 25)),
    ("Grape", (175, 130, 255), (120, 80, 210)),
]

LEVELS = [
    {"name": "Level 1: Backyard", "par": 35, "moving": False,
     "stars": [(-2.5, -1.5), (2.5, -1.5), (3.0, 2.0), (-2.5, 2.5),
               (0.0, -3.5)]},
    {"name": "Level 2: Big Park", "par": 55, "moving": False,
     "stars": [(-5.0, -4.0), (4.5, -5.0), (5.5, 1.5), (1.0, 5.5),
               (-5.5, 3.0), (-1.0, -6.0)]},
    {"name": "Level 3: Dancing Stars", "par": 75, "moving": True,
     "stars": [(-4.0, -4.0), (4.0, -4.0), (4.0, 4.0), (-4.0, 4.0),
               (0.0, 5.8), (5.8, 0.0), (-5.8, 0.0)]},
]

COMMAND_ALIASES = {
    "TAKEOFF": "TAKEOFF", "TAKE OFF": "TAKEOFF",
    "LAND": "LAND",
    "FORWARD": "FORWARD", "FWD": "FORWARD",
    "BACKWARD": "BACKWARD", "BACK": "BACKWARD",
    "LEFT": "ROTATE LEFT", "ROTATE LEFT": "ROTATE LEFT",
    "TURN LEFT": "ROTATE LEFT",
    "RIGHT": "ROTATE RIGHT", "ROTATE RIGHT": "ROTATE RIGHT",
    "TURN RIGHT": "ROTATE RIGHT",
    "HOVER": "HOVER", "STOP": "HOVER", "HOLD": "HOVER",
    "RESET": "RESET",
}

# Seconds between repeats of the same gesture command.
GESTURE_COOLDOWN = {"FORWARD": 0.12, "BACKWARD": 0.12,
                    "ROTATE LEFT": 0.12, "ROTATE RIGHT": 0.12,
                    "HOVER": 0.5, "TAKEOFF": 1.0, "LAND": 1.0,
                    "RESET": 1.5}


# =============================================================
# SOUND EFFECTS (generated in code, no files needed)
# =============================================================

class SoundBox:

    def __init__(self):
        self.enabled = False
        self.muted = False
        self.sounds = {}
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(22050, -16, 1, 512)
            rate, _size, channels = pygame.mixer.get_init()
            self.rate, self.channels = rate, channels
            self.sounds = {
                "collect": self._tone([(880, .06), (1175, .06), (1568, .12)]),
                "takeoff": self._tone([(392, .07), (523, .07), (659, .12)]),
                "land": self._tone([(659, .07), (523, .07), (392, .14)]),
                "boing": self._tone([(330, .06), (220, .12)], wave="square",
                                    volume=.18),
                "oops": self._tone([(300, .1), (250, .14)], volume=.22),
                "pop": self._tone([(988, .05), (1319, .07)], volume=.25),
                "start": self._tone([(523, .08), (659, .08), (784, .16)]),
                "win": self._tone([(523, .1), (659, .1), (784, .1),
                                   (1047, .32)]),
            }
            self.enabled = True
        except Exception as error:   # no audio device: play silently
            print("Sound disabled:", error)

    def _tone(self, notes, volume=0.32, wave="sine"):
        samples = array("h")
        for freq, duration in notes:
            count = int(self.rate * duration)
            for i in range(count):
                t = i / self.rate
                envelope = min(1.0, i / 150) * (1 - i / count) ** 1.5
                wave_value = math.sin(2 * math.pi * freq * t)
                if wave == "square":
                    wave_value = 0.6 if wave_value > 0 else -0.6
                value = int(wave_value * envelope * volume * 32767)
                for _ in range(self.channels):
                    samples.append(value)
        return pygame.mixer.Sound(buffer=samples.tobytes())

    def play(self, name):
        if self.enabled and not self.muted and name in self.sounds:
            self.sounds[name].play()


# =============================================================
# SIMULATOR
# =============================================================

class Simulator3D:

    def __init__(self, drone, controller=None):
        pygame.mixer.pre_init(22050, -16, 1, 512)
        pygame.init()

        self.width, self.height = 1200, 750
        # Fullscreen on a computer, normal window in a web browser
        if sys.platform == "emscripten":
            flags = 0
        else:
            flags = pygame.FULLSCREEN | pygame.SCALED
        self.screen = pygame.display.set_mode((self.width, self.height), flags)
        pygame.display.set_caption("GestureFly 3D - Star Quest")
        pygame.key.set_repeat(220, 110)   # hold a key to keep flying

        self.clock = pygame.time.Clock()
        self.drone = drone
        self.controller = controller
        self.sounds = SoundBox()
        self.running = True

        family = "comicsansms,trebuchetms,verdana,arial"
        self.big_font = pygame.font.SysFont(family, 46, bold=True)
        self.title_font = pygame.font.SysFont(family, 32, bold=True)
        self.subtitle_font = pygame.font.SysFont(family, 20, bold=True)
        self.info_font = pygame.font.SysFont(family, 18)
        self.small_font = pygame.font.SysFont(family, 15)

        # Input info
        self.input_source = "KEYBOARD"
        self.last_gesture = None
        self.last_gesture_time = {}

        # Look and feel
        self.skin_index = 0
        self.show_details = False   # TAB: grown-up numbers
        self.propeller_angle = 0.0
        self.particles = []
        self.floaters = []
        self.clouds = [[random.uniform(0, self.width), random.uniform(35, 140),
                        random.uniform(8, 18), random.uniform(0.7, 1.2)]
                       for _ in range(6)]
        self.sky = self._build_sky()
        self.world = self._build_world()

        # Game state
        self.mode = "TITLE"           # TITLE, QUEST, FREE
        self.phase = "PLAYING"        # PLAYING, WON (Star Quest only)
        self.level_index = 0
        self.stars = []
        self.score = 0
        self.score_at_level_start = 0
        self.timer_running = False
        self.timer_start = 0
        self.elapsed = 0.0
        self.rating = 0
        self.win_time = 0
        self.banner_text = ""
        self.banner_until = 0
        self.last_sound_time = {}

    # =========================================================
    # 3D PROJECTION
    # =========================================================

    def project(self, x, y, z):
        screen_x = CENTER_X + (x - y) * SCALE
        screen_y = CENTER_Y + (x + y) * SCALE * 0.5 - z * SCALE
        return int(screen_x), int(screen_y)

    # =========================================================
    # STATIC SCENERY (drawn once)
    # =========================================================

    def _build_sky(self):
        surf = pygame.Surface((self.width, self.height))
        for yy in range(self.height):
            k = min(1.0, yy / 520)
            color = [int(SKY_TOP[i] + (SKY_BOTTOM[i] - SKY_TOP[i]) * k)
                     for i in range(3)]
            pygame.draw.line(surf, color, (0, yy), (self.width, yy))

        # Sun
        for r, c in [(62, (255, 245, 200)), (48, (255, 232, 140)),
                     (38, (255, 214, 80))]:
            pygame.draw.circle(surf, c, (835, 78), r)

        # Rolling hills
        for cx, cy, r, c in [(120, 560, 300, (160, 222, 130)),
                             (640, 590, 340, (150, 214, 120)),
                             (1100, 560, 300, (160, 222, 130))]:
            pygame.draw.circle(surf, c, (cx, cy), r)
        pygame.draw.rect(surf, (140, 206, 112), (0, 600, self.width, 150))
        return surf

    def _draw_tree(self, surf, base):
        bx, by = base
        pygame.draw.rect(surf, (140, 95, 60), (bx - 5, by - 28, 10, 30),
                         border_radius=3)
        for ox, oy, r, c in [(-14, -38, 17, (70, 160, 80)),
                             (14, -38, 17, (70, 160, 80)),
                             (0, -55, 21, (90, 185, 95)),
                             (-4, -60, 8, (140, 215, 130))]:
            pygame.draw.circle(surf, c, (bx + ox, by + oy), r)

    def _build_world(self):
        surf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        # Trees along the far edges (behind the field)
        for ty in [-6.0, -2.0, 2.0, 5.5]:
            self._draw_tree(surf, self.project(-9.6, ty, 0))
        for tx in [-5.0, -1.0, 3.0, 6.5]:
            self._draw_tree(surf, self.project(tx, -9.6, 0))

        # Earthy side of the floating island
        f = FIELD
        top = [self.project(f, -f, 0), self.project(f, f, 0),
               self.project(-f, f, 0)]
        side = top + [(x, y + 18) for x, y in reversed(top)]
        pygame.draw.polygon(surf, (170, 120, 80), side)
        pygame.draw.lines(surf, (130, 90, 60), False,
                          [(x, y + 18) for x, y in top], 3)

        # Checkerboard grass
        for i in range(-f, f):
            for j in range(-f, f):
                tile = [self.project(i, j, 0), self.project(i + 1, j, 0),
                        self.project(i + 1, j + 1, 0),
                        self.project(i, j + 1, 0)]
                color = GRASS_A if (i + j) % 2 == 0 else GRASS_B
                pygame.draw.polygon(surf, color, tile)

        # Little flowers
        rng = random.Random(7)
        for _ in range(45):
            fx, fy = rng.uniform(-7.6, 7.6), rng.uniform(-7.6, 7.6)
            if abs(fx) < 2 and abs(fy) < 2:
                continue   # keep the home pad clear
            px, py = self.project(fx, fy, 0)
            petal = rng.choice([(255, 255, 255), (255, 170, 200),
                                (255, 230, 110), (200, 170, 255)])
            for ox, oy in [(-3, 0), (3, 0), (0, -2), (0, 2)]:
                pygame.draw.circle(surf, petal, (px + ox, py + oy), 2)
            pygame.draw.circle(surf, (255, 190, 40), (px, py), 2)

        # Wooden fence
        edges = [((-f, -f), (f, -f)), ((f, -f), (f, f)),
                 ((f, f), (-f, f)), ((-f, f), (-f, -f))]
        for (x1, y1), (x2, y2) in edges:
            posts = 9
            tops = []
            for k in range(posts):
                px = x1 + (x2 - x1) * k / (posts - 1)
                py = y1 + (y2 - y1) * k / (posts - 1)
                bottom = self.project(px, py, 0)
                tip = self.project(px, py, 0.45)
                pygame.draw.line(surf, (150, 100, 60), bottom, tip, 5)
                tops.append(tip)
            pygame.draw.lines(surf, (190, 135, 85), False, tops, 4)
            mids = [self.project(x1 + (x2 - x1) * k / 8,
                                 y1 + (y2 - y1) * k / 8, 0.22)
                    for k in range(9)]
            pygame.draw.lines(surf, (190, 135, 85), False, mids, 3)
        return surf

    # =========================================================
    # TEXT HELPERS
    # =========================================================

    def draw_text(self, text, position, font, color=INK, center=False,
                  shadow=None):
        surface = font.render(text, True, color)
        rect = surface.get_rect()
        position = (int(position[0]), int(position[1]))
        if center:
            rect.center = position
        else:
            rect.topleft = position
        if shadow:
            shade = font.render(text, True, shadow)
            self.screen.blit(shade, rect.move(2, 2))
        self.screen.blit(surface, rect)
        return rect

    def wrap(self, text, font, width):
        lines, current = [], ""
        for word in text.split():
            test = (current + " " + word).strip()
            if font.size(test)[0] <= width:
                current = test
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def draw_star_shape(self, center, radius, fill, outline=None, width=2):
        cx, cy = center
        points = []
        for i in range(10):
            angle = -math.pi / 2 + i * math.pi / 5
            r = radius if i % 2 == 0 else radius * 0.48
            points.append((cx + math.cos(angle) * r,
                           cy + math.sin(angle) * r))
        pygame.draw.polygon(self.screen, fill, points)
        if outline:
            pygame.draw.polygon(self.screen, outline, points, width)

    def card(self, rect, fill=(255, 255, 255), border=YELLOW, alpha=235):
        panel = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, (*fill, alpha), panel.get_rect(),
                         border_radius=18)
        pygame.draw.rect(panel, border, panel.get_rect(), 4, border_radius=18)
        self.screen.blit(panel, rect.topleft)

    # =========================================================
    # EFFECTS
    # =========================================================

    def burst(self, pos, count=26, colors=CONFETTI, speed=220):
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            spd = random.uniform(60, speed)
            life = random.uniform(0.6, 1.1)
            self.particles.append({
                "x": pos[0], "y": pos[1],
                "vx": math.cos(angle) * spd,
                "vy": math.sin(angle) * spd - 90,
                "life": life, "max": life,
                "color": random.choice(colors),
                "size": random.randint(3, 6), "rect": False})

    def confetti_rain(self, count=4):
        for _ in range(count):
            life = random.uniform(1.6, 2.6)
            self.particles.append({
                "x": random.uniform(0, 900), "y": -10,
                "vx": random.uniform(-40, 40), "vy": random.uniform(60, 140),
                "life": life, "max": life,
                "color": random.choice(CONFETTI),
                "size": random.randint(4, 7), "rect": True})

    def float_text(self, text, pos, color=ORANGE):
        self.floaters.append({"text": text, "x": pos[0], "y": pos[1],
                              "life": 1.2, "color": color})

    def update_effects(self, dt):
        for p in self.particles:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vy"] += (60 if p["rect"] else 320) * dt
            p["life"] -= dt
        self.particles = [p for p in self.particles if p["life"] > 0]

        for f in self.floaters:
            f["y"] -= 45 * dt
            f["life"] -= dt
        self.floaters = [f for f in self.floaters if f["life"] > 0]

        for cloud in self.clouds:
            cloud[0] += cloud[2] * dt
            if cloud[0] > self.width + 80:
                cloud[0] = -80

    def play(self, name, min_gap=0.0):
        now = pygame.time.get_ticks() / 1000
        if now - self.last_sound_time.get(name, -99) >= min_gap:
            self.last_sound_time[name] = now
            self.sounds.play(name)

    def show_banner(self, text, seconds=2.5):
        self.banner_text = text
        self.banner_until = pygame.time.get_ticks() + int(seconds * 1000)

    # =========================================================
    # GAME FLOW
    # =========================================================

    def start_quest(self):
        self.mode = "QUEST"
        self.score = 0
        self.start_level(0)
        self.play("start")

    def start_free(self):
        self.mode = "FREE"
        self.phase = "PLAYING"
        self.drone.reset()
        self.particles.clear()
        self.show_banner("Free Flight: explore anywhere!")
        self.play("start")

    def start_level(self, index):
        self.level_index = index
        level = LEVELS[index]
        self.drone.reset()
        self.stars = [{"bx": x, "by": y, "x": x, "y": y, "collected": False,
                       "phase": k * 1.3}
                      for k, (x, y) in enumerate(level["stars"])]
        self.phase = "PLAYING"
        self.timer_running = False
        self.elapsed = 0.0
        self.rating = 0
        self.score_at_level_start = self.score
        self.particles.clear()
        self.show_banner(level["name"])

    def restart_level(self):
        self.score = self.score_at_level_start
        self.start_level(self.level_index)
        self.play("pop")

    def next_level(self):
        if self.level_index + 1 < len(LEVELS):
            self.start_level(self.level_index + 1)
            self.play("start")
        else:
            self.start_quest()

    def win_level(self):
        self.phase = "WON"
        self.timer_running = False
        self.win_time = pygame.time.get_ticks()
        par = LEVELS[self.level_index]["par"]
        if self.elapsed <= par:
            self.rating = 3
        elif self.elapsed <= par * 1.6:
            self.rating = 2
        else:
            self.rating = 1
        self.score += self.rating * 20
        self.play("win")
        self.burst(self.project(self.drone.x, self.drone.y, 0.5), count=60)

    def all_stars_found(self):
        return bool(self.stars) and all(s["collected"] for s in self.stars)

    def update_game(self, now, just_landed):
        if self.mode != "QUEST":
            return

        if self.phase == "WON":
            if now - self.win_time < 2500:
                self.confetti_rain()
            return

        if self.timer_running:
            self.elapsed = (now - self.timer_start) / 1000

        # Dancing stars on level 3
        t = now / 1000
        if LEVELS[self.level_index]["moving"]:
            for s in self.stars:
                if not s["collected"]:
                    s["x"] = s["bx"] + 0.9 * math.cos(t * 0.7 + s["phase"])
                    s["y"] = s["by"] + 0.9 * math.sin(t * 0.7 + s["phase"])

        d = self.drone
        if d.z > 0.3:
            for s in self.stars:
                if s["collected"]:
                    continue
                if math.hypot(d.x - s["x"], d.y - s["y"]) <= COLLECT_RADIUS:
                    s["collected"] = True
                    self.score += 10
                    pos = self.project(s["x"], s["y"], 0.8)
                    self.burst(pos, colors=[YELLOW, (255, 240, 150), ORANGE])
                    self.float_text("+10", pos, (255, 170, 0))
                    self.play("collect")
                    if self.all_stars_found():
                        self.show_banner("All stars! Fly home to the H!")

        if self.all_stars_found() and d.state == "LANDED":
            if math.hypot(d.x, d.y) <= HOME_RADIUS:
                self.win_level()
            elif just_landed:
                self.float_text("Land on the H pad!",
                                self.project(d.x, d.y, 1.2), INK)

    # =========================================================
    # COMMANDS (keyboard and gestures both come through here)
    # =========================================================

    def normalize_command(self, command):
        if not command:
            return None
        key = str(command).upper().replace("_", " ").replace("-", " ")
        return COMMAND_ALIASES.get(" ".join(key.split()))

    def execute_command(self, command, source="KEYBOARD"):
        cmd = self.normalize_command(command)
        if cmd is None:
            return False
        self.input_source = source
        now = pygame.time.get_ticks()

        # Title screen: a takeoff starts the game (works with gestures too)
        if self.mode == "TITLE":
            if cmd == "TAKEOFF":
                self.start_quest()
                return True
            return False

        # Level finished: takeoff goes to the next level
        if self.mode == "QUEST" and self.phase == "WON":
            if cmd == "TAKEOFF" and now - self.win_time > 1500:
                self.next_level()
                return True
            return False

        if cmd == "RESET":
            if self.mode == "QUEST":
                self.restart_level()
            else:
                self.drone.reset()
                self.play("pop")
            return True

        actions = {
            "TAKEOFF": self.drone.takeoff,
            "LAND": self.drone.land,
            "FORWARD": self.drone.forward,
            "BACKWARD": self.drone.backward,
            "ROTATE LEFT": self.drone.rotate_left,
            "ROTATE RIGHT": self.drone.rotate_right,
            "HOVER": self.drone.hover,
        }
        ok = actions[cmd]()
        result = self.drone.last_result

        if cmd == "TAKEOFF" and ok:
            self.play("takeoff")
            if self.mode == "QUEST" and not self.timer_running:
                self.timer_running = True
                self.timer_start = now
        elif "BOUNDARY" in result:
            self.play("boing", 0.35)
            self.float_text("Boing!", self.project(self.drone.x,
                                                   self.drone.y,
                                                   self.drone.z + 1.2))
        elif not ok and "BLOCKED" in result:
            self.play("oops", 0.8)
        return ok

    def poll_controller(self):
        if self.controller is None:
            return
        try:
            command = self.controller.get_command()
        except Exception as error:
            print("Gesture controller error:", error)
            return
        gesture = getattr(self.controller, "last_gesture", None)
        if gesture:
            self.last_gesture = str(gesture)

        cmd = self.normalize_command(command)
        if cmd is None:
            return
        now = pygame.time.get_ticks() / 1000
        if now - self.last_gesture_time.get(cmd, -99) < GESTURE_COOLDOWN[cmd]:
            return
        self.last_gesture_time[cmd] = now
        self.execute_command(cmd, source="HANDS")

    # =========================================================
    # DRAWING: WORLD
    # =========================================================

    def draw_clouds(self):
        for cx, cy, _speed, size in self.clouds:
            for ox, oy, r in [(-22, 5, 17), (0, -6, 23), (22, 5, 17),
                              (2, 10, 19)]:
                pygame.draw.circle(self.screen, WHITE,
                                   (int(cx + ox * size), int(cy + oy * size)),
                                   int(r * size))

    def draw_home_pad(self, now):
        glow = self.mode == "QUEST" and self.all_stars_found()
        if glow:
            r = 1.65 + 0.15 * math.sin(now / 160)
            ring = [self.project(r * math.cos(a / 20 * 2 * math.pi),
                                 r * math.sin(a / 20 * 2 * math.pi), 0.02)
                    for a in range(20)]
            pygame.draw.polygon(self.screen, (255, 240, 150), ring)

        pad = [self.project(1.3 * math.cos(a / 24 * 2 * math.pi),
                            1.3 * math.sin(a / 24 * 2 * math.pi), 0.03)
               for a in range(24)]
        pygame.draw.polygon(self.screen, WHITE, pad)
        pygame.draw.polygon(self.screen, ORANGE if glow else (255, 160, 90),
                            pad, 4)

        h_color = (255, 120, 60)
        for a, b in [((-0.45, -0.45), (-0.45, 0.45)),
                     ((0.45, -0.45), (0.45, 0.45)),
                     ((-0.45, 0.0), (0.45, 0.0))]:
            pygame.draw.line(self.screen, h_color, self.project(*a, 0.05),
                             self.project(*b, 0.05), 5)

    def draw_trail(self, now):
        trail = self.drone.trail[-120:]
        if len(trail) < 2:
            return
        points = [self.project(x, y, z) for x, y, z in trail]
        n = len(points)
        for i in range(1, n):
            hue = (i * 0.02 + now / 4000) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 1.0)
            width = 2 + int(4 * i / n)
            pygame.draw.line(self.screen, (int(r * 255), int(g * 255),
                                           int(b * 255)),
                             points[i - 1], points[i], width)

    def draw_stars(self, now):
        if self.mode != "QUEST":
            return
        t = now / 1000
        for k, s in enumerate(self.stars):
            if s["collected"]:
                continue
            bob = 0.15 * math.sin(t * 2.5 + k)
            gx, gy = self.project(s["x"], s["y"], 0)
            pygame.draw.ellipse(self.screen, (95, 165, 80),
                                (gx - 14, gy - 5, 28, 10))
            sx, sy = self.project(s["x"], s["y"], 0.8 + bob)
            radius = 17 + 2 * math.sin(t * 4 + k)
            glow = pygame.Surface((80, 80), pygame.SRCALPHA)
            pygame.draw.circle(glow, (255, 240, 140, 110), (40, 40),
                               int(radius + 10))
            self.screen.blit(glow, (sx - 40, sy - 40))
            self.draw_star_shape((sx, sy), radius, (255, 205, 50),
                                 (255, 245, 180), 3)
            pygame.draw.circle(self.screen, (110, 75, 30), (sx - 5, sy - 1), 2)
            pygame.draw.circle(self.screen, (110, 75, 30), (sx + 5, sy - 1), 2)
            pygame.draw.arc(self.screen, (110, 75, 30),
                            (sx - 5, sy, 10, 7), math.pi, 2 * math.pi, 2)

    def draw_guide_arrow(self, now):
        """Bouncy arrow next to the drone that points at the next goal."""
        if self.mode != "QUEST" or self.phase != "PLAYING":
            return
        d = self.drone
        remaining = [s for s in self.stars if not s["collected"]]
        if remaining:
            target = min(remaining,
                         key=lambda s: math.hypot(s["x"] - d.x, s["y"] - d.y))
            tx, ty = target["x"], target["y"]
            color = (255, 175, 0)
        else:
            tx, ty = 0.0, 0.0
            color = (255, 100, 60)
        if math.hypot(tx - d.x, ty - d.y) < 1.4:
            return

        start = self.project(d.x, d.y, d.z)
        end = self.project(tx, ty, d.z)
        vx, vy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(vx, vy) or 1
        ux, uy = vx / length, vy / length
        dist = 52 + 6 * math.sin(now / 150)
        tip = (start[0] + ux * (dist + 14), start[1] + uy * (dist + 14))
        back = (start[0] + ux * dist, start[1] + uy * dist)
        left = (back[0] - uy * 9, back[1] + ux * 9)
        right = (back[0] + uy * 9, back[1] - ux * 9)
        pygame.draw.polygon(self.screen, color, [tip, left, right])
        pygame.draw.polygon(self.screen, WHITE, [tip, left, right], 2)

    def draw_drone(self, now):
        d = self.drone
        t = now / 1000
        skin_name, body, trim = DRONE_SKINS[self.skin_index]

        bob = 0.08 * math.sin(t * 3.2) if d.state == "FLYING" else 0.0
        wobble = (math.sin(t * 45) * 0.15 * d.bump_timer / d.bump_duration
                  if d.bump_timer > 0 else 0.0)
        x, y, z = d.x + wobble, d.y - wobble, d.z + bob

        center = self.project(x, y, z)
        ground = self.project(d.x, d.y, 0)

        # Soft shadow (shrinks as the drone climbs)
        size = max(14, 34 - d.z * 5)
        shadow = pygame.Surface((int(size * 2), int(size)), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (40, 70, 40, 90), shadow.get_rect())
        self.screen.blit(shadow, (int(ground[0] - size), int(ground[1] - size / 2)))

        # Dotted height line
        if d.z > 0.05:
            steps = max(2, int((ground[1] - center[1]) / 8))
            for i in range(0, steps, 2):
                py = ground[1] + (center[1] - ground[1]) * i / steps
                pygame.draw.circle(self.screen, (255, 255, 255),
                                   (ground[0], int(py)), 2)

        yaw = math.radians(d.display_yaw)
        fx, fy = math.sin(yaw), math.cos(yaw)
        rx, ry = -math.cos(yaw), math.sin(yaw)

        # Arms and propellers (in world space so they turn in 3D)
        arm = 0.75
        rotors = []
        for a, b in [(1, 1), (1, -1), (-1, 1), (-1, -1)]:
            wx = x + (fx * a + rx * b) * arm
            wy = y + (fy * a + ry * b) * arm
            rotors.append(self.project(wx, wy, z))
        for rp in rotors:
            pygame.draw.line(self.screen, trim, center, rp, 6)
        spinning = d.state in ("FLYING", "TAKING OFF", "LANDING")
        for index, (px, py) in enumerate(rotors):
            disc = pygame.Surface((36, 18), pygame.SRCALPHA)
            pygame.draw.ellipse(disc, (255, 255, 255, 120 if spinning else 60),
                                disc.get_rect())
            self.screen.blit(disc, (px - 18, py - 9))
            pygame.draw.circle(self.screen, (70, 80, 100), (px, py), 5)
            angle = math.radians(self.propeller_angle + index * 45)
            dx, dy = math.cos(angle) * 15, math.sin(angle) * 7
            pygame.draw.line(self.screen, (60, 70, 90),
                             (px - dx, py - dy), (px + dx, py + dy), 3)

        # Body
        body_rect = pygame.Rect(0, 0, 50, 34)
        body_rect.center = center
        pygame.draw.ellipse(self.screen, body, body_rect)
        pygame.draw.ellipse(self.screen, trim, body_rect, 3)
        pygame.draw.ellipse(self.screen, (255, 255, 255),
                            (center[0] - 14, center[1] - 13, 14, 7))

        # Which way is "forward" on screen?
        front = self.project(x + fx, y + fy, z)
        sx, sy = front[0] - center[0], front[1] - center[1]
        slen = math.hypot(sx, sy) or 1
        sx, sy = sx / slen, sy / slen

        # Nose so kids can see which way it will fly
        nose_base = (center[0] + sx * 24, center[1] + sy * 17)
        nose_tip = (center[0] + sx * 38, center[1] + sy * 27)
        pygame.draw.polygon(self.screen, ORANGE, [
            nose_tip,
            (nose_base[0] - sy * 7, nose_base[1] + sx * 7),
            (nose_base[0] + sy * 7, nose_base[1] - sx * 7)])

        # Face: eyes look the way the drone is heading, and it blinks
        blinking = (now % 3600) < 120
        for ex in (-9, 9):
            eye = (center[0] + ex + int(sx * 3), center[1] - 3 + int(sy * 2))
            if blinking:
                pygame.draw.line(self.screen, INK, (eye[0] - 5, eye[1]),
                                 (eye[0] + 5, eye[1]), 2)
            else:
                pygame.draw.circle(self.screen, WHITE, eye, 7)
                pygame.draw.circle(self.screen, INK,
                                   (eye[0] + int(sx * 3), eye[1] + int(sy * 3)),
                                   4)
        pygame.draw.arc(self.screen, INK, (center[0] - 7, center[1] + 1, 14, 9),
                        math.pi * 1.1, math.pi * 1.9, 2)

    def draw_particles(self):
        for p in self.particles:
            k = p["life"] / p["max"]
            size = max(1, int(p["size"] * (0.4 + 0.6 * k)))
            if p["rect"]:
                pygame.draw.rect(self.screen, p["color"],
                                 (int(p["x"]), int(p["y"]), size, size * 2))
            else:
                pygame.draw.circle(self.screen, p["color"],
                                   (int(p["x"]), int(p["y"])), size)
        for f in self.floaters:
            self.draw_text(f["text"], (f["x"], f["y"]), self.subtitle_font,
                           f["color"], center=True, shadow=WHITE)

    # =========================================================
    # DRAWING: SCREENS AND PANELS
    # =========================================================

    def draw_header(self):
        self.draw_star_shape((50, 45), 20, YELLOW, ORANGE)
        self.draw_text("GestureFly 3D", (80, 20), self.title_font, INK,
                       shadow=WHITE)
        self.draw_text("Let's fly, explore and collect stars!", (82, 62),
                       self.info_font, INK_SOFT)

        if self.mode == "QUEST":
            label, color = "STAR QUEST", (255, 150, 40)
        elif self.mode == "FREE":
            label, color = "FREE FLIGHT", (70, 170, 255)
        else:
            label, color = "WELCOME!", GREEN
        pill = pygame.Rect(0, 0, 170, 36)
        pill.topright = (1180, 30)
        pygame.draw.rect(self.screen, color, pill, border_radius=18)
        self.draw_text(label, pill.center, self.subtitle_font, WHITE,
                       center=True)
        if self.sounds.muted:
            self.draw_text("Sound off (M)", (pill.left - 125, 38),
                           self.small_font, INK_SOFT)

    def draw_quest_panel(self):
        if self.mode != "QUEST":
            return
        rect = pygame.Rect(28, 100, 300, 112)
        self.card(rect)
        level = LEVELS[self.level_index]
        self.draw_text(level["name"], (44, 108), self.subtitle_font,
                       (230, 120, 20))
        for k, s in enumerate(self.stars):
            pos = (52 + k * 32, 152)
            if s["collected"]:
                self.draw_star_shape(pos, 13, YELLOW, ORANGE)
            else:
                self.draw_star_shape(pos, 13, (225, 230, 240),
                                     (180, 190, 210))
        self.draw_text(f"Time: {int(self.elapsed)}s", (44, 176),
                       self.info_font, INK)
        self.draw_text(f"Points: {self.score}", (170, 176), self.info_font,
                       INK)

    def friendly_last_move(self):
        cmd, res = self.drone.last_command, self.drone.last_result
        if "NOT FLYING" in res:
            return "Oops! Take off first."
        if "NOT LANDED" in res:
            return "We're already flying!"
        if res in ("ALREADY LANDED", "ALREADY LANDING"):
            return "We're already going down."
        if "BOUNDARY" in res:
            return "Boing! That's the fence."
        if cmd == "TAKEOFF":
            return "Ready to fly!" if res == "COMPLETED" else "Up, up and away!"
        if cmd == "LAND":
            return "Nice landing!" if res == "COMPLETED" else "Coming down..."
        return {"FORWARD": "Zoom forward!", "BACKWARD": "Backing up!",
                "ROTATE LEFT": "Turned left!", "ROTATE RIGHT": "Turned right!",
                "HOVER": "Stopped and hovering.",
                "RESET": "Back to the start!"}.get(cmd, "Let's go!")

    def next_tip(self):
        d = self.drone
        hands = " (or show the takeoff hand sign)" if self.controller else ""
        if self.mode == "TITLE":
            return "Press ENTER to start Star Quest" + hands + "!"
        if self.mode == "QUEST" and self.phase == "WON":
            return "Press ENTER for the next level" + hands + "!"
        quest = self.mode == "QUEST"
        if d.state == "LANDED":
            if quest and self.all_stars_found():
                return "Take off and land on the H pad to finish!"
            return "Press T to take off" + hands + "!"
        if d.state == "TAKING OFF":
            return "Going up... get ready!"
        if d.state == "LANDING":
            return "Landing... gently does it!"
        if quest and self.all_stars_found():
            return "Follow the red arrow home, then press L to land on the H!"
        if quest:
            return "Follow the yellow arrow to the next star!"
        return "Fly anywhere you like! Press L to land."

    def draw_helper_panel(self):
        x, y, w, h = 915, 100, 265, 565
        self.card(pygame.Rect(x, y, w, h), border=(120, 190, 255))
        d = self.drone
        self.draw_text("PILOT HELPER", (x + 20, y + 14), self.subtitle_font,
                       (60, 120, 220))

        # Flight state badge
        state_info = {"LANDED": ("On the ground", (120, 200, 140)),
                      "TAKING OFF": ("Going up!", (90, 170, 255)),
                      "FLYING": ("Flying!", (255, 165, 50)),
                      "LANDING": ("Coming down", (190, 140, 255))}
        text, color = state_info.get(d.state, (d.state, INK_SOFT))
        badge = pygame.Rect(x + 20, y + 48, w - 40, 40)
        pygame.draw.rect(self.screen, color, badge, border_radius=20)
        self.draw_text(text, badge.center, self.subtitle_font, WHITE,
                       center=True)

        # Height bar
        self.draw_text("Height", (x + 20, y + 100), self.info_font, INK)
        bar = pygame.Rect(x + 90, y + 105, w - 110, 16)
        pygame.draw.rect(self.screen, (225, 232, 245), bar, border_radius=8)
        fill = bar.copy()
        fill.width = int(bar.width * min(1.0, d.z / d.max_altitude))
        if fill.width > 0:
            pygame.draw.rect(self.screen, (90, 175, 255), fill,
                             border_radius=8)

        # Next step
        self.draw_text("What to do next", (x + 20, y + 138),
                       self.small_font, INK_SOFT)
        tip_box = pygame.Rect(x + 15, y + 158, w - 30, 92)
        pygame.draw.rect(self.screen, (255, 248, 215), tip_box,
                         border_radius=12)
        for i, line in enumerate(self.wrap(self.next_tip(), self.info_font,
                                           w - 50)[:4]):
            self.draw_text(line, (x + 25, y + 164 + i * 21), self.info_font,
                           INK)

        # Last move
        self.draw_text("Last move", (x + 20, y + 262), self.small_font,
                       INK_SOFT)
        self.draw_text(self.friendly_last_move(), (x + 20, y + 280),
                       self.info_font, (230, 110, 30))

        # Input
        self.draw_text("Flying with", (x + 20, y + 316), self.small_font,
                       INK_SOFT)
        source = "Your hands" if self.input_source == "HANDS" else "Keyboard"
        self.draw_text(source, (x + 20, y + 334), self.info_font, INK)
        if self.controller is not None:
            gesture = self.last_gesture or "No hand seen"
            self.draw_text("Camera sees: " + gesture, (x + 20, y + 358),
                           self.small_font, (60, 120, 220))

        # Drone colour
        self.draw_text("Drone colour", (x + 20, y + 388), self.small_font,
                       INK_SOFT)
        skin_name, body, trim = DRONE_SKINS[self.skin_index]
        pygame.draw.circle(self.screen, body, (x + 32, y + 418), 11)
        pygame.draw.circle(self.screen, trim, (x + 32, y + 418), 11, 3)
        self.draw_text(f"{skin_name}  (C to change)", (x + 50, y + 408),
                       self.info_font, INK)

        # Grown-up details
        if self.show_details:
            rows = [f"STATE {d.state}",
                    f"X {d.x:.1f}   Y {d.y:.1f}   Z {d.z:.1f}",
                    f"YAW {d.yaw:.0f}",
                    f"CMD {d.last_command}",
                    f"RESULT {d.last_result}"]
            for i, row in enumerate(rows):
                self.draw_text(row, (x + 20, y + 445 + i * 22),
                               self.small_font, INK_SOFT)
        else:
            self.draw_text("TAB: show flight numbers", (x + 20, y + 530),
                           self.small_font, INK_SOFT)

    def draw_controls(self):
        keys = [("T", "Take off"), ("L", "Land"), ("UP/DN", "Fly"),
                ("LT/RT", "Turn"), ("SPACE", "Stop"), ("R", "Restart"),
                ("C", "Colour"), ("G", "Game/Free"), ("M", "Sound")]
        bar = pygame.Rect(20, 690, 1160, 44)
        self.card(bar, border=(200, 220, 245), alpha=225)
        px = 36
        for key, label in keys:
            key_surf = self.small_font.render(key, True, WHITE)
            cap = pygame.Rect(px, 699, key_surf.get_width() + 16, 26)
            pygame.draw.rect(self.screen, (90, 120, 200), cap,
                             border_radius=7)
            self.screen.blit(key_surf, key_surf.get_rect(center=cap.center))
            label_rect = self.draw_text(label, (cap.right + 6, 702),
                                        self.small_font, INK)
            px = label_rect.right + 18

    def draw_banner(self, now):
        if now > self.banner_until or not self.banner_text:
            return
        remaining = (self.banner_until - now) / 1000
        lift = int(10 * math.sin(now / 200))
        surf = self.title_font.render(self.banner_text, True, INK)
        rect = pygame.Rect(0, 0, surf.get_width() + 60, 60)
        rect.center = (450, 255 + lift)
        alpha = int(235 * min(1.0, remaining / 0.5))
        self.card(rect, alpha=alpha)
        self.screen.blit(surf, surf.get_rect(center=rect.center))

    def draw_overlay(self, now):
        if self.mode == "TITLE":
            dim = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            dim.fill((40, 70, 120, 90))
            self.screen.blit(dim, (0, 0))
            card = pygame.Rect(0, 0, 640, 330)
            card.center = (600, 370)
            self.card(card, alpha=245)
            bounce = int(8 * math.sin(now / 250))
            self.draw_star_shape((card.centerx - 230, card.top + 60 + bounce),
                                 26, YELLOW, ORANGE)
            self.draw_star_shape((card.centerx + 230, card.top + 60 - bounce),
                                 26, YELLOW, ORANGE)
            self.draw_text("GestureFly 3D", (card.centerx, card.top + 62),
                           self.big_font, INK, center=True)
            self.draw_text("Help the little drone find all the stars!",
                           (card.centerx, card.top + 120), self.info_font,
                           INK_SOFT, center=True)
            lines = ["ENTER  =  Play Star Quest",
                     "F  =  Free Flight",
                     "C  =  Pick your drone colour"]
            if self.controller is not None:
                lines.insert(1, "or show the TAKEOFF hand sign to play")
            for i, line in enumerate(lines):
                self.draw_text(line, (card.centerx, card.top + 170 + i * 34),
                               self.subtitle_font, (60, 120, 220),
                               center=True)

        elif self.mode == "QUEST" and self.phase == "WON":
            card = pygame.Rect(0, 0, 520, 280)
            card.center = (450, 330)
            self.card(card, alpha=245)
            last = self.level_index + 1 >= len(LEVELS)
            title = "YOU'RE A STAR PILOT!" if last else "AMAZING PILOT!"
            self.draw_text(title, (card.centerx, card.top + 45),
                           self.title_font, (230, 110, 20), center=True)
            for i in range(3):
                pos = (card.centerx - 70 + i * 70,
                       card.top + 115 - (12 if i == 1 else 0))
                if i < self.rating:
                    self.draw_star_shape(pos, 30, YELLOW, ORANGE, 3)
                else:
                    self.draw_star_shape(pos, 30, (225, 230, 240),
                                         (180, 190, 210), 3)
            self.draw_text(f"Time: {int(self.elapsed)} seconds    "
                           f"Points: {self.score}",
                           (card.centerx, card.top + 175), self.info_font,
                           INK, center=True)
            nxt = "play again from Level 1" if last else "go to the next level"
            self.draw_text(f"Press ENTER to {nxt}",
                           (card.centerx, card.top + 225),
                           self.subtitle_font, (60, 120, 220), center=True)

    # =========================================================
    # DRAW EVERYTHING
    # =========================================================

    def draw(self, now):
        self.screen.blit(self.sky, (0, 0))
        self.draw_clouds()
        self.screen.blit(self.world, (0, 0))
        self.draw_home_pad(now)
        self.draw_stars(now)
        self.draw_trail(now)
        self.draw_drone(now)
        self.draw_guide_arrow(now)
        self.draw_particles()
        self.draw_header()
        self.draw_quest_panel()
        self.draw_helper_panel()
        self.draw_controls()
        self.draw_banner(now)
        self.draw_overlay(now)
        pygame.display.flip()

    # =========================================================
    # EVENTS
    # =========================================================

    KEY_COMMANDS = {
        pygame.K_t: "TAKEOFF", pygame.K_l: "LAND",
        pygame.K_UP: "FORWARD", pygame.K_w: "FORWARD",
        pygame.K_DOWN: "BACKWARD", pygame.K_s: "BACKWARD",
        pygame.K_LEFT: "ROTATE LEFT", pygame.K_a: "ROTATE LEFT",
        pygame.K_RIGHT: "ROTATE RIGHT", pygame.K_d: "ROTATE RIGHT",
        pygame.K_SPACE: "HOVER", pygame.K_r: "RESET",
    }

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.KEYDOWN:
                key = event.key

                if key == pygame.K_ESCAPE:
                    self.running = False

                elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if self.mode == "TITLE":
                        self.start_quest()
                    elif self.mode == "QUEST" and self.phase == "WON":
                        if pygame.time.get_ticks() - self.win_time > 600:
                            self.next_level()

                elif key == pygame.K_g:
                    if self.mode == "QUEST":
                        self.start_free()
                    else:
                        self.start_quest()

                elif key == pygame.K_f:
                    self.start_free()

                elif key == pygame.K_c:
                    self.skin_index = (self.skin_index + 1) % len(DRONE_SKINS)
                    self.play("pop")
                    d = self.drone
                    self.float_text(DRONE_SKINS[self.skin_index][0] + "!",
                                    self.project(d.x, d.y, d.z + 1.4),
                                    DRONE_SKINS[self.skin_index][2])

                elif key == pygame.K_m:
                    self.sounds.muted = not self.sounds.muted

                elif key == pygame.K_TAB:
                    self.show_details = not self.show_details

                elif key in self.KEY_COMMANDS:
                    self.execute_command(self.KEY_COMMANDS[key], "KEYBOARD")

    # =========================================================
    # MAIN LOOP
    # =========================================================

    async def run(self):
        while self.running:
            dt = min(self.clock.tick(60) / 1000.0, 0.05)
            now = pygame.time.get_ticks()

            self.handle_events()
            self.poll_controller()

            previous_state = self.drone.state
            self.drone.update(dt)
            just_landed = (previous_state == "LANDING"
                           and self.drone.state == "LANDED")
            if just_landed:
                self.play("land")

            self.update_game(now, just_landed)
            self.update_effects(dt)

            if self.drone.state in ("FLYING", "TAKING OFF", "LANDING"):
                self.propeller_angle += 1100 * dt

            self.draw(now)
            await asyncio.sleep(0)   # lets the game run in a web browser

        if self.controller is not None and hasattr(self.controller, "close"):
            try:
                self.controller.close()
            except Exception:
                pass
        pygame.quit()
