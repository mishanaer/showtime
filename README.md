# showtime

Create app demo videos with your AI agent

[![Example: Telegram attachment menu](assets/examples/attach-menu-duck.gif)](assets/examples/attach-menu-duck.mp4)

[Full-quality video](assets/examples/attach-menu-duck.mp4)

## How it works

showtime combines instructions for your AI agent with scripts for recording and editing video.

The agent follows your scenario in iOS Simulator while the scripts record the screen and track each tap. The agent chooses where to cut. The scripts trim the recording, add the phone frame and tap indicators, and export the MP4.

## How to use

1. [Install showtime](docs/install.md).
2. Open your app project in your AI agent. For example, ask “Use `~/showtime/SKILL.md` to record how I save a note.”
3. Choose whether to show the whole phone or just the top or bottom of the screen.
4. Approve the scenario. The agent prepares, records, and checks your demo, then sends the finished MP4.
