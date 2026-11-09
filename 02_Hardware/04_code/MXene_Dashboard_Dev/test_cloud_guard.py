"""Run in its own process: audit-hook checks without making network requests."""
import sys
from security import install
from ai_providers import CLOUD_DESTINATIONS
install()
allowed=('8.8.8.8',443)
token=CLOUD_DESTINATIONS.set(frozenset({allowed}))
try:
    sys.audit('socket.connect',None,allowed)
    for address in [('1.1.1.1',443),('8.8.8.8',80),('127.0.0.1',8767),('127.0.0.1',11434)]:
        try:sys.audit('socket.connect',None,address)
        except PermissionError:pass
        else:raise AssertionError('Cloud scope escaped destination pinning')
finally:CLOUD_DESTINATIONS.reset(token)
try:sys.audit('socket.connect',None,allowed)
except PermissionError:pass
else:raise AssertionError('Cloud permission leaked after request')
print('6/6 cloud destination isolation checks passed; no connections attempted')
