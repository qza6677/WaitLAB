# Cookie sprite assets

These files were prepared from the original 4×3 sheet without regenerating or
redrawing the cat.

- `sprites-256/`: normalized master sprites (256×256 transparent PNG).
- `sprites-96/`: 96×96 copies for the desktop pet and compact UI.
- `sheet-transparent.png`: the complete sheet with its original layout.
- `preview.png`: a light-background contact sheet for visual checking.
- `manifest.json`: source cells, state names, canvas size and anchor metadata.

States are in row-major order:

1. `idle` — idle/standby
2. `waiting` — waiting for an AI response
3. `working` — actively writing or researching
4. `paused` — micro-task paused/resting
5. `attention` — needs attention
6. `ai-complete` — AI response finished
7. `error` — connection or task error
8. `task-complete` — micro-task completed
9. `curious` — uncertain/asking for input
10. `offline` — network unavailable
11. `update-available` — an update is ready
12. `updating` — update in progress

The original file in the parent directory is not overwritten.
