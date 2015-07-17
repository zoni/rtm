#!/usr/bin/env python

import argparse
import github
import logging
import os
import re
import structlog
import sys
from rtm import createRTM
from structlog import get_logger


GITHUB_ISSUE_QUERIES = [
    {'filter': "assigned", 'state': "open"},
    {'filter': "created", 'state': "open"},
    {'filter': "subscribed", 'state': "open"},
]
WORK_USERS = ['ByteInternet']
WORK_REPOS = []
TASKLIST_FILTER = "tag:github"
RTM_TAGS = ["github"]
RTM_LIST_ID = 39454401  # GitHub


def main(args):
    init_logger(level=args.log_level.upper())

    GITHUB_ACCESS_TOKEN = os.environ.get('GITHUB_ACCESS_TOKEN', None)
    if GITHUB_ACCESS_TOKEN is None:
        print("Missing GITHUB_ACCESS_TOKEN!", file=sys.stderr)
        sys.exit(1)

    RTM_API_KEY = os.environ.get('RTM_API_KEY', None)
    RTM_SHARED_SECRET = os.environ.get('RTM_SHARED_SECRET', None)
    RTM_TOKEN = os.environ.get('RTM_TOKEN', None)

    if None in (RTM_API_KEY, RTM_SHARED_SECRET, RTM_TOKEN):
        print("Missing RTM_API_KEY, RTM_SHARED_SECRET or RTM_TOKEN!", file=sys.stderr)
        sys.exit(1)

    log = get_logger()
    log = log.bind(component="main")
    log.info("gh.init")

    gh = github.GitHub(access_token=GITHUB_ACCESS_TOKEN)
    log.info("gh.get_issues")
    issues = get_github_issues(gh, GITHUB_ISSUE_QUERIES)

    log.info("rtm.init")
    rtm = createRTM(RTM_API_KEY, RTM_SHARED_SECRET, RTM_TOKEN)

    log.info("complete_missing_issues.start")
    complete_missing_issues(rtm, issues)
    log.info("complete_missing_issues.finish")
    log.info("add_new_issues.start")
    add_new_issues(rtm, issues)
    log.info("add_new_issues.finish")


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
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_github_issues(gh, queries):
    """
    Return a dict of issues found matching specified filters.


    :param gh: github.GitHub client
    :param queries: A list containing individual search queries to perform
    :return: A dictionary whose elements are in the form of
        {'repo#000: Summary': {..data from github}}
    """
    log = get_logger()
    log = log.bind(component='get_github_issues')

    issues = {}
    results = []
    for query in queries:
        log.debug("gh.issues.get", q=query)
        items = gh.issues.get(**query)
        log.debug("gh.issues.result", q=query, result_count=len(items))
        results += items

    for issue in results:
        title = "{repository[name]}#{number}: {title}".format(**issue)
        log.debug("gh.parse_issue", title=title)
        if title not in issues:
            issues[title] = issue
    return issues


def complete_missing_issues(rtm, issues):
    """
    Mark tasks as completed if they cannot be found in issues.
    """
    log = get_logger()
    log = log.bind(component='complete_missing_issues')

    log.info("timeline.create")
    timeline = rtm.timelines.create().timeline
    log.info("tasklist.get")
    tasklist = rtm.tasks.getList(filter=TASKLIST_FILTER)

    if not (hasattr(tasklist.tasks, "list") and hasattr(tasklist.tasks.list, "__getitem__")):
        log.debug("tasklist.empty")
        return

    for taskseries in tasklist.tasks.list:
        # Workaround for taskseries.taskseries not being iterable when only 1 task was returned
        if not hasattr(taskseries.taskseries, "__iter__"):
            taskseries.taskseries = [taskseries.taskseries]
        for task in taskseries.taskseries:
            log = log.bind(name=task.name)
            if task.task.completed:
                log.debug("tasklist.task.skip", reason="already_completed")
                continue

            if task.name not in issues:
                log.info("tasklist.task.mark_complete")
                rtm.tasks.complete(
                        timeline=timeline,
                        list_id=RTM_LIST_ID,
                        taskseries_id=task.id,
                        task_id=task.task.id
                )
            else:
                log.debug("tasklist.task.skip", reason="still_active")


def add_new_issues(rtm, issues):
    """
    Add new tasks for GitHub issues that aren't yet in RTM.
    """
    log = get_logger()
    log = log.bind(component='add_new_issues')

    log.info("timeline.create")
    timeline = rtm.timelines.create().timeline
    log.info("tasklist.get")
    tasklist = rtm.tasks.getList(filter=TASKLIST_FILTER)

    tasks = []
    for taskseries in tasklist.tasks.list:
        # Workaround for taskseries.taskseries not being iterable when only 1 task was returned
        if not hasattr(taskseries.taskseries, "__iter__"):
            taskseries.taskseries = [taskseries.taskseries]
        for task in taskseries.taskseries:
            if task.task.completed:
                log.debug("tasklist.task.marknotseen", name=task.name, reason="task.task.completed")
                continue
            log.debug("tasklist.task.markseen", name=task.name)
            tasks.append(task.name)

    for issue, details in issues.items():
        log = log.bind(issue=issue)
        if issue in tasks:
            log.debug("tasklist.task.skip", reason="already_present")
        else:
            log.info("tasklist.task.add")
            task = rtm.tasks.add(
                timeline=timeline,
                list_id=RTM_LIST_ID,
                name=issue,
                parse=0
            )

            log.info("tasklist.task.setTags")
            tags = RTM_TAGS[:]
            user = details['repository']['owner']['login']
            repo = details['repository']['name']

            tags += [user, repo]
            if user in WORK_USERS or repo in WORK_REPOS:
                tags.append(".work")

            rtm.tasks.setTags(
                timeline=timeline,
                list_id=RTM_LIST_ID,
                taskseries_id=task.list.taskseries.id,
                task_id=task.list.taskseries.task.id,
                tags=tags
            )

            log.info("tasklist.task.setURL")
            rtm.tasks.setURL(
                timeline=timeline,
                list_id=RTM_LIST_ID,
                taskseries_id=task.list.taskseries.id,
                task_id=task.list.taskseries.task.id,
                url=details['html_url']
            )
            log.debug("tasklist.task.added")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--log-level", help="select log-level", default="info")
    args = parser.parse_args()
    main(args)
