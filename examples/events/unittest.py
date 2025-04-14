import signal

import time
import random
import signal

from aworld.events.event import Event
from aworld.events.pub_event import publish_event
from aworld.events.sub_event import sub_event

APPLICATION_GROUP = "APP"
SECURITY_GROUP = "SECURITY"

# Define events with different groups
class AppStartupEvent(Event):
    def __init__(self, app_name, **kwargs):
        super().__init__(
            event_code="APP_STARTUP",
            event_group=APPLICATION_GROUP,
            **kwargs
        )
        self.app_name = app_name


class AppShutdownEvent(Event):
    def __init__(self, app_name, **kwargs):
        super().__init__(
            event_code="APP_SHUTDOWN",
            event_group=APPLICATION_GROUP,
            **kwargs
        )
        self.app_name = app_name


class SecurityAlertEvent(Event):
    def __init__(self,  threat_name, **kwargs):
        super().__init__(
            event_code="SECURITY_ALERT",
            event_group=SECURITY_GROUP,
            **kwargs
        )

        self.threat_name = threat_name


# Event simulator
def event_simulator():

    # Generate a mix of events from different groups
    i = 0
    while running:
        group = i % 2  # Cycle through groups

        if group == 0:
            # Generate APPLICATION group event
            if random.choice([True, False]):
                # App startup
                publish_event(AppStartupEvent(
                    app_name="AppStartupEvent"
                ))
            else:
                # App shutdown
                publish_event(AppShutdownEvent(
                    app_name="AppShutdownEvent"
                ))
        else:
            publish_event(SecurityAlertEvent(
                threat_name="SecurityAlertEvent"
            ))

        i += 1
        # Random delay between 1-3 seconds to make it more realistic
        delay = 1
        time.sleep(delay)

#@sub_event(event_group=APPLICATION_GROUP,event_code="APP_SHUTDOWN")
@sub_event(event_group=APPLICATION_GROUP)
def app_event_monitor(event):
    """Handler for all application events"""
    current_time = time.strftime("%H:%M:%S", time.localtime())
    print(f"app_event_monitor - {current_time}-{event.event_group}-{event.event_code}")


# Subscribe to all SECURITY group events
#@sub_event(event_group=SECURITY_GROUP)
def security_event_monitor(event):
    """Handler for all security events"""
    current_time = time.strftime("%H:%M:%S", time.localtime())
    print(f"security_event_monitor - {current_time}-{event.event_group}-{event.event_code}")

# Global flag to control the service loop
running = True

def signal_handler(sig, frame):
    global running
    print("\nShutting down gracefully...")
    running = False

def main():
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=== SIMPLE EVENT_GROUP SUBSCRIPTION DEMO ===")
    print("This demo shows subscribing to events by their event_group.")
    print("We'll generate events with different groups:")
    print(f"  - {APPLICATION_GROUP}: Application events (startup/shutdown)")
    print(f"  - {SECURITY_GROUP}: Security events (alerts)")
    print("Each event group has its own handler.")
    print("\nPress Ctrl+C to stop the service...\n")

    # Run the event simulator
    try:
        event_simulator()
    except Exception as e:
        print(f"Error in event simulator: {e}")

    print("\nService stopped.")


if __name__ == "__main__":
    main()