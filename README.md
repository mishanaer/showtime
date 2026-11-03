# showtime

Record a 10–20 second demo of your app in iOS Simulator. showtime adds a phone frame and tap indicators and exports a 1920 × 1920 MP4 at 60 fps.

## How to use

1. [Install showtime on your Mac](docs/install.md).
2. Open your iOS app project in an AI agent that can read local files and run commands. Ask it to follow `~/showtime/SKILL.md` to record a demo.
3. Describe what you want to show. When prompted, choose the whole phone, the top of the screen, or the bottom.
4. Approve the scenario, then review and approve the storyboard. After recording, the agent gives you the path to the MP4.

## How it works

1. **Prepare the scene.** After you approve the scenario, the agent sets up the app and builds a storyboard from real screens in your chosen framing.
2. **Record the interaction.** Once you approve the storyboard, the agent performs the actions in iOS Simulator. showtime records the screen and logs where and when each tap occurs.
3. **Edit the take.** The agent watches the recording to decide where to cut. It trims idle time, keeps transitions at their original speed, and leaves the result on screen long enough to read.
4. **Export and review.** showtime adds the phone frame, background, and timed tap indicators. It checks the MP4’s dimensions, duration, and frame rate. The agent watches the video on repeat to catch rough cuts or a visible jump at the loop.
