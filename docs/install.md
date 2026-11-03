# Install showtime for Claude Code

Use a Mac with [Claude Code](https://code.claude.com/docs/en/quickstart), [Homebrew](https://brew.sh), and Xcode. Install an iOS Simulator runtime in Xcode and run your app on an iPhone simulator before recording.

## One-time setup

Run these commands in Terminal. They install FFmpeg and its ffprobe tool, Python 3.12, and [AXe for simulator input](https://www.axe-cli.com/docs/installation). Cloning into the [personal skills directory](https://code.claude.com/docs/en/skills#choose-where-skills-load) makes showtime available across your projects.

```sh
brew install ffmpeg python@3.12 cameroncooke/axe/axe
mkdir -p ~/.claude/skills
git clone https://github.com/mishanaer/showtime.git ~/.claude/skills/showtime
cd ~/.claude/skills/showtime
"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/showtime_doctor.py --record
axe list-simulators
```

Look for `"ok": true` in the doctor output and your running simulator in the AXe list. If a dependency is missing, follow the doctor's `next_action` and run it again.

## Make a video

Open Terminal in the project for the **app you want to record**. Launch Claude Code with the showtime environment active:

```sh
source ~/.claude/skills/showtime/.venv/bin/activate
claude
```

Type `/showtime`. When Claude asks for a scenario, describe what happens on screen. For example: “Open a saved note, change its title, and show the new title in the list.” Choose the full phone, top crop, or bottom crop. Approve the scenario and storyboard before recording. Ask Claude for the exported MP4 in the video's run directory.

To update showtime, run `git -C ~/.claude/skills/showtime pull --ff-only` and install the requirements again using the same virtual environment.
