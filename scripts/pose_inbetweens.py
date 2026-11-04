"""Interpolate existing Liya drawings at export time."""
import cv2
import numpy as np
from PIL import Image

from build_animations import motion_pair


def _warp(frame, flow, amount, x, y):
    map_x, map_y = x, y
    for _ in range(4):
        displacement = cv2.remap(flow, map_x, map_y, cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
        map_x = x - amount * displacement[:, :, 0]
        map_y = y - amount * displacement[:, :, 1]
    return np.clip(cv2.remap(frame, map_x, map_y, cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT), 0, 1)


def _premultiply(image):
    rgba = np.asarray(image.convert('RGBA'), dtype=np.float32) / 255
    rgba[:, :, :3] *= rgba[:, :, 3:]
    return rgba


def _intermediate(before, after, forward, backward, t, x, y):
    left = _warp(before, forward, t, x, y)
    right = _warp(after, backward, 1 - t, x, y)
    # Use one source in areas of occlusion or unmatched expression details.
    # Blend only where both warped drawings agree.
    agreement = np.clip((0.13 - np.max(abs(left - right), axis=2, keepdims=True)) / 0.09, 0, 1)
    weight = agreement * t + (1 - agreement) * float(t > 0.5)
    result = left * (1 - weight) + right * weight
    result[:, :, :3] /= np.maximum(result[:, :, 3:], 1 / 255)
    return Image.fromarray(np.uint8(np.clip(result, 0, 1) * 255 + 0.5))


def loop_timing(durations, stretch=1):
    """Stretch the holds and transitions while preserving the average cadence."""
    transitions = [min(130, duration) for duration in durations]
    counts = [(transition + 49) // 50 for transition in transitions]
    total = sum(counts)
    scale = int(total * stretch + 0.5) / total
    elapsed = frame = 0
    for duration, transition, count in zip(durations, transitions, counts, strict=True):
        start_tick = round(elapsed * scale / 10)
        elapsed += duration
        end_tick = round(elapsed * scale / 10)
        hold_ticks = round((elapsed - transition) * scale / 10) - start_tick
        samples = round((frame + count) * scale) - round(frame * scale)
        frame += count
        yield end_tick - start_tick, hold_ticks, samples


def smooth_loop(poses, timing):
    """Keep the drawn poses and interpolate through each timed transition."""
    frames, delays = [], []
    poses = [pose.convert('RGBA') for pose in poses]
    pixels = [_premultiply(pose) for pose in poses]
    width, height = poses[0].size
    y, x = np.mgrid[:height, :width].astype(np.float32)
    for i, (pose, (duration, hold, count)) in enumerate(zip(poses, timing, strict=True)):
        j = (i + 1) % len(poses)
        # Preserve the long resting holds. The last part of each interval
        # contains the transition, with delays representable by GIF.
        transition = 10 * (duration - hold)
        ticks, remainder = divmod(duration - hold, count)
        steps = [10 * (ticks + (k < remainder)) for k in range(count)]
        frames.append(pose)
        delays.append(10 * hold + steps[0])
        if count == 1:
            continue
        forward, backward = motion_pair(pose, poses[j])
        elapsed = steps[0]
        for delay in steps[1:]:
            frames.append(_intermediate(pixels[i], pixels[j], forward, backward,
                                        elapsed / transition, x, y))
            delays.append(delay)
            elapsed += delay
    return frames, delays
