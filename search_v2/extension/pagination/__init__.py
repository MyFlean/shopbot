"""
Offset/page-size handling for extension endpoints, if it needs to differ
from the pattern search_v2/extension/search/core.py already uses
(pool-then-slice — see the earlier latency investigation for why the
gateway retrieves a candidate pool before paginating).
"""
