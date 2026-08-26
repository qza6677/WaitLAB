"""Split and normalize the Cookie sprite sheet.

This is intentionally a deterministic, local asset-preparation step.  It does
not redraw the cat, so the generated sprites keep the exact pixels from the
source sheet.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


STATE_NAMES = [
    "idle",
    "waiting",
    "working",
    "paused",
    "attention",
    "ai-complete",
    "error",
    "task-complete",
    "curious",
    "offline",
    "update-available",
    "updating",
]


def component_details(mask: np.ndarray) -> list[tuple[int, tuple[int, int, int, int], bool]]:
    """Return ``(area, bbox, touches_edge)`` for every 8-connected component."""

    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    components: list[tuple[int, tuple[int, int, int, int], bool]] = []

    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(y, x)])
            visited[y, x] = True
            area = 0
            min_x = max_x = x
            min_y = max_y = y
            while queue:
                cy, cx = queue.popleft()
                area += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if (
                            0 <= ny < height
                            and 0 <= nx < width
                            and mask[ny, nx]
                            and not visited[ny, nx]
                        ):
                            visited[ny, nx] = True
                            queue.append((ny, nx))
            components.append(
                (
                    area,
                    (min_x, min_y, max_x + 1, max_y + 1),
                    min_x == 0 or min_y == 0 or max_x == width - 1 or max_y == height - 1,
                )
            )

    return components


def largest_component_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return the bounding box of the largest 8-connected true component."""

    components = component_details(mask)
    if not components:
        return None
    return max(components, key=lambda item: item[0])[1]


def remove_boundary_fragments(cell: Image.Image, threshold: int = 8) -> Image.Image:
    """Drop small components that touch a tile edge.

    The source sheet has no gutters.  A few anti-aliased pixels from a cat in
    the neighbouring row can consequently land in the next tile.  The main cat
    component is retained even when it touches an edge; only small edge
    fragments are removed.
    """

    rgba = np.asarray(cell.convert("RGBA")).copy()
    alpha = rgba[..., 3]
    mask = alpha > threshold
    components = component_details(mask)
    if not components:
        return Image.fromarray(rgba, mode="RGBA")
    largest_area = max(area for area, _, _ in components)
    keep = np.zeros(mask.shape, dtype=bool)
    # Re-run a small flood fill for each component so we can construct a mask;
    # this keeps the implementation dependency-free (no OpenCV/scipy needed).
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(y, x)])
            visited[y, x] = True
            pixels: list[tuple[int, int]] = []
            touches_edge = False
            while queue:
                cy, cx = queue.popleft()
                pixels.append((cy, cx))
                touches_edge = touches_edge or cy in (0, height - 1) or cx in (0, width - 1)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if (
                            0 <= ny < height
                            and 0 <= nx < width
                            and mask[ny, nx]
                            and not visited[ny, nx]
                        ):
                            visited[ny, nx] = True
                            queue.append((ny, nx))
            if not (touches_edge and len(pixels) < max(500, largest_area * 0.12)):
                for py, px in pixels:
                    keep[py, px] = True

    rgba[..., 3] = np.where(keep, alpha, 0)
    return Image.fromarray(rgba, mode="RGBA")


def clean_alpha(image: Image.Image, threshold: int = 8) -> Image.Image:
    """Keep the supplied alpha channel and remove only near-transparent noise.

    The Cookie sheet already has a transparent background.  Deleting dark RGB
    values would damage the cat's pixel outlines, so transparency is determined
    from alpha rather than colour.
    """

    rgba = np.asarray(image.convert("RGBA")).copy()
    alpha = rgba[..., 3]
    alpha[alpha <= threshold] = 0
    rgba[..., 3] = alpha
    return Image.fromarray(rgba, mode="RGBA")


def paste_anchored(
    cell: Image.Image,
    cat_bbox: tuple[int, int, int, int],
    canvas_size: int,
    target_cat_height: int,
    target_center_x: float,
    target_baseline_y: float,
    margin: int = 8,
) -> tuple[Image.Image, dict[str, float]]:
    """Resize a cell and place it using the main cat component as an anchor."""

    cell_rgba = cell.convert("RGBA")
    alpha = np.asarray(cell_rgba)[..., 3]
    content_bbox = Image.fromarray((alpha > 0).astype(np.uint8) * 255).getbbox()
    if content_bbox is None:
        content_bbox = (0, 0, cell_rgba.width, cell_rgba.height)

    cat_x0, cat_y0, cat_x1, cat_y1 = cat_bbox
    cat_width = max(1, cat_x1 - cat_x0)
    cat_height = max(1, cat_y1 - cat_y0)
    # Keep every state inside the common canvas, while making cat height as
    # consistent as possible.  The constraints are calculated around the cat
    # anchor, so large props never push the cat sideways or vertically.
    cat_anchor_x = (cat_x0 + cat_x1) / 2
    cat_anchor_y = cat_y1
    rel_left = content_bbox[0] - cat_anchor_x
    rel_right = content_bbox[2] - cat_anchor_x
    rel_top = content_bbox[1] - cat_anchor_y
    rel_bottom = content_bbox[3] - cat_anchor_y
    max_scale = target_cat_height / cat_height
    if rel_left < 0:
        max_scale = min(max_scale, (target_center_x - margin) / (-rel_left))
    if rel_right > 0:
        max_scale = min(max_scale, (canvas_size - margin - target_center_x) / rel_right)
    if rel_top < 0:
        max_scale = min(max_scale, (target_baseline_y - margin) / (-rel_top))
    if rel_bottom > 0:
        max_scale = min(max_scale, (canvas_size - margin - target_baseline_y) / rel_bottom)
    scale = max(0.05, max_scale)
    scale = max(0.05, scale)
    new_size = (
        max(1, round(cell_rgba.width * scale)),
        max(1, round(cell_rgba.height * scale)),
    )
    resized = cell_rgba.resize(new_size, Image.Resampling.LANCZOS)

    scaled_cat = tuple(value * scale for value in cat_bbox)
    cat_center_x = (scaled_cat[0] + scaled_cat[2]) / 2
    cat_baseline_y = scaled_cat[3]
    offset_x = target_center_x - cat_center_x
    offset_y = target_baseline_y - cat_baseline_y

    scaled_content = tuple(value * scale for value in content_bbox)

    output = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    output.alpha_composite(resized, (round(offset_x), round(offset_y)))
    return output, {
        "scale": scale,
        "cat_center_x": (cat_center_x + offset_x),
        "cat_baseline_y": (cat_baseline_y + offset_y),
        "content_bbox": [
            scaled_content[0] + offset_x,
            scaled_content[1] + offset_y,
            scaled_content[2] + offset_x,
            scaled_content[3] + offset_y,
        ],
    }


def find_source(input_dir: Path) -> Path:
    candidates = [
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]
    if not candidates:
        raise FileNotFoundError(f"No PNG/JPEG/WebP file found in {input_dir}")
    return max(candidates, key=lambda path: path.stat().st_size)


def infer_row_edges(alpha: np.ndarray, rows: int) -> np.ndarray:
    """Find quiet horizontal gutters instead of assuming equal row heights.

    Generated sheets often place the last row a little higher than an exact
    1/3 split.  Selecting the lowest-alpha scanline around each expected split
    prevents the top of the next cat from leaking into the preceding tile.
    """

    height = alpha.shape[0]
    counts = (alpha > 8).sum(axis=1)
    edges = [0]
    for row in range(1, rows):
        expected = round(height * row / rows)
        radius = max(20, round(height * 0.06))
        start = max(edges[-1] + 1, expected - radius)
        end = min(height - (rows - row - 1) - 1, expected + radius)
        candidates = range(start, end + 1)
        edge = min(candidates, key=lambda value: (int(counts[value]), abs(value - expected)))
        edges.append(edge)
    edges.append(height)
    return np.asarray(edges, dtype=int)


def make_preview(sprites: list[Image.Image], output: Path, columns: int = 4) -> None:
    tile = sprites[0].width
    gap = 12
    rows = (len(sprites) + columns - 1) // columns
    preview = Image.new(
        "RGBA",
        (columns * tile + (columns + 1) * gap, rows * tile + (rows + 1) * gap),
        (244, 239, 232, 255),
    )
    draw = ImageDraw.Draw(preview)
    for index, sprite in enumerate(sprites):
        x = gap + (index % columns) * tile
        y = gap + (index // columns) * tile
        draw.rectangle((x, y, x + tile - 1, y + tile - 1), outline=(220, 211, 199, 255), width=1)
        preview.alpha_composite(sprite, (x, y))
    preview.convert("RGB").save(output, quality=95)


def process(input_dir: Path, output_dir: Path, columns: int, rows: int) -> Path:
    source = find_source(input_dir)
    source_image = Image.open(source).convert("RGBA")
    cleaned = clean_alpha(source_image)
    width, height = cleaned.size
    x_edges = np.rint(np.linspace(0, width, columns + 1)).astype(int)
    y_edges = infer_row_edges(np.asarray(cleaned)[..., 3], rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    master_dir = output_dir / "sprites-256"
    app_dir = output_dir / "sprites-96"
    master_dir.mkdir(exist_ok=True)
    app_dir.mkdir(exist_ok=True)
    cleaned.save(output_dir / "sheet-transparent.png")

    cells: list[Image.Image] = []
    bboxes: list[tuple[int, int, int, int]] = []
    for row in range(rows):
        for column in range(columns):
            left, right = int(x_edges[column]), int(x_edges[column + 1])
            top, bottom = int(y_edges[row]), int(y_edges[row + 1])
            cell = remove_boundary_fragments(cleaned.crop((left, top, right, bottom)))
            cells.append(cell)
            alpha = np.asarray(cell)[..., 3]
            bbox = largest_component_bbox(alpha > 8)
            if bbox is None:
                bbox = (0, 0, cell.width, cell.height)
            bboxes.append(bbox)

    target_cat_height = min(220, max(1, int(max(y1 - y0 for x0, y0, x1, y1 in bboxes))))
    sprites: list[Image.Image] = []
    manifest_states: list[dict[str, object]] = []
    for index, (cell, bbox) in enumerate(zip(cells, bboxes)):
        sprite, anchor = paste_anchored(
            cell,
            bbox,
            canvas_size=256,
            target_cat_height=target_cat_height,
            target_center_x=128,
            target_baseline_y=232,
        )
        name = STATE_NAMES[index] if index < len(STATE_NAMES) else f"state-{index + 1:02d}"
        sprite.save(master_dir / f"{index + 1:02d}-{name}.png")
        sprite.resize((96, 96), Image.Resampling.LANCZOS).save(app_dir / f"{index + 1:02d}-{name}.png")
        sprites.append(sprite)
        manifest_states.append(
            {
                "index": index + 1,
                "name": name,
                "row": index // columns + 1,
                "column": index % columns + 1,
                "source_bbox": [
                    int(x_edges[index % columns]),
                    int(y_edges[index // columns]),
                    int(x_edges[index % columns + 1]),
                    int(y_edges[index // columns + 1]),
                ],
                "cat_bbox_in_cell": list(map(int, bbox)),
                "anchor": anchor,
            }
        )

    make_preview(sprites, output_dir / "preview.png")
    manifest = {
        "source": str(source),
        "source_size": [width, height],
        "grid": {"columns": columns, "rows": rows},
        "alpha_threshold": 8,
        "canvas_size": 256,
        "app_size": 96,
        "target_cat_height": target_cat_height,
        "target_cat_center": [128, 232],
        "states": manifest_states,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_dir


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=project_root / "resources" / "Cookie")
    parser.add_argument(
        "--output", type=Path, default=project_root / "resources" / "Cookie" / "processed"
    )
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--rows", type=int, default=3)
    args = parser.parse_args()
    output = process(args.input, args.output, args.columns, args.rows)
    print(f"Processed Cookie sprites into {output}")


if __name__ == "__main__":
    main()
