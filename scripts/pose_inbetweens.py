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


def smooth_loop(poses, durations):
    """Keep each drawing at its original timestamp and add short inbetweens."""
    frames, delays = [], []
    poses = [pose.convert('RGBA') for pose in poses]
    pixels = [_premultiply(pose) for pose in poses]
    width, height = poses[0].size
    y, x = np.mgrid[:height, :width].astype(np.float32)
    for i, (pose, duration) in enumerate(zip(poses, durations, strict=True)):
        j = (i + 1) % len(poses)
        # Preserve the long resting holds. The last part of each interval
        # contains the transition, with delays representable by GIF.
        transition = min(130, duration)
        count = max(1, (transition + 49) // 50)
        ticks, remainder = divmod(transition // 10, count)
        steps = [10 * (ticks + (k < remainder)) for k in range(count)]
        frames.append(pose)
        delays.append(duration - transition + steps[0])
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
