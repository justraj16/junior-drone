import math


class Drone3D:
    """
    Simple drone model for GestureFly 3D.

    The public command methods (takeoff, land, hover, forward, backward,
    rotate_left, rotate_right, reset) keep the same names and True/False
    return values as before, so any keyboard or MediaPipe hand-gesture code
    that calls them keeps working.

    What changed to make it friendlier for kids:
      * Moves glide smoothly to their new spot instead of teleporting.
      * Turning is animated (display_yaw eases toward yaw).
      * Hitting the edge of the play area is a soft "boing" bump: the drone
        slides along the fence instead of the move simply being refused.
      * HOVER now means "stop right here", cancelling any glide in progress.
    """

    MAX_TRAIL_POINTS = 300

    def __init__(self):
        # Position (x, y are the smoothed, visible position)
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0

        # Where the drone is gliding to
        self.target_x = 0.0
        self.target_y = 0.0

        # Orientation: yaw is the real heading, display_yaw is the animated one
        self.yaw = 0.0
        self.display_yaw = 0.0

        # Flight state
        self.state = "LANDED"

        # Altitude settings
        self.target_altitude = 3.0
        self.boundary = 7.5
        self.max_altitude = 5.0

        # Movement
        self.vertical_speed = 1.5
        self.move_speed = 0.65
        self.rotation_speed = 15.0
        self.glide_rate = 8.0      # higher = snappier glide
        self.turn_rate = 10.0      # higher = snappier turn animation

        # Fun feedback
        self.bump_timer = 0.0      # > 0 while the "boing" wobble plays
        self.bump_duration = 0.45

        # Information
        self.last_command = "NONE"
        self.last_result = "READY"

        # Flight trail
        self.trail = []

    # =========================================================
    # UPDATE
    # =========================================================

    def update(self, dt):
        # TAKEOFF
        if self.state == "TAKING OFF":
            self.z += self.vertical_speed * dt
            if self.z >= self.target_altitude:
                self.z = self.target_altitude
                self.state = "FLYING"
                self.last_command = "TAKEOFF"
                self.last_result = "COMPLETED"

        # LANDING
        elif self.state == "LANDING":
            self.z -= self.vertical_speed * dt
            if self.z <= 0:
                self.z = 0
                self.state = "LANDED"
                self.last_command = "LAND"
                self.last_result = "COMPLETED"

        # Smooth glide toward the target position
        glide = 1.0 - math.exp(-self.glide_rate * dt)
        self.x += (self.target_x - self.x) * glide
        self.y += (self.target_y - self.y) * glide
        if abs(self.target_x - self.x) < 0.001:
            self.x = self.target_x
        if abs(self.target_y - self.y) < 0.001:
            self.y = self.target_y

        # Smooth turn (always the short way round)
        turn = 1.0 - math.exp(-self.turn_rate * dt)
        diff = (self.yaw - self.display_yaw + 180) % 360 - 180
        self.display_yaw = (self.display_yaw + diff * turn) % 360

        if self.bump_timer > 0:
            self.bump_timer = max(0.0, self.bump_timer - dt)

        if self.z > 0:
            self._add_trail_point()

    # =========================================================
    # TAKEOFF / LAND / HOVER
    # =========================================================

    def takeoff(self):
        if self.state != "LANDED":
            self.last_command = "TAKEOFF"
            self.last_result = "BLOCKED - NOT LANDED"
            return False

        self.state = "TAKING OFF"
        self.last_command = "TAKEOFF"
        self.last_result = "TAKING OFF"
        self.trail = [(self.x, self.y, self.z)]
        return True

    def land(self):
        if self.state == "LANDED":
            self.last_command = "LAND"
            self.last_result = "ALREADY LANDED"
            return False

        if self.state == "LANDING":
            self.last_command = "LAND"
            self.last_result = "ALREADY LANDING"
            return False

        self.state = "LANDING"
        self.last_command = "LAND"
        self.last_result = "LANDING"
        return True

    def hover(self):
        if self.state != "FLYING":
            self.last_command = "HOVER"
            self.last_result = "BLOCKED - NOT FLYING"
            return False

        # Stop exactly where we are.
        self.target_x = self.x
        self.target_y = self.y
        self.last_command = "HOVER"
        self.last_result = "EXECUTED"
        return True

    # =========================================================
    # MOVE
    # =========================================================

    def forward(self):
        return self._move(+1, "FORWARD")

    def backward(self):
        return self._move(-1, "BACKWARD")

    def _move(self, direction, name):
        if self.state != "FLYING":
            self.last_command = name
            self.last_result = "BLOCKED - NOT FLYING"
            return False

        angle = math.radians(self.yaw)
        new_x = self.target_x + direction * math.sin(angle) * self.move_speed
        new_y = self.target_y + direction * math.cos(angle) * self.move_speed

        # Slide along the fence instead of refusing the move.
        clamped_x = max(-self.boundary, min(self.boundary, new_x))
        clamped_y = max(-self.boundary, min(self.boundary, new_y))
        hit_fence = (clamped_x != new_x) or (clamped_y != new_y)
        moved = (abs(clamped_x - self.target_x) > 1e-6
                 or abs(clamped_y - self.target_y) > 1e-6)

        self.last_command = name

        if hit_fence:
            self.bump_timer = self.bump_duration

        if not moved:
            self.last_result = "BLOCKED - BOUNDARY"
            return False

        self.target_x = clamped_x
        self.target_y = clamped_y
        self.last_result = "BUMPED BOUNDARY" if hit_fence else "EXECUTED"
        return True

    # =========================================================
    # ROTATE
    # =========================================================

    def rotate_left(self):
        if self.state != "FLYING":
            self.last_command = "ROTATE LEFT"
            self.last_result = "BLOCKED - NOT FLYING"
            return False

        self.yaw = (self.yaw + self.rotation_speed) % 360
        self.last_command = "ROTATE LEFT"
        self.last_result = "EXECUTED"
        return True

    def rotate_right(self):
        if self.state != "FLYING":
            self.last_command = "ROTATE RIGHT"
            self.last_result = "BLOCKED - NOT FLYING"
            return False

        self.yaw = (self.yaw - self.rotation_speed) % 360
        self.last_command = "ROTATE RIGHT"
        self.last_result = "EXECUTED"
        return True

    # =========================================================
    # RESET
    # =========================================================

    def reset(self):
        self.x = self.y = self.z = 0.0
        self.target_x = self.target_y = 0.0
        self.yaw = 0.0
        self.display_yaw = 0.0
        self.state = "LANDED"
        self.bump_timer = 0.0
        self.last_command = "RESET"
        self.last_result = "EXECUTED"
        self.trail = []

    # =========================================================
    # HELPERS
    # =========================================================

    def _check_boundary(self, new_x, new_y):
        return (-self.boundary <= new_x <= self.boundary
                and -self.boundary <= new_y <= self.boundary)

    def _add_trail_point(self):
        point = (self.x, self.y, self.z)

        if not self.trail:
            self.trail.append(point)
            return

        last = self.trail[-1]
        distance = math.sqrt((self.x - last[0]) ** 2
                             + (self.y - last[1]) ** 2
                             + (self.z - last[2]) ** 2)

        if distance >= 0.1:
            self.trail.append(point)
            if len(self.trail) > self.MAX_TRAIL_POINTS:
                del self.trail[0]
