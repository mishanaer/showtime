# showtime

Record a 10–20 second demo of your app in iOS Simulator. showtime adds a phone frame and tap indicators and exports a 1920 × 1920 MP4 at 60 fps.

## How to use

1. [Install showtime on your Mac](docs/install.md).
2. Open your iOS app project in an AI agent that can read local files and run commands. Ask it to follow `~/showtime/SKILL.md` to record a demo.
3. Describe what you want to show. When prompted, choose the whole phone, the top of the screen, or the bottom.
4. Approve the scenario, then review and approve the storyboard. After recording, the agent gives you the path to the MP4.

## How it works

showtime combines instructions for your AI agent with scripts for recording and editing video.

The agent follows your scenario in iOS Simulator while the scripts record the screen and track each tap. The agent chooses where to cut. The scripts trim the recording, add the phone frame and tap indicators, and export the MP4.
