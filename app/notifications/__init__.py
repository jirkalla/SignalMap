"""Registry of active notification channels — add a new channel by adding a module here plus one
entry below, never by editing app/services/notifications.py's `notify()` (design decision 25).
"""

from app.notifications.base import NotificationChannel
from app.notifications.inapp import InAppChannel

CHANNELS: list[NotificationChannel] = [InAppChannel()]
