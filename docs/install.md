# Install showtime

Use a Mac with [Homebrew](https://brew.sh), Xcode, and an AI agent that can read the showtime and app directories and run local commands. Install an iOS Simulator runtime in Xcode and run your app on an iPhone simulator before recording.

## One-time setup

Run these commands in Terminal. They install FFmpeg and its ffprobe tool, Python 3.12, and [AXe for simulator input](https://www.axe-cli.com/docs/installation).

```sh
brew install ffmpeg python@3.12 cameroncooke/axe/axe
git clone https://github.com/mishanaer/showtime.git ~/showtime
cd ~/showtime
"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/showtime_doctor.py --record
axe list-simulators
```

Look for `"ok": true` in the doctor output and your running simulator in the AXe list. If a dependency is missing, follow the doctor's `next_action` and run it again.

## Make a video

Open the iOS app project in your agent and give it a prompt like this:

> Follow `~/showtime/SKILL.md` to record a demo. Use `~/showtime/.venv/bin/python` for the scripts. Show me opening a saved note, changing its title, and finding it in the list under the new title.

Choose the full phone, top crop, or bottom crop when asked. Approve the scenario, then the storyboard. After recording, the agent gives you the path to the finished MP4.

### Optional shortcut for Claude Code

To invoke showtime with `/showtime`, link the same installation to Claude Code's [personal skills directory](https://code.claude.com/docs/en/skills#choose-where-skills-load):

```sh
mkdir -p ~/.claude/skills
ln -s ~/showtime ~/.claude/skills/showtime
```

If you already installed showtime in that directory, keep using your existing installation.

## Update

Run `git -C ~/showtime pull --ff-only`, then `~/showtime/.venv/bin/python -m pip install -r ~/showtime/requirements.txt`.
