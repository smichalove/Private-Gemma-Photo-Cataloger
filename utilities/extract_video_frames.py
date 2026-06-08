import os
import sys
import cv2
from typing import List, Dict, Union

# Reconfigure console encoding and enable line buffering to prevent CP1252 errors on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
        sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except AttributeError:
        pass


def timecode_to_seconds(timecode: str) -> float:
    """Parses a timecode string in HH:MM:SS or MM:SS format into total seconds.

    Args:
        timecode: The timecode string (e.g., '00:01:17' or '01:17' or '77.5').

    Returns:
        The total number of seconds as a float.

    Raises:
        ValueError: If the timecode format is invalid or empty.
    """
    timecode = timecode.strip()
    if not timecode:
        raise ValueError("Empty timecode string.")

    # Check if the user inputted a raw numeric value in seconds
    if ":" not in timecode:
        try:
            return float(timecode)
        except ValueError:
            raise ValueError(f"Invalid timecode format: {timecode}")

    parts: List[str] = timecode.split(":")
    if len(parts) == 2:
        # Format is MM:SS or MM:SS.mmm
        minutes: int = int(parts[0])
        seconds: float = float(parts[1])
        return minutes * 60.0 + seconds
    elif len(parts) == 3:
        # Format is HH:MM:SS or HH:MM:SS.mmm
        hours: int = int(parts[0])
        minutes: int = int(parts[1])
        seconds: float = float(parts[2])
        return hours * 3600.0 + minutes * 60.0 + seconds
    else:
        raise ValueError(f"Invalid timecode format (too many colons): {timecode}")


def seconds_to_timecode(seconds: float) -> str:
    """Converts a float number of seconds back into an HH:MM:SS timecode.

    Args:
        seconds: The number of seconds.

    Returns:
        The formatted timecode string.

    Raises:
        None
    """
    hours: int = int(seconds // 3600)
    minutes: int = int((seconds % 3600) // 60)
    remaining_seconds: float = seconds % 60.0
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:06.3f}"


def extract_frames(video_path: str, start_time: str, end_time: str, output_dir: str) -> None:
    """Extracts all video frames between the specified start and end timecodes.

    It saves them as uncompressed .bmp images to maximize clarity for VLM analysis.

    Args:
        video_path: The absolute path to the input video file.
        start_time: The start timecode (e.g., '00:01:17').
        end_time: The end timecode (e.g., '00:01:50').
        output_dir: The directory where extracted frames will be saved.

    Returns:
        None

    Raises:
        FileNotFoundError: If the input video file does not exist.
        ValueError: If the video cannot be opened, or frame rates are invalid.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Input video file not found: {video_path}")

    # Open the video file using OpenCV
    cap: cv2.VideoCapture = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}")

    try:
        # Retrieve video details
        fps: float = float(cap.get(cv2.CAP_PROP_FPS))
        total_frames: int = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_seconds: float = total_frames / fps if fps > 0 else 0.0

        if fps <= 0:
            raise ValueError(f"Invalid frame rate (FPS = {fps}) detected in video.")

        # Parse start and end times to seconds
        start_seconds: float = timecode_to_seconds(start_time)
        end_seconds: float = timecode_to_seconds(end_time)

        # Validate timecodes
        if start_seconds < 0:
            start_seconds = 0.0
        if end_seconds > duration_seconds:
            end_seconds = duration_seconds

        if start_seconds >= end_seconds:
            raise ValueError(f"Start time ({start_time}) must be earlier than end time ({end_time}).")

        # Calculate exact start and end frame indices
        start_frame: int = int(start_seconds * fps)
        end_frame: int = int(end_seconds * fps)

        # Cap the end frame at total_frames
        if end_frame > total_frames:
            end_frame = total_frames

        num_frames: int = end_frame - start_frame

        print("\n==================================================")
        print("          Video Frame Extraction Plan             ")
        print("==================================================")
        print(f"Video File:     {os.path.basename(video_path)}")
        print(f"Frame Rate:     {fps:.2f} FPS")
        print(f"Total Duration: {seconds_to_timecode(duration_seconds)} ({duration_seconds:.2f}s)")
        print(f"Start Timecode: {seconds_to_timecode(start_seconds)} (Frame {start_frame})")
        print(f"End Timecode:   {seconds_to_timecode(end_seconds)} (Frame {end_frame})")
        print(f"Frames to Save: {num_frames}")
        print(f"Output Folder:  {output_dir}")
        print("==================================================\n")

        # Create the output directory if it does not exist
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        # Seek to the starting frame index
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        extracted_count: int = 0
        video_base: str = os.path.splitext(os.path.basename(video_path))[0]

        print("Starting frame extraction...", flush=True)
        for frame_idx in range(start_frame, end_frame):
            ret: bool
            frame: cv2.Mat
            ret, frame = cap.read()
            if not ret:
                print(f"[WARNING] Stopped early: failed to read frame at index {frame_idx}", flush=True)
                break

            # Format the output filename to include the frame number and safe timecode
            # Get actual millisecond timestamp of the current frame to prevent VFR drift
            msec_current: float = cap.get(cv2.CAP_PROP_POS_MSEC)
            seconds_current: float = msec_current / 1000.0

            hours: int = int(seconds_current // 3600)
            minutes: int = int((seconds_current % 3600) // 60)
            secs: int = int(seconds_current % 60)

            # Calculate the frame index within the current second based on video frame rate
            seconds_part: float = seconds_current % 1.0
            frame_within_second: int = int(round(seconds_part * fps)) % int(round(fps))

            tc_str: str = f"{hours:02d}-{minutes:02d}-{secs:02d}_{frame_within_second:02d}"
            # Keep filenames clean and easy to correlate (e.g. frame_002310_tc_00-01-17_00.bmp)
            out_filename: str = f"frame_{frame_idx:06d}_tc_{tc_str}.bmp"
            out_path: str = os.path.join(output_dir, out_filename)

            # Write as uncompressed BMP (lossless clarity)
            cv2.imwrite(out_path, frame)
            
            extracted_count += 1
            if extracted_count % 30 == 0 or frame_idx == end_frame - 1:
                print(f"Progress: Saved {extracted_count}/{num_frames} frames...", flush=True)

        print(f"\n[SUCCESS] Extracted {extracted_count} frames to: {output_dir}", flush=True)

    finally:
        cap.release()


def main() -> None:
    """Orchestrates the user inputs and frame extraction process.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    print("==================================================")
    print("        Lossless Video Frame Extractor            ")
    print("==================================================")

    # 1. Video Path Resolution
    video_path: str = ""
    default_filenames: List[str] = [
        "hit and run WhatsApp Video 2026-06-07 at 10.52.00_prob4.mov",
        "hit and run WhatsApp Video 2026-06-07 at 10.52.00_nyx3.mov",
        "hit and run WhatsApp Video 2026-06-07 at 10.52.00.mp4"
    ]
    user_profile: str = os.environ.get("USERPROFILE", r"C:\Users\Default")
    appdata: str = os.environ.get("APPDATA", r"C:\Users\Default\AppData\Roaming")
    search_dirs: List[str] = [
        os.getcwd(),
        os.path.dirname(os.getcwd()),
        os.path.join(appdata, r"PotPlayerMini64\Capture"),
        os.path.join(user_profile, "Pictures"),
        os.path.join(user_profile, "Downloads"),
        r"C:\Downloads"
    ]

    # Search for any of the default video files in common locations
    for s_dir in search_dirs:
        for d_file in default_filenames:
            potential_path: str = os.path.join(s_dir, d_file)
            if os.path.exists(potential_path):
                video_path = potential_path
                print(f"Auto-detected video at: {video_path}")
                break
        if video_path:
            break

    # If not auto-detected, ask the user
    if not video_path:
        user_input: str = input("Enter the path to the video file: ").strip()
        # Strip surrounding quotes if the user dragged and dropped the file
        video_path = user_input.replace('"', '').replace("'", "")

    if not video_path or not os.path.exists(video_path):
        print(f"[ERROR] Specified file does not exist: '{video_path}'")
        sys.exit(1)

    # 2. Timecode Inputs (Default to 00:00:00 and dynamic video end timecode)
    start_time: str = input("Enter start timecode [default: 00:00:00]: ").strip()
    if not start_time:
        start_time = "00:00:00"

    # Dynamically determine the video duration for the default end timecode
    default_end_time: str = "00:01:50"
    try:
        cap_meta: cv2.VideoCapture = cv2.VideoCapture(video_path)
        if cap_meta.isOpened():
            meta_fps: float = float(cap_meta.get(cv2.CAP_PROP_FPS))
            meta_frames: int = int(cap_meta.get(cv2.CAP_PROP_FRAME_COUNT))
            if meta_fps > 0 and meta_frames > 0:
                duration_sec: float = meta_frames / meta_fps
                default_end_time = seconds_to_timecode(duration_sec)
            cap_meta.release()
    except Exception:
        pass

    end_time: str = input(f"Enter end timecode [default: {default_end_time}]: ").strip()
    if not end_time:
        end_time = default_end_time

    # 3. Output Folder (Default to PotPlayer capture or a subdirectory in workspace)
    appdata_out: str = os.environ.get("APPDATA", r"C:\Users\Default\AppData\Roaming")
    default_out_dir: str = os.path.join(appdata_out, r"PotPlayerMini64\Capture")
    if not os.path.exists(default_out_dir):
        # Fallback to local workspace folder
        default_out_dir = os.path.join(os.getcwd(), "extracted_frames")

    output_dir: str = input(f"Enter output folder path [default: {default_out_dir}]: ").strip()
    if not output_dir:
        output_dir = default_out_dir

    # 4. Execute Extraction
    try:
        extract_frames(video_path, start_time, end_time, output_dir)
    except Exception as e:
        print(f"\n[ERROR] Frame extraction failed: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
