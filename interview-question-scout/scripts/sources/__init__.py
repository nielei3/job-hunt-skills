"""Sources registry. Add new sources by importing them here."""
from .base import Post, Source
from .one_p_three_a import OnePointThreeAcresSource
from .leetcode import LeetCodeDiscussSource
from .reddit import RedditSource
from .glassdoor import GlassdoorSource

SOURCES: dict[str, Source] = {
    s.slug: s()
    for s in [
        OnePointThreeAcresSource,
        LeetCodeDiscussSource,
        RedditSource,
        GlassdoorSource,
    ]
}

__all__ = ["Post", "Source", "SOURCES"]
