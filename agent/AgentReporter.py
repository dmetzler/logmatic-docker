import logging

import requests
from docker.errors import DockerException

from agent.Calculator import Calculator

internal_logger = logging.getLogger()


class AgentReporter:
    def __init__(self, client, logger, namespace, attrs):
        self.client = client
        self.logger = logger
        self.ns = namespace
        self.calculator = Calculator()
        self.local_cache = {}
        self.attrs = {}
        for attr in attrs:
            kv = attr.split("=")
            if len(kv) > 1:
                self.attrs[kv[0]] = kv[1]
            else:
                self.attrs[kv[0]] = ""

        internal_logger.info("Initialize a new agent reporter")
        self.daemon_name = self.client.info()["Name"]

    def export_events(self):
        try:
            events = self.client.events(decode=True)
            for event in events:
                if event["Type"] == "container":
                    container_meta = {
                        self.ns: {
                            "id": event["id"],
                            "image": event["Actor"]["Attributes"]["image"],
                            "name": event["Actor"]["Attributes"]["name"],
                            "status": event["status"],
                        },
                        "@marker": ["docker"]
                    }
                    if len(self.attrs):
                        container_meta["attr"] = self.attrs

                    # override if tehre are more info about the container
                    if event["id"] in self.local_cache:
                        container_meta = self.local_cache[event["id"]].copy()

                    # add event data
                    container_meta[self.ns]["event"] = {
                        "type": event["Type"],
                        "action": event["Action"]
                    }
                    container_meta["@marker"].append("docker-events")

                    # send it to Logmatic.io
                    self.logger.info("[Docker event] name:{} >> event:{} (image={})"
                                     .format(container_meta[self.ns]["name"],
                                             container_meta[self.ns]["event"]["action"],
                                             container_meta[self.ns]["image"]),
                                     extra=container_meta)

        except (requests.exceptions.ConnectionError, DockerException) as error:
            internal_logger.error("Unexpected end of event stream): {}".format(error))
        except:
            internal_logger.error("Unexpected error, current thread ended.")
        return

    def export_daemon_info(self):
        try:
            info = self.client.info()
            meta = {
                "@marker": ["docker", "docker-info"],
                self.ns: {
                    "info": info,
                    "daemon_name": self.daemon_name
                }
            }
            self.logger.info(
                "[Docker daemon] Daemon: '{}' >> Containers: {}/{}/{} (run/stop/total), Cores: {}; Mem: {:.2f} GB"
                    .format(info["Name"],
                            info["ContainersRunning"],
                            info["ContainersStopped"],
                            info["Containers"],
                            info["NCPU"],
                            info["MemTotal"] / 1000000000.0),
                extra=meta)
        except (requests.exceptions.ConnectionError, DockerException) as error:
            internal_logger.error("Unexpected end of info stream): {}".format(error))
        except:
            internal_logger.error("Unexpected error, current thread ended.")
        return

    def export_stats(self, container):
        try:
            meta = self._build_context(container)
            meta["@marker"].append("docker-stats")
            stats = container.stats(stream=False, decode=True)
            meta[self.ns]["stats"] = stats
            meta[self.ns]["human_stats"] = self.calculator.compute_human_stats(container, stats)
            self.logger.info("[Docker stats] name:{} >> cpu:{:.2f}% mem:{:.2f}% io:{:.2f}MB/s (host:{} image:{})"
                             .format(meta[self.ns]["name"],
                                     meta[self.ns]["human_stats"]["cpu_stats"]["total_usage_%"] * 100.0,
                                     meta[self.ns]["human_stats"]["memory_stats"]["usage_%"] * 100.0,
                                     meta[self.ns]["human_stats"]["blkio_stats"]["total_bps"] / 1000000.0,
                                     meta[self.ns]["hostname"],
                                     meta[self.ns]["image"]), extra=meta)

        except (requests.exceptions.ConnectionError, DockerException) as error:
            internal_logger.error("Unexpected end of stats stream for container({}): {}"
                                  .format(container.short_id, error))
        except:
            internal_logger.error("Unexpected error, current thread ended.")
        return

    def export_logs(self, container):
        """Send all logs to Logmatic.io"""
        if container.attrs["Config"]["Image"].startswith("logmatic/logmatic-docker"):
            return
        try:
            line = ""
            meta = self._build_context(container)
            meta["@marker"].append("docker-logs")
            logs = container.logs(stream=True, follow=True, stdout=True, stderr=False, tail=0)
            for chunk in logs:
                # Append all char into a string until a \n
                if chunk is not '\n':
                    line = line + chunk
                else:
                    self.logger.info(line, extra=meta)
                    line = ""

        except (requests.exceptions.ConnectionError, DockerException) as error:
            internal_logger.error(
                "Unexpected end of logs stream for container({}): {}".format(container.short_id, error))
        except:
            internal_logger.error("Unexpected error, current thread ended.")
        return

    def _build_context(self, container):
        """Internal method, to build the container context"""
        labels = {
            "all": [],
            "details": {}
        }
        for label in container.attrs["Config"]["Labels"]:
            labels["all"].append(label)
            labels["details"][label] = container.attrs["Config"]["Labels"][label]

        meta = {
            self.ns: {
                "id": container.id,
                "short_id": container.short_id,
                "name": container.name,
                "status": container.status,
                "daemon_name": self.daemon_name,
                "labels": labels,
                "hostname": container.attrs["Config"]["Hostname"],
                "image": container.attrs["Config"]["Image"],
                "created": container.attrs["Created"],
                "pid": container.attrs["State"]["Pid"]
            },
            "@marker": ["docker"]
        }

        if len(self.attrs):
            meta["attr"] = self.attrs
        self.local_cache[container.id] = meta.copy()
        return meta
