"""Keep private passive-send credentials separate from visible quote targets."""
import hashlib
from pathlib import Path

ORIGINAL = 'cb0fbd76340023adada609aa51f5cf68c335cbd86856f4ba07aea775d387671a'
PATCHED = '07b1165b3747760edf844f97b8520d6563f1a8d4e1c896d2a72bd2619e693162'


def build(source, output):
    raw = Path(source).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest == ORIGINAL:
        old = b'            quote_target = reply_target_id or reply_to'
        new = b'            quote_target = reply_target_id or (reply_to if scene == "group" else "")'
        if raw.count(old) != 1:
            raise RuntimeError('QQ quote anchor changed')
        raw = raw.replace(old, new)
    elif digest != PATCHED:
        raise RuntimeError('Uninspected QQ adapter source')
    if hashlib.sha256(raw).hexdigest() != PATCHED:
        raise RuntimeError('Unexpected QQ adapter patch')
    compile(raw, str(output), 'exec')
    Path(output).write_bytes(raw)
    return PATCHED
