"""Build a short sanitized GIF and MP4 walkthrough from tested dashboard captures."""

from __future__ import annotations

from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "artifacts" / "dashboard-demo"
FONT_REGULAR = Path(r"C:\Windows\Fonts\segoeui.ttf")
FONT_SEMIBOLD = Path(r"C:\Windows\Fonts\seguisb.ttf")

SCENES = [
    ("01-overview.png", "Decision", "IDR 100,000 claimed → IDR 40,000 reimbursable", 0),
    ("02-review.png", "Finance review", "One ready case · complete evidence · recommended action", 0),
    ("03-evidence.png", "Receipt evidence", "IDR 40,000 purchase · IDR 100,000 tendered · IDR 60,000 change", 0),
    ("04-policy.png", "Policy controls", "IDR 60,000 change excluded · four controls explain the variance", 0),
    ("05-models.png", "OCR quality", "Both pipelines match 11/11 fields · native confidence not provided", 0),
    ("06-ocr-inspector.png", "OCR Inspector · GRANDTOTAL 40.000 → 40,000 (IDR defaulted)", "Native confidence not provided · reveal is display-only, not authorization", 300),
    ("07-architecture.png", "Architecture pipeline", "Evidence → claim → parallel extraction → policy → decision", 0),
    ("08-trace.png", "Langfuse cloud trace", "Backend verified · 10 observations · 8 scores · 4 semantic KPIs · 0 mirror errors", 0),
]


def font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path.exists():
        return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def scene_frame(source: Path, title: str, subtitle: str, crop_top: int) -> Image.Image:
    image = Image.open(source).convert("RGB")
    width, height = image.size

    # Remove the persistent navigation rail so the task-specific panel stays
    # readable after the animation is reduced to its delivery resolution.
    left = min(250, max(0, width - 640))
    focus_width = width - left
    crop_height = min(height, round(focus_width * 9 / 16))
    crop_top = min(max(0, crop_top), max(0, height - crop_height))
    image = image.crop((left, crop_top, width, crop_top + crop_height)).resize((1280, 720), Image.Resampling.LANCZOS)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 620, 1280, 720), fill=(7, 27, 39, 255))
    draw.rectangle((0, 620, 9, 720), fill=(234, 183, 105, 255))
    draw.text((38, 639), title, font=font(FONT_SEMIBOLD, 26), fill=(248, 247, 242, 255))
    draw.text((38, 677), subtitle, font=font(FONT_REGULAR, 15), fill=(172, 190, 195, 255))
    draw.text((1190, 647), "RECEIPT", font=font(FONT_SEMIBOLD, 10), fill=(234, 183, 105, 255))
    draw.text((1190, 666), "REVIEW", font=font(FONT_SEMIBOLD, 10), fill=(234, 183, 105, 255))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def write_gif(frames: list[Image.Image], output: Path) -> None:
    gif_frames = [
        frame.resize((960, 540), Image.Resampling.LANCZOS).quantize(
            colors=128,
            method=Image.Quantize.MEDIANCUT,
        )
        for frame in frames
    ]
    durations = [3000] * len(gif_frames)

    gif_frames[0].save(
        output,
        save_all=True,
        append_images=gif_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


def write_mp4(frames: list[Image.Image], output: Path) -> None:
    with imageio.get_writer(
        output,
        fps=24,
        codec="libx264",
        quality=8,
        pixelformat="yuv420p",
        macro_block_size=16,
    ) as writer:
        for frame in frames:
            frame_array = np.asarray(frame)
            for _ in range(72):
                writer.append_data(frame_array)


def main() -> None:
    missing = [str(MEDIA / filename) for filename, _, _, _ in SCENES if not (MEDIA / filename).exists()]
    if missing:
        raise FileNotFoundError(f"Missing tested dashboard captures: {missing}")

    frames = [scene_frame(MEDIA / filename, title, subtitle, crop_top) for filename, title, subtitle, crop_top in SCENES]
    gif_path = MEDIA / "receipt-review-demo.gif"
    mp4_path = MEDIA / "receipt-review-demo.mp4"
    poster_path = MEDIA / "receipt-review-demo-poster.png"
    verification_path = MEDIA / "verification-midframe.png"

    write_gif(frames, gif_path)
    write_mp4(frames, mp4_path)
    frames[0].save(poster_path, optimize=True)
    frames[5].save(verification_path, optimize=True)

    print(f"GIF: {gif_path} ({gif_path.stat().st_size} bytes)")
    print(f"MP4: {mp4_path} ({mp4_path.stat().st_size} bytes)")
    print(f"Poster: {poster_path} ({poster_path.stat().st_size} bytes)")
    print(f"Verification: {verification_path} ({verification_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
