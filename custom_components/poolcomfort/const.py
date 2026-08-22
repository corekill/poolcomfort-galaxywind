from datetime import timedelta

DOMAIN = "poolcomfort"

DEFAULT_PASSWORD = "123456"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
DEFAULT_TIMEOUT = 3.0

# Readings older than this mean we are no longer talking to the pump.  Entity
# values are deliberately held at their last reading during an outage, so this
# is what the connection sensors report on instead.  Six missed polls, long
# enough that a single dropped packet does not flip the sensor.
STALE_AFTER = timedelta(minutes=3)

MIN_TEMP = 15
MAX_TEMP = 40
