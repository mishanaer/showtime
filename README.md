# showtime

Record a 10–20 second demo of your app in iOS Simulator. showtime adds a phone frame and tap indicators and exports a 1920 × 1920 MP4 at 60 fps.

## How to use

1. [Install showtime on your Mac](docs/install.md).
2. Open your app project in your AI agent. For example, ask “Use `~/showtime/SKILL.md` to record how I save a note.”
3. Choose whether to show the whole phone or just the top or bottom of the screen.
4. Approve the scenario, then the storyboard. The agent records your demo and tells you where to find the MP4.

## How it works

showtime combines instructions for your AI agent with scripts for recording and editing video.

The agent follows your scenario in iOS Simulator while the scripts record the screen and track each tap. The agent chooses where to cut. The scripts trim the recording, add the phone frame and tap indicators, and export the MP4.
