#!/usr/bin/python
import argparse
import logging
import sys
import threading
from time import sleep

import docker
import logmatic

from agent.AgentReporter import AgentReporter

internal_logger = logging.getLogger()
internal_logger.addHandler(logging.NullHandler())

# Args parser settings
parser = argparse.ArgumentParser(description='Send logs, events and stats to Logmatic.io')
parser.add_argument("token", metavar="LOGMATIC_API_KEY", help='The Logmatic.io API key')
parser.add_argument("--logs", dest='logs', action="store_true", help="Enable the logs streams")
parser.add_argument('--no-logs', dest='logs', action="store_false", help="Disable the logs streams")
parser.add_argument("--stats", dest='stats', action="store_true", help="Enable the stats streams")
parser.add_argument('--no-stats', dest='stats', action="store_false", help="Disable the stats streams")
parser.add_argument("--daemon-info", dest='daemon_info', action="store_true", help="Enable the info streams")
parser.add_argument('--no-daemon-info', dest='daemon_info', action="store_false", help="Disable the info streams")
parser.add_argument("--events", dest='events', action="store_true", help="Enable the event stream")
parser.add_argument('--no-events', dest='events', action="store_false", help="Disable the event stream")
parser.add_argument("--namespace", dest='ns', help="Default namespace")
parser.add_argument("--hostname", dest='hostname', help="Logmatic.io's hostname (default api.logmatic.io)")
parser.add_argument("--port", dest='port', type=int, help="Logmatic.io's port (default 10514)")
parser.add_argument("-i", dest='interval', type=int, help="Seconds between to stats report (default 30)")
parser.add_argument("-a", "--attr", action='append')

# Default values
parser.set_defaults(logs=True)
parser.set_defaults(stats=True)
parser.set_defaults(events=True)
parser.set_defaults(daemon_info=True)
parser.set_defaults(ns="docker")
parser.set_defaults(hostname="api.logmatic.io")
parser.set_defaults(port=10514)
parser.set_defaults(interval=30)
parser.set_defaults(attr=[])

args = parser.parse_args()
internal_logger.debug(args)

# Initialise the logger for Logmatic.io
logmatic_logger = logging.getLogger("docker-logmatic")
handler = logmatic.LogmaticHandler(args.token, host=args.hostname, port=args.port)
handler.setFormatter(logmatic.JsonFormatter(fmt="%(message)"))
logmatic_logger.addHandler(handler)
logmatic_logger.setLevel(logging.DEBUG)

# Initialise the connection to the local daemon
base_url = 'unix://var/run/docker.sock'
client = docker.DockerClient(base_url=base_url, timeout=None)

# Main logic starts here
agent = AgentReporter(client=client, logger=logmatic_logger, namespace=args.ns, attrs=args.attr)

# Initialize all threads
event_thread = None
log_threads = {}
logs = False

# Main loop
while 1:


    try:
        containers = None
        # Start the event thread, and check if it's alive each seconds
        if args.events is True and (event_thread is None or not event_thread.isAlive()):
            internal_logger.info("Starting the event stream thread")
            event_thread = threading.Thread(target=agent.export_events)
            event_thread.daemon = True
            event_thread.start()

        # Start all log threads, and check if they're alive each x seconds
        if args.logs is True or args.stats is True:
            containers = client.containers.list()
            for container in containers:
                # Start threads and check if each logging thread are alive
                if args.logs is True and (container.id not in log_threads or not log_threads[container.id].isAlive()):
                    internal_logger.info("Starting the log stream thread for " + container.id)
                    log_threads[container.id] = threading.Thread(target=agent.export_logs, args=[container])
                    log_threads[container.id].daemon = True
                    log_threads[container.id].start()

                # Export stats to Logmatic.io
                if args.stats is True:
                    agent.export_stats(container)

            # Export info to Logmatic.io
            if args.daemon_info is True:
                agent.export_daemon_info()

        sleep(args.interval)
        internal_logger.debug("Next tick in {}s".format(args.interval))


    except (KeyboardInterrupt, SystemExit):
        exit(0)
