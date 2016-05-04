#!/usr/bin/env python

import argparse
import logging
import structlog
import sys
import yaml
from email.header import decode_header, make_header
from imapclient import IMAPClient
from rtm import createRTM
from structlog import get_logger

TASKLIST_FILTER = "tag:imap2rtm"


def main(args):
    init_logger(level=args.log_level.upper())
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)

    log = get_logger()
    rtm = createRTM(config['rtm']['api_key'], config['rtm']['shared_secret'], config['rtm']['token'])
    imap = new_imap_connection(
        log=log,
        host=config['imap']['host'],
        port=config['imap'].get('port', 143),
        legacy_ssl=config['imap'].get('legacy_ssl', False),
        username=config['imap']['username'],
        password=config['imap']['password'],
    )

    messages = get_messages(log, imap, config['imap']['folder'])
    tasks = get_tasks(log, rtm, list=config['rtm']['list'])
    message_set = set(messages.keys())
    task_set = set(tasks.keys())

    not_in_rtm = message_set.difference(task_set)
    not_in_imap = task_set.difference(message_set)

    timeline = rtm.timelines.create().timeline
    tags = ["imap2rtm"] + config["rtm"].get('extra_tags', [])

    for title in not_in_rtm:
        log.info("Adding task", title=title)
        task = rtm.tasks.add(
            timeline=timeline,
            list_id=config['rtm']['list'],
            name=title,
            parse=0
        )
        log.debug("Applying tags", task=title, tags=tags)
        rtm.tasks.setTags(
            timeline=timeline,
            list_id=config['rtm']['list'],
            taskseries_id=task.list.taskseries.id,
            task_id=task.list.taskseries.task.id,
            tags=tags
        )

    for title in not_in_imap:
        log.info("Marking task completed", task=title)
        task = tasks[title]
        rtm.tasks.complete(
            timeline=timeline,
            list_id=config['rtm']['list'],
            taskseries_id=task.id,
            task_id=task.task.id
        )


def init_logger(level):
    logging.basicConfig()
    formatter = logging.Formatter('%(message)s')
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.removeHandler(root_logger.handlers[0])
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.KeyValueRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def new_imap_connection(log, host, username, password, port=143, legacy_ssl=False):
    log.debug("Establishing IMAP connection", host=host, port=port, ssl=legacy_ssl, username=username)
    c = IMAPClient(host, port=port, use_uid=True, ssl=legacy_ssl)
    if not legacy_ssl:
        log.debug("Upgrading to TLS")
        c.starttls()
    log.debug("Logging in")
    c.login(username, password)
    log.debug("Successfully connected to IMAP server")
    return c


def get_messages(log, connection, folder):
    response = connection.select_folder(folder)
    log.debug("Switched folders", folder=folder, response=response)
    log.debug("Searching for messages matching NOT DELETED")
    uids = connection.search(['NOT', 'DELETED'])
    messages = connection.fetch(uids, ["ENVELOPE"])
    log.debug("Fetched messages", count=len(messages))
    result = {}
    for uid, message in messages.items():
        try:
            subject = str(make_header(decode_header(message[b'ENVELOPE'].subject.decode('utf-8')))).strip()
        except AttributeError:
            log.warning("Skipped message because of missing subject header", message=message)
            continue
        result[subject] = message
    return result


def get_tasks(log, rtm, list):
    log.debug("Getting RTM tasks")
    tasks = {}
    tasklist = rtm.tasks.getList(filter=TASKLIST_FILTER)

    if not (hasattr(tasklist.tasks, "list") and hasattr(tasklist.tasks.list, "__getitem__")):
        log.debug("RTM tasklist empty")
        return

    for taskseries in tasklist.tasks.list:
        # Workaround for taskseries.taskseries not being iterable when only 1 task was returned
        if not hasattr(taskseries.taskseries, "__iter__"):
            taskseries.taskseries = [taskseries.taskseries]
        for task in taskseries.taskseries:
            if task.task.completed:
                continue
            tasks[task.name] = task

    log.debug("Fetched RTM tasks", count=len(tasks))
    return tasks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--log-level", help="specify log level", default="info")
    parser.add_argument("-c", "--config", help="specify configuration file", default="config.yml")
    args = parser.parse_args()
    main(args)
