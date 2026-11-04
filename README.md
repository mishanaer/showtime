# showtime

Create app demo videos with your AI agent.

[![Example: Telegram attachment menu](assets/examples/attach-menu-duck.gif)](assets/examples/attach-menu-duck.mp4)

[Full-quality video](assets/examples/attach-menu-duck.mp4).

## How it works

Showtime is a local toolkit that turns a scenario into a recorded app demo. 

AI agent prepares the scene, operates the app in iOS Simulator, and reviews the result. Python scripts record the screen, keep actions aligned with the footage, edit the take, and compose the finished video.

After you approve the scenario, preparation and quality checks happen inside the workflow. The video you receive is the finished MP4, with the phone frame, background, and tap indicators already included.

## How to use

1. [Install showtime](docs/install.md).
2. Open your app project in your AI agent. For example, ask “Use `~/showtime/SKILL.md` to record how I save a note.”
3. Choose whether to show the whole phone or just the top or bottom of the screen.
4. Approve the scenario. The agent prepares, records, and checks your demo, then sends the finished MP4.
