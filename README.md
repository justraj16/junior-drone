# GestureFly 3D - Star Quest

A kid-friendly drone simulator made with Python and Pygame. Fly the little drone, collect all the stars and land back on the H pad.

**Play in your browser:** https://YOUR-USERNAME.github.io/gesturefly-3d/

## Controls

| Key | Action |
|-----|--------|
| ENTER | Start Star Quest |
| F | Free Flight |
| T / L | Take off / Land |
| Arrow keys or WASD | Fly and turn |
| SPACE | Stop and hover |
| R | Restart level |
| C | Change drone colour |
| G | Switch Game / Free Flight |
| M | Sound on/off |
| TAB | Show flight numbers |
| ESC | Quit |

## Run it on your computer

```
pip install -r requirements.txt
python main.py
```

## Hand gestures

The game can be flown with hand gestures (MediaPipe) on a computer. See `make_gesture_controller()` in `main.py`. The web version uses the keyboard only.
