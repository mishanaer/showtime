# showtime

Record a real app interaction in iOS Simulator and turn it into a 10–20 second demo. showtime adds a phone frame and tap indicators, then checks the exported MP4 at 1920 × 1920 and 60 fps.

## Get started

Install Python 3.9 or later, FFmpeg, and ffprobe. Recording also requires Xcode, an iOS Simulator, and the AXe input driver.

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 scripts/showtime_doctor.py
```

Give `$showtime` your scenario and choose the full phone, top crop, or bottom crop. Approve the storyboard before recording.

[Recording guide](docs/showtime-execution.md)
