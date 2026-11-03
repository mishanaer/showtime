---
name: showtime
description: Create, revise, or review short product videos from a real app. Use for app demos, storyboards, or existing Showtime videos. Supports iOS Simulator capture and local composition.
---

# Showtime

Demonstrate one product benefit through real app interaction. Continue existing work at the relevant stage; reuse footage when it supports the requested change.

## Starting a new video

1. Ask the user for their scenario and wait for it. Use the scenario they supply.
2. Once the scenario is available, ask which framing they want: top crop (`crop-top`), bottom crop (`crop-bottom`), or the whole phone (`full`). Wait for their choice before preparing the story for review.

Ask one question at a time, in the conversation language. Treat information already supplied by the user as answered.

## Defaults and boundaries

- Record the simulator at its native cadence. Watch scrolls and transitions at normal speed. Retake if the captured motion visibly stutters.
- Deliver a 10–20 second, 1920×1920, constant-60-fps MP4 with a readable result and a loop. No editorial titles or captions; app text stays visible. Different requested formats require checking backend support.
- Write the review copy of the user’s scenario, the saved scenario and on-screen content in English unless explicitly requested otherwise. Set `content.language` accordingly in both manifests. Explanations outside the scenario follow the user's conversation language.
- For new videos, obtain approval of the story, then the actual prepared materials. A direct correction request authorizes that scope without another permission question; use the correction receipt when recording again. Never mark unseen materials as user-reviewed.
- Keep complete native transitions, scrolling and reading time. Shorten idle waits or simplify the story to fit the duration.
- New videos use approved iPhone 17 or 18 family mockups and the bundled background palettes. Choose an available matching simulator model, phone color and background automatically; user preferences override these choices. Show the combination in the storyboard. See [Preparation](docs/preparation.md) for assets and capture matching.

- Apply the layout chosen by the user. Use `crop-top` for action near the top, `crop-bottom` for action near the composer, attachments or keyboard, and `full` to show the whole phone centered with 90 px above and below on a 1920 square. Use the selected framing in preparation, review and export.
- Text appears character by character. Keyboard keys must show no highlights, popup characters or tap indicators. [Standard typing driver](scripts/simulator/typing-driver.m) · [Usage](docs/showtime-execution.md#capture) · [Visual acceptance](docs/visual-acceptance.md)

## Workspace

This file's directory is the Showtime root (`SHOWTIME`). Resolve it and invoke scripts by absolute path. Follow the target app's instructions. Read the relevant run's `run.json` and artifacts; update progress with `scripts/showtime_run.py init/note/status`. Progress is not approval.

Keep the installed skill and assets read-only during production. Write into the video's run directory; preserve previous takes and other agents' work. Use historical pilots only when supplied as references.

Before production, run `scripts/showtime_doctor.py`; add `--record --axe <driver>` when recording. New runs use [the product profile](profiles/telegram-product.json); existing runs retain their configuration.

## Read only what the task needs

| Task | Reference |
| --- | --- |
| Plan a story, capture, edit or export | [Execution](docs/showtime-execution.md) — load the relevant section |
| Prepare a scene or storyboard | [Preparation](docs/preparation.md) — visual rules and evidence before review |
| Save approvals or authorize a correction | [Manifest and approval contracts](docs/review-workflow.md) |
| Review prepared frames or a finished video | [Visual acceptance](docs/visual-acceptance.md) |
| Adjust tap color, timing or size | [Tap asset](assets/tap/README.md) |

A finished delivery includes the MP4, source capture, action journal, observations and verification findings. Technical checks do not establish visual acceptance.
