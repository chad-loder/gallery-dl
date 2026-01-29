# -*- coding: utf-8 -*-

# Copyright 2026 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Trim video files (e.g., remove preroll/watermarks)

Options:
    start               Trim start point (see formats below)
    end                 Trim end point (see formats below)
    scene-threshold     Scene detection sensitivity (0.0-1.0, default: 0.1)
    scene-window        Only detect scenes in first N seconds (default: 5.0)
    scene-index         Which scene change to use (default: 1 = first)
    scene-before        Only trim if scene is before this timestamp (seconds)
                        Prevents trimming content if no preroll detected
    scene-after         Only trim if scene is after this timestamp (seconds)
    ffmpeg-location     Path to ffmpeg executable
    ffprobe-location    Path to ffprobe executable
    ffmpeg-output       FFmpeg log level (default: "error")
    keep-original       Keep original file as .original (default: false)
    extensions          List of video extensions to process

Trim specification formats:
    1.56                Timestamp in seconds (number)
    "1.56"              Timestamp in seconds (string)
    "frame:39"          Frame number (converted using video FPS)
    "scene"             First scene change
    "scene:2"           Second scene change

Example config for Pond5 preroll removal:
    "postprocessors": [
        {
            "name": "trim",
            "start": "scene",
            "scene-threshold": 0.1,
            "scene-before": 3.0
        },
        {
            "name": "metadata",
            "extension-format": "{extension}.json"
        }
    ]

Note: List "trim" BEFORE "metadata" so trim fields appear in the JSON.
      Adds to kwdict: trim_start, trim_end, trim_method
"""

from .common import PostProcessor
from .. import util
import subprocess
import tempfile
import shutil
import os
import re


class TrimPP(PostProcessor):

    def __init__(self, job, options):
        PostProcessor.__init__(self, job)

        # FFmpeg location
        ffmpeg = options.get("ffmpeg-location")
        self.ffmpeg = util.expand_path(ffmpeg) if ffmpeg else "ffmpeg"

        ffprobe = options.get("ffprobe-location")
        self.ffprobe = util.expand_path(ffprobe) if ffprobe else "ffprobe"

        # Output verbosity
        self.output = options.get("ffmpeg-output", "error")

        # Keep original file as .original
        self.keep_original = options.get("keep-original", False)

        # Parse trim specification
        self.start = self._parse_trim_spec(options.get("start"))
        self.end = self._parse_trim_spec(options.get("end"))

        # Scene detection options (used when start/end is scene-based)
        self.scene_threshold = options.get("scene-threshold", 0.1)
        self.scene_window = options.get("scene-window", 5.0)
        self.scene_index = options.get("scene-index", 1)  # Which scene change

        # Safety bounds for scene detection
        # Only trim if scene is detected before this timestamp (seconds)
        # Prevents trimming actual content if there's no preroll
        self.scene_before = options.get("scene-before")  # e.g., 3.0
        # Only trim if scene is detected after this timestamp
        self.scene_after = options.get("scene-after")  # e.g., 0.5

        # Only process video files
        self.extensions = options.get(
            "extensions", ["mp4", "mkv", "webm", "avi", "mov"])

        # Register hook - use "file" event so trim fields are in kwdict
        # before metadata postprocessor writes JSON
        job.register_hooks({"file": self.trim}, options)

    def _parse_trim_spec(self, spec):
        """Parse a trim specification.

        Formats:
            None          -> no trim
            1.56          -> timestamp in seconds (float or int)
            "1.56"        -> timestamp in seconds (string)
            "frame:39"    -> frame number (converted to timestamp)
            "scene"       -> first scene change
            "scene:2"     -> second scene change
        """
        if spec is None:
            return None

        # Numeric value = timestamp in seconds
        if isinstance(spec, (int, float)):
            return ("timestamp", float(spec))

        spec = str(spec).strip()

        # Frame-based: "frame:39" or "frames:39"
        match = re.match(r"frames?:(\d+)", spec, re.IGNORECASE)
        if match:
            return ("frame", int(match.group(1)))

        # Scene-based: "scene" or "scene:2"
        match = re.match(r"scene(?::(\d+))?", spec, re.IGNORECASE)
        if match:
            index = int(match.group(1)) if match.group(1) else 1
            return ("scene", index)

        # Try to parse as float (timestamp)
        try:
            return ("timestamp", float(spec))
        except ValueError:
            self.log.warning("Invalid trim specification: %s", spec)
            return None

    def _get_video_fps(self, filepath):
        """Get video frame rate using ffprobe."""
        try:
            result = subprocess.run(
                [self.ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=r_frame_rate",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                # Parse "25/1" or "30000/1001" format
                fps_str = result.stdout.strip()
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    return float(num) / float(den)
                return float(fps_str)
        except Exception as exc:
            self.log.debug("Failed to get FPS: %s", exc)
        return 25.0  # Default fallback

    def _detect_scene_change(self, filepath, index=1):
        """Detect scene changes and return timestamp of the Nth one.

        Args:
            filepath: Path to video file
            index: Which scene change to return (1 = first, 2 = second, etc.)

        Returns:
            Timestamp in seconds, or None if not found
        """
        try:
            # Run ffmpeg scene detection on first N seconds
            cmd = [
                self.ffmpeg,
                "-i", filepath,
                "-vf", f"select='gt(scene,{self.scene_threshold})',showinfo",
                "-t", str(self.scene_window),
                "-f", "null", "-"
            ]

            self.log.debug("Running scene detection: %s", " ".join(cmd))

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60
            )

            # Parse pts_time from showinfo output
            # Example: [Parsed_showinfo_1 @ ...] n:   0 pts:  25088 pts_time:1.96
            timestamps = []
            for line in result.stderr.split("\n"):
                match = re.search(r"pts_time:(\d+\.?\d*)", line)
                if match:
                    timestamps.append(float(match.group(1)))

            if timestamps and len(timestamps) >= index:
                timestamp = timestamps[index - 1]
                self.log.debug(
                    "Scene detection found %d changes, using #%d at %.3fs",
                    len(timestamps), index, timestamp
                )

                # Apply safety bounds
                if self.scene_before is not None and timestamp > self.scene_before:
                    self.log.info(
                        "Scene at %.3fs is after scene-before limit (%.3fs), "
                        "skipping trim (probably not a preroll)",
                        timestamp, self.scene_before
                    )
                    return None

                if self.scene_after is not None and timestamp < self.scene_after:
                    self.log.info(
                        "Scene at %.3fs is before scene-after limit (%.3fs), "
                        "skipping trim",
                        timestamp, self.scene_after
                    )
                    return None

                return timestamp
            else:
                self.log.warning(
                    "Scene detection found only %d changes, requested #%d",
                    len(timestamps), index
                )
                return None

        except subprocess.TimeoutExpired:
            self.log.error("Scene detection timed out")
        except Exception as exc:
            self.log.error("Scene detection failed: %s", exc)

        return None

    def _resolve_trim_point(self, spec, filepath):
        """Convert a trim specification to a timestamp in seconds."""
        if spec is None:
            return None

        mode, value = spec

        if mode == "timestamp":
            return value

        elif mode == "frame":
            fps = self._get_video_fps(filepath)
            timestamp = value / fps
            self.log.debug(
                "Frame %d at %.2f fps = %.3f seconds", value, fps, timestamp
            )
            return timestamp

        elif mode == "scene":
            return self._detect_scene_change(filepath, index=value)

        return None

    def _get_trim_method(self):
        """Return a human-readable description of the trim method used."""
        parts = []
        if self.start:
            mode, value = self.start
            if mode == "scene":
                parts.append(f"scene:{value}@threshold={self.scene_threshold}")
            elif mode == "frame":
                parts.append(f"frame:{value}")
            else:
                parts.append(f"timestamp:{value}")
        return ",".join(parts) if parts else "unknown"

    def trim(self, pathfmt):
        """Trim the video file."""
        # Check if this is a video file
        ext = pathfmt.extension.lower() if pathfmt.extension else ""
        if ext not in self.extensions:
            self.log.debug("Skipping non-video file: %s", pathfmt.filename)
            return

        # During "file" event, the file is at temppath (before finalize)
        # Use temppath if it exists, otherwise fall back to realpath
        filepath = pathfmt.temppath or pathfmt.realpath

        # Check file exists
        if not os.path.exists(filepath):
            self.log.warning("File not found: %s", filepath)
            return

        # Resolve trim points
        start_time = self._resolve_trim_point(self.start, filepath)
        end_time = self._resolve_trim_point(self.end, filepath)

        if start_time is None and end_time is None:
            self.log.debug("No trim points specified, skipping")
            return

        self.log.info(
            "Trimming %s (start=%.3fs, end=%s)",
            pathfmt.filename,
            start_time or 0,
            f"{end_time:.3f}s" if end_time else "none"
        )

        # Build ffmpeg command
        with tempfile.NamedTemporaryFile(
            suffix=f".{ext}", delete=False
        ) as tmp:
            tmp_path = tmp.name

        try:
            cmd = [self.ffmpeg]

            # Input seeking (fast, but may be inaccurate for some codecs)
            if start_time:
                cmd += ["-ss", str(start_time)]

            cmd += ["-i", filepath]

            # Output duration limit
            if end_time:
                if start_time:
                    cmd += ["-t", str(end_time - start_time)]
                else:
                    cmd += ["-t", str(end_time)]

            # Copy streams without re-encoding
            cmd += ["-c", "copy"]

            # Avoid negative timestamps
            cmd += ["-avoid_negative_ts", "make_zero"]

            # Output options
            if isinstance(self.output, str):
                cmd += ["-loglevel", self.output]

            # Overwrite output
            cmd += ["-y", tmp_path]

            self.log.debug("Running: %s", " ".join(cmd))

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )

            if result.returncode != 0:
                self.log.error(
                    "FFmpeg failed (exit %d): %s",
                    result.returncode, result.stderr[-500:] if result.stderr else ""
                )
                return

            # Replace original with trimmed version
            if self.keep_original:
                # Save original next to final destination
                original_path = pathfmt.realpath + ".original"
                shutil.copy2(filepath, original_path)
                self.log.debug("Kept original as: %s", original_path)

            # Replace the temp file with trimmed version
            # (finalize() will move it to realpath)
            shutil.move(tmp_path, filepath)
            self.log.debug("Trimmed file saved: %s", filepath)

            # Add trim metadata to kwdict for metadata postprocessor
            # NOTE: For this to appear in JSON, "trim" must be listed
            # BEFORE "metadata" in the postprocessors config
            pathfmt.kwdict["trim_start"] = start_time
            pathfmt.kwdict["trim_end"] = end_time
            pathfmt.kwdict["trim_method"] = self._get_trim_method()

        except subprocess.TimeoutExpired:
            self.log.error("FFmpeg timed out")
        except Exception as exc:
            self.log.error("Trim failed: %s: %s", exc.__class__.__name__, exc)
        finally:
            # Clean up temp file if it still exists
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


__postprocessor__ = TrimPP
