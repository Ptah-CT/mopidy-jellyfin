from __future__ import unicode_literals

import logging
import time


logger = logging.getLogger(__name__)


class cache(object):
    # stolen from mopidy-soundcloud <3

    def __init__(self, ctl=8, ttl=3600):
        self.cache = {}
        self.ctl = ctl
        self.ttl = ttl
        self._call_count = 1

    def __call__(self, func):
        def _memoized(*args):
            self.func = func
            now = time.time()
            try:
                value, last_update = self.cache[args]
                age = now - last_update
                if self._call_count >= self.ctl or age > self.ttl:
                    self._call_count = 1
                    raise AttributeError

                self._call_count += 1
                return value

            except (KeyError, AttributeError):
                value = self.func(*args)
                self.cache[args] = (value, now)
                return value

            except TypeError:
                return self.func(*args)

        return _memoized

def create_headers(device, device_id, version, token=None):
    """Return header dict that is needed to talk to the Jellyfin API.
    """
    headers = {}

    authorization = (
        f'MediaBrowser , '
        f'Client="Mopidy", '
        f'Device="{device}", '
        f'DeviceId="{device_id}", '
        f'Version="{version}"'
    )

    headers['Authorization'] = authorization

    if token:
        headers['Authorization'] += f', Token="{token}"'

    return headers
