import asyncio

from drone_3d_new import Drone3D
from simulator_3d_new import Simulator3D


def make_gesture_controller():
    """
    Return your MediaPipe hand-gesture controller here, or None to play with
    the keyboard only.

    The controller just needs a get_command() method that returns a command
    string such as "TAKEOFF", "FORWARD" or "LEFT" (or None when no gesture is
    seen). Optional: a `last_gesture` attribute to show on screen, and a
    close() method to release the camera when the game ends.

    Note: MediaPipe does not run in the web version, so keep this returning
    None for the browser build.

    Example:
        from gesture_controller import GestureController
        return GestureController()
    """
    return None


async def main():
    print("==============================")
    print("   GESTUREFLY 3D - STAR QUEST")
    print("==============================")
    print("ENTER: play Star Quest   F: free flight")
    print("==============================")

    drone = Drone3D()
    simulator = Simulator3D(drone, controller=make_gesture_controller())
    await simulator.run()


if __name__ == "__main__":
    asyncio.run(main())
