"""Local signals for waiting on a private message burst, without another LLM call."""
import re


def private_quiet_seconds(text):
    text = str(text or '').strip()
    if re.search(r'(?:我说完了|说完了|发完了|就这些|可以回复了|你说吧)[。！!～~\s]*$', text):
        return 1.5
    if re.search(r'(?:还没说完|还没发完|等我说完|等我发完|等一下[，, ]?我|先等一下|我继续说)[。！!～~\s]*$', text):
        # A subsequent message replaces this deadline; shutdown can cancel the wait.
        return float('inf')
    unclosed = any(text.count(a) > text.count(b) for a, b in (('（', '）'), ('(', ')'), ('“', '”'), ('「', '」')))
    unfinished = re.search(r'(?:[，,、：:]|\.\.\.|…+|还有|然后|因为|但是|而且|比如|我想说的是|等一下)$', text)
    return 12.0 if unclosed or unfinished else 7.0
