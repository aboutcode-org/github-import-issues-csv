#
# SPDX-License-Indentifier: MIT
#
# Copyright (c) nexB Inc. and others
# Copyright (c) 2024 goldhaxx
#
# Originally based on goldhaxx MIT-licensed code and heavily modified
# The rate limit processing is reused mostly as-is.
#
# See https://github.com/goldhaxx/github-projects-task-uploader/blob/a3a649e740d0fa45e4d16f5b3dfa405ffb655673/csv-to-github-project.py
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
This script read a CSV and creates GitHub issues, and add these to GitHub projects.
You need first to install these dependencies in your virtualenv::

    pip install click requests

Then run this way::

    python src/import_issues.py --help

You need to have:
- pre-existing repositories and projects created in GitHub, with optional fields if needed,
- a proper token exported in a GITHUB_TOKEN environment variable with repo and project scope

The CSV has these columns:

Core fields :
- account_type: required for projects, GitHub account type: either a "user" or an "organization"

- account_name: required, GitHub account name that owns the repo_name where to create the issues
  and who owns the optional "project_number" to append issues to.

- repo_name: required, GitHub repo name where to create the issues

- title: required, GitHub issue title
- body: required, GitHub issue body.


Optional Project support:

- project_number: required for projects, GitHub project number in the "account_name".


Optional Project fields support:

- project_estimate: an estimate to complete as a number of days for this issue.
  This is used to populate an "Estimate" custom project field that needs to be created first as a
  "number" field in the project.

Optional status:

- status: A status value. Must be exactly one of the selectable value in the Status project field.


Optional Issues and Subissues support:

- project_id: Field must exist with ProjectID
- project_issue_id: Field must exist with IssueID
- project_parent_issue_id: used to create subissues. Not imported.


An issue with a project_issue_id and a project_parent_issue_id will be added as a subissue of the
issue with an id of project_parent_issue_id
"""

import csv
import dataclasses
import os
import time

from collections import defaultdict
from datetime import datetime
from typing import Dict
from typing import List

import click
import requests

from requests.exceptions import RequestException

# this needs a token with scope project
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

auth_headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "AbouCode.org-issuer"
}

# Rate limiter settingsissue_id
# Maximum number of requests allowed within the time frame
RATE_LIMIT_MAX_REQUESTS = 90
# Time frame for rate limiting in seconds
RATE_LIMIT_TIME_FRAME = 60

DEBUG = False
VERBOSE = False


class RateLimiter:

    def __init__(self, max_requests, time_frame):
        self.max_requests = max_requests
        self.time_frame = time_frame
        self.requests = []

    def wait(self):
        # wait always a little to avoid hitting secondary rate limits
        now = time.time()
        if len(self.requests) >= self.max_requests:
            wait_time = self.time_frame - (now - self.requests[0])
            if wait_time > 0:
                click.echo(f"\n==> Own rate limiter: waiting: {wait_time:.2f} seconds")
                time.sleep(wait_time)
            self.requests = [r for r in self.requests if now - r <= self.time_frame]
        self.requests.append(now)


rate_limiter = RateLimiter(
    max_requests=RATE_LIMIT_MAX_REQUESTS,
    time_frame=RATE_LIMIT_TIME_FRAME,
)


def handle_rate_limit(response):
    """
    Wait according to the rate limit headers in ``response``.
    Return True if the rate limit was exceeded and the request was throttled, False otherwise.
    Riase Exceptions on errors.
    """
    if response.status_code in (403, 429):
        reset_time = int(response.headers.get('x-ratelimit-reset', 0))
        current_time = int(time.time())
        sleep_time = min(reset_time - current_time, 120)
        click.echo(f"\n==> Rate limit exceeded. Waiting for {sleep_time} seconds before retrying")
        check_rate_limit_status(response)
        time.sleep(sleep_time)
        return True

    elif 400 <= response.status_code < 600:
        click.echo(f"Error: {response.status_code} - {response.text}")
        raise RequestException(f"HTTP error {response.status_code}")

    return False


def check_rate_limit_status(response):
    """
    Print verbose rate-limiting status details after each API call.
    """
    limit = response.headers.get('x-ratelimit-limit')
    remaining = response.headers.get('x-ratelimit-remaining')
    used = response.headers.get('x-ratelimit-used')
    reset = response.headers.get('x-ratelimit-reset')
    resource = response.headers.get('x-ratelimit-resource')

    if all([limit, remaining, used, reset, resource]):
        reset_time = datetime.fromtimestamp(int(reset)).strftime('%Y-%m-%d %H:%M:%S')
        if not VERBOSE:
            if not int(used) % 10:
                click.echo(
                    f"Rate Limit Status: used: {used} "
                    f"remaining: {remaining}/{limit} "
                    f"Reset Time: {reset_time} Resource: {resource}"
                )
        else:
            click.echo(
                f"Rate Limit Status: used: {used} "
                f"remaining: {remaining}/{limit} "
                f"Reset Time: {reset_time} Resource: {resource}"
            )

    else:
        click.echo("Rate limit information not available in the response headers.")


@dataclasses.dataclass(kw_only=True)
class Item:
    """An issue or PR"""

    # Do not set: the issue number, automatically set upon creation
    number: int = 0
    # Do not set: used in graphql, automatically set upon creation
    # this is for the Issue or PR node
    node_id: str = ""

    # one of user or organization
    account_type: str = "organization"
    account_name: str = ""
    repo_name: str = ""

    # Optional field, if we add the issue to a GitHub project
    project_number: int = 0

    # Do not set: used in graphql, automatically set upon creation
    # this is for the Item node
    item_node_id: str = ""

    title: str = ""

    # Optional:
    status: str = ""

    # Optional:
    iteration: str = ""

    # Optional:
    project_estimate: int = 0

    # Optional: an arbitrary string used to identify this project ( NOT the same as the GH project
    # id e.g., its number)
    project_id: str = ""

    # Optional: an arbitrary string used to identify this issue is this project( NOT the same as the
    # issue id e.g., its number)
    project_issue_id: str = ""

    # Optional: an arbitrary string used to identify a parent "project_issue_id" for this issue.
    # This issue will be added to the parent as subissue id.
    project_parent_issue_id: str = ""

    # Optional:
    full_url: str = ""

    def __post_init__(self):
        assert self.account_type in ("user", "organization") , f"Invalid account type: {self!r}"
        assert self.account_name, f"Missing account name: {self!r}"
        assert self.repo_name, f"Missing repo name: {self!r}"

        if any([self.project_estimate, self.project_issue_id, self.project_id, self.project_parent_issue_id]):
            assert self.project_number

    @classmethod
    def from_data(cls, account_type, account_name, project_number, data):
        """
        Create and return an object from an item ``data`` mapping as retrieved from
        Project.get_items().

        Sample data:
         {
            "id": "PVTI_lADOBamaz84Au48hzgWElNY",
            "project_id": {
              "text": "fast-scan"
            },
            "issue_id": {
              "text": "fast-scan-t3-b"
            },
            "estimate": {
              "number": 1.0
            },
            "status": {
              "name": "In progress"
            },
            "content": {
              "id": "I_kwDOAkmH2s6lEfBX",
              "number": 4069,
              "title": "fast-scan: Release ScanCode.io",
              "url": "https://github.com/aboutcode-org/scancode-toolkit/issues/4069",
              "updatedAt": "2025-01-05T19:28:51Z",
              "assignees": {
                "nodes": []
              },
              "labels": {
                "nodes": []
              }
            }
          }
        """
        content = data["content"]

        number = content["number"]
        node_id = content["id"]

        item_node_id = data["id"]

        project_id = (data.get("project_id") or {}).get("text") or ""
        issue_id = (data.get("issue_id") or {}).get("text") or ""
        estimate = (data.get("estimate") or {}).get("number") or 0
        estimate = int(estimate)
        status = (data.get("status") or {}).get("name") or ""
        iteration = (data.get("iteration") or {}).get("title") or ""

        title = content.get("title") or ""
        url = content["url"]
        # >>> "https://github.com/aboutcode-org/scancode-toolkit/issues/4059".split("/")
        # ['https:', '', 'github.com', 'aboutcode-org', 'scancode-toolkit', 'issues', '4059']
        repo_name = url.split("/")[-3]
        return cls(
            # standard fields
            number=number,
            node_id=node_id,
            title=title,
            account_type=account_type,
            account_name=account_name,
            repo_name=repo_name,

            project_number=project_number,
            item_node_id=item_node_id,

            # custom fields
            status=status,
            iteration=iteration,
            project_estimate=estimate,
            project_id=project_id,
            project_issue_id=issue_id,
            full_url=url
        )

    @property
    def url(self):
        return self.full_url


@dataclasses.dataclass(kw_only=True)
class Issue(Item):
    """
    A GitHub issue with is title and body.
    """
    # Required
    body: str = ""

    # list of label strings
    labels: list["str"] = dataclasses.field(default_factory=list)

    # Do not set: used for sub issues, automatically populated. The value is a project_issue_id
    project_subissue_ids: List[str] = dataclasses.field(default_factory=list)

    def __post_init__(self):
        assert self.title, f"Missing title: {self!r}"
        assert self.body, f"Missing body: {self!r}"

    @property
    def url(self):
        return f"https://github.com/{self.account_name}/{self.repo_name}/issues/{self.number}"

    def get_body(self):
        """Return the body. Subclasses can override"""
        return self.body

    def create(self, headers=auth_headers, retries=0):
        """
        Create issue at GitHub and update thyself.
        NB: this does not check if the same issue already exists.
        """
        rate_limiter.wait()
        api_url = f"https://api.github.com/repos/{self.account_name}/{self.repo_name}/issues"
        request_data = {"title": self.title, "body": self.get_body()}
        labels = self.labels or []

        if labels:
            request_data["labels"] = labels

        response = requests.post(url=api_url, headers=headers, json=request_data)

        try:
            throttled = handle_rate_limit(response)
            if throttled and retries < 2:
                retries += 1
                click.echo(f"Request failed: {response} retrying: {retries}")
                self.create(
                    headers=headers,
                    retries=retries,
                )
                return
        except Exception as e:
            raise Exception(
                f"Failed to create issue: {self!r}\n"
                f"  with api_url: {api_url}\n"
                f"  with request: {request_data}\n"
                f"  and response: {response}"
            ) from e

        check_rate_limit_status(response)

        results = response.json()
        self.number = results["number"]
        # this is needed for further GraphQL queries and mutations
        self.node_id = results["node_id"]

    def add_subissue(self, subissue, headers=auth_headers, retries=0):
        """
        Add Issue ``subissue`` as a subissue of this Issue.
        Both issues must have been created first.
        NB: this does not check if the same issue already exists.
        """
        self.fail_if_not_created()
        subissue.fail_if_not_created()

        variables = {"issue_node_id": self.node_id, "subissue_node_id": subissue.node_id}

        query = """mutation($issue_node_id:ID!, $subissue_node_id:ID!) {
            addSubIssue(input: {issueId: $issue_node_id, subIssueId: $subissue_node_id}) {
                clientMutationId
            }
        }
        """
        graphql_query(query=query, variables=variables)

    def fail_if_not_created(self):
        assert self.number, f"Issue: {self.title} must be created first at GitHub"

    def add_to_project(self):
        """
        Add this issue to its project, if this issue has a "project_number".
        Update project fields: estimate, issue_id and project_id
        """
        self.fail_if_not_created()
        project = self.get_project()
        if not project:
            return

        project.add_issue(issue=self)

        project.set_fields(
            item_node_id=self.item_node_id,
            project_estimate=self.project_estimate or 0,
            project_issue_id=self.project_issue_id,
            project_id=self.project_id,
            status=self.status or "",
            iteration=self.iteration or "",
        )

    def get_project(self):
        """
        Return a Project for this issue or None.
        """
        if self.project_number:
            return Project.get_or_create_project(
                number=self.project_number,
                account_type=self.account_type,
                account_name=self.account_name,
            )

    def create_issue_and_add_to_project(self):
        """
        Create this Issue at GitHub and add to project.
        """
        self.create()
        click.echo(f"Created Issue: URL: {self.url} - {self.title} ")

        project = self.get_project()
        if project:
            self.add_to_project()
            click.echo(f"Added Issue: URL: {self.url} to Project: {project.url}")
        else:
            click.echo("")

    @classmethod
    def from_data(cls, data):
        """
        Create and return an Issue from a ``data`` mapping.
        """
        labels = data.get("labels", "").strip()
        if labels:
            labels = [l.strip() for l in labels.split(",") if l.strip()]
        else:
            labels = []

        return cls(
            title=data["title"].strip(),
            body=data["body"].strip(),
            account_type=data["account_type"].strip(),
            account_name=data["account_name"].strip(),
            repo_name=data["repo_name"].strip(),

            labels=labels,

            # force int
            project_number=int(data.get("project_number", "").strip() or 0),
            # force int
            project_estimate=int(data.get("project_estimate", "").strip() or 0),

            project_id=data.get("project_id", "").strip() or "",
            project_issue_id=data.get("project_issue_id", "").strip() or "",
            project_parent_issue_id=data.get("project_parent_issue_id", "").strip() or "",
            status=data.get("status", "").strip() or "",
            iteration=data.get("iteration", "").strip() or "",
        )


def graphql_query(query, variables=None, headers=auth_headers, retries=0):
    """
    Post GraphQL ``query`` with ``variables``  to GitHub API query using ``headers`` and return
    results. Raise Exceptions on errors. Retry up to ``retries`` time.
    """
    rate_limiter.wait()

    api_url = "https://api.github.com/graphql"
    request_data = {"query": query}
    if variables:
        request_data["variables"] = variables

    if DEBUG:
        click.echo()
        click.echo(f"GraphQL variables: {variables}")
        click.echo(f"GraphQL query: {query}")
        click.echo()

    response = requests.post(url=api_url, headers=headers, json=request_data)

    try:
        throttled = handle_rate_limit(response)
        if throttled and retries < 2:
            retries += 1
            click.echo(f"Request failed: {response} retrying: {retries}")
            graphql_query(query=query, variables=variables, headers=headers, retries=retries)
    except Exception as e:
        raise Exception(
            f"Failed to post GraphQL query with request: {request_data}\n\n"
            f"and response: {response}"
        ) from e

    check_rate_limit_status(response)

    if response.status_code == 200:
        results = response.json()
        if 'errors' in results:
            raise Exception(
                f"GraphQL query error: {results['errors']}\n\n"
                f"query: {query}\n"
                f"variables: {variables}"
            )
        return results
    else:
        raise Exception(
            f"Query failed with status code {response.status_code}. Response: {response.text}"
        )


@dataclasses.dataclass(kw_only=True)
class Project:
    """
    A GitHub project, identified by its project number in a GitHub account.
    """
    # a cache of all projects, keyed by number
    projects_by_number = {}

    number: int = 0
    project_node_id: str = ""

    # one of user or organization
    account_type: str = "organization"
    account_name: str = ""

    # {name -> field_node_id} mapping for the project "plain" fields (text, date and numbers).
    field_ids_by_field_name: Dict[str, str] = dataclasses.field(default_factory=dict)

    # {name -> {option name: option id} mapping for the project singleselect fields
    field_select_option_ids_by_field_and_option_name: Dict[str, Dict[str, str]] = dataclasses.field(default_factory=dict)

    # {name -> {iteration title: iteration id} mapping for the project iteration fields
    field_iteration_ids_by_field_and_iteration_title: Dict[str, Dict[str, str]] = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        assert self.number
        assert self.account_type in ("user", "organization",)
        assert self.account_name

    @property
    def url(self):
        if self.account_type == "user":
            org_type_for_url = "users"
        elif self.account_type == "organization":
            org_type_for_url = "orgs"

        return f"https://github.com/{org_type_for_url}/{self.account_name}/projects/{self.number}"

    @classmethod
    def get_or_create_project(cls, number, account_type, account_name):
        """
        Return a new or an existing, cached Project object.
        (Does NOT create anything at GitHub, the project must always exist remotely at first)
        """
        if existing := cls.projects_by_number.get(number):
            return existing

        project = Project(number=number, account_type=account_type, account_name=account_name)
        cls.projects_by_number[number] = project
        return project

    def create_item(self, content_id):
        """
        Create item with ``content_id`` in this project at GitHub. Return the created item id.
        """
        query = """
        mutation($project_node_id: ID!, $content_id: ID!) {
            addProjectV2ItemById(input: {projectId: $project_node_id, contentId: $content_id}) {
                item {
                    id
                }
            }
        }
        """
        variables = {"project_node_id": self.get_project_node_id(), "content_id": content_id}
        results = graphql_query(query=query, variables=variables)
        return results["data"]["addProjectV2ItemById"]["item"]["id"]

    def add_issue(self, issue):
        """
        Add Issue ``issue`` to this project at GitHub. Update the issue fields in place.
        The issue must have been created first.
        """
        issue.fail_if_not_created()
        content_id = issue.node_id
        issue.item_node_id = self.create_item(content_id)

    def create_draft_issue(self, title, body):
        """
        Create draft issue item with ``title``  and ``body`` in this project at GitHub.
        Return the created item id.
        """
        query = """
        mutation($project_node_id: ID!, $title: String!, $body: String!) {
            addProjectV2DraftIssue(input: {projectId: $project_node_id, title: $title, body: $body}) {
                projectItem {
                    id
                }
            }
        }
        """
        variables = {
            "project_node_id": self.get_project_node_id(),
            "title": title,
            "body": body,
        }
        results = graphql_query(query=query, variables=variables)
        return results["data"]["addProjectV2DraftIssue"]["projectItem"]["id"]

    def set_fields(
        self,
        item_node_id,
        project_estimate,
        project_id,
        project_issue_id,
        status="",
        iteration="",
    ):
        """
        Update multiple fields of this project item with ``item_node_id``.

        The fields are hardcoded: ``project_estimate`` , ``project_isssue_id`` and ``project_id`` .
        This is designed to work on a multiple fields at once to avoid hitting rate limit too quickly.
        """
        assert item_node_id

        variables = {
            "project_node_id": self.get_project_node_id(),
            "item_node_id": item_node_id,
            "estimate_field_id": self.get_field_node_id("Estimate"),
            "estimate_value": project_estimate or 0,
            "issue_id_field_id": self.get_field_node_id("IssueID"),
            "issue_id_value": project_issue_id or "",
            "project_id_field_id": self.get_field_node_id("ProjectID"),
            "project_id_value": project_id or "",
        }

        with_status = bool(status)
        if with_status:
            status_variables = {
                "status_field_id": self.get_field_node_id("Status"),
                "status_value": self.get_field_option_id(field_name="Status", option_name=status) or "",
            }
            variables.update(status_variables)
            if DEBUG:
                click.echo(f"Updating Status field: {status} with {status_variables}")

        with_iteration = bool(iteration)
        if with_iteration:
            iteration_variables = {
                "iteration_field_id": self.get_field_node_id("Iteration"),
                "iteration_value": self.get_field_iteration_id(field_name="Iteration", iteration_title=iteration) or "",
            }
            variables.update(iteration_variables)
            if DEBUG:
                click.echo(f"Updating iIeration field: {iteration} with {iteration_variables}")

        query = get_fields_update_query(with_status=with_status, with_iteration=with_iteration)
        graphql_query(query=query, variables=variables)

    def get_project_node_id(self):
        """
        Return (through a cache) the remote GH project id
        """
        self.populate_project_node_id()
        return self.project_node_id

    def populate_project_node_id(self):
        """
        Fetch, and cache this project node id.
        """
        if self.project_node_id:
            return

        query = """query($account_name:String!, $project_number:Int!) {
            %s(login: $account_name) {
                projectV2(number: $project_number){
                    id
                }
            }
        }""" % (self.account_type)

        variables = {"account_name": self.account_name, "project_number": self.number}
        results = graphql_query(query=query, variables=variables)

        # sample: {"data":{"user":{"projectV2":{"id":"PVT_kwHOAApQnc4Au19y"}}}}
        self.project_node_id = results['data'][self.account_type]['projectV2']['id']

    def get_field_node_id(self, field_name):
        """
        Return the node id for a ``field_name``.
        """
        self.populate_field_ids_by_name()
        try:
            return self.field_ids_by_field_name[field_name]
        except KeyError as e:
            raise Exception(f"Custom field {field_name!r} is missing in project: {self.url}") from e

    def get_field_option_id(self, field_name, option_name):
        """
        Return the option id for a ``field_name`` and ``option_name``.
        This is a string and not an ID! from graphql point of view.
        """
        self.populate_field_ids_by_name()
        return self.field_select_option_ids_by_field_and_option_name[field_name].get(option_name)

    def get_field_iteration_id(self, field_name, iteration_title):
        """
        Return the iteration id for a ``field_name`` and ``iteration_title``.
        This is a string and not an ID! from graphql point of view.
        """
        self.populate_field_ids_by_name()
        return self.field_iteration_ids_by_field_and_iteration_title[field_name].get(iteration_title)

    def populate_field_ids_by_name(self):
        """
        Fetch and cache this project field names and node ids. This is a {name -> field_node_id}
        mapping for the project "plain" fields (text, date and numbers). This ignores field
        typename, datatype, and skip most special field types like iterations.

        SingleSelect are tracked with field_select_option_ids_by_field_and_option_name (
        like the important Status)

        Iteration are tracked with field_iteration_ids_by_field_and_iteration_title.

        """
        if self.field_ids_by_field_name:
            return

        query = """query($project_node_id:ID!) {
            node(id: $project_node_id) {
                ... on ProjectV2 {
                    fields(first: 30) {
                        nodes {
                            ... on ProjectV2Field {
                                id
                                name
                            }
                            ... on ProjectV2SingleSelectField {
                                id
                                name
                                options {
                                    id
                                    name
                                }
                            }
                            ... on ProjectV2IterationField {
                                id
                                name
                                configuration {
                                    iterations {
                                        id
                                        title
                                    }
                                }
                            }

                        }
                    }
                }
            }
        }
        """

        variables = {"project_node_id":self.get_project_node_id()}
        results = graphql_query(query=query, variables=variables)

        # results data shape
        """
        {
          "data": {
            "node": {
              "fields": {
                "nodes": [
                  {
                    "id": "PVTSSF_lADODNlKL84A-YHvzgxzFX8",
                    "name": "Status",
                    "options": [
                      {
                        "id": "f75ad846",
                        "name": "Todo"
                      },
                      {
                        "id": "98236657",
                        "name": "Done"
                      }
                    ]
                  },
                  {
                    "id": "PVTF_lADODNlKL84A-YHvzgxzFYg",
                    "name": "Estimate"
                  },
                  {
                    "id": "PVTF_lADODNlKL84A-YHvzgxzFYk",
                    "name": "IssueID"
                  },
                  {
                    "id": "PVTF_lADODNlKL84A-YHvzgxzFYo",
                    "name": "ProjectID"
                  },
                  {
                    "id": "PVTIF_lADODNlKL84A-YHvzgyACIw",
                    "name": "Iteration",
                    "configuration": {
                      "iterations": [
                        {
                          "id": "704b2d01",
                          "title": "Iteration 1"
                        },
                        {
                          "id": "2aa037b3",
                          "title": "Iteration 2"
                        }
                      ]
                    }
                  }
                ]
              }
            }
          }
        }
        """

        field_ids_by_field_name = {}
        field_option_ids_by_field_and_option_name = {}
        field_iteration_ids_by_field_and_iteration_title = {}

        for field in results["data"]["node"]["fields"]["nodes"]:
            # Some non-plain fields can be empty mappings
            # better be safe
            if not field:
                continue

            name = field.get("name")
            field_node_id = field.get("id")

            if not name or not field_node_id:
                continue

            field_ids_by_field_name[name] = field_node_id

            # singleselect
            options = field.get("options") or {}
            if options:
                optid_by_name = {opt["name"]: opt["id"] for opt in options}
                field_option_ids_by_field_and_option_name[name] = optid_by_name

            # iteration
            configuration = field.get("configuration") or {}
            if configuration:
                iterations = configuration.get("iterations") or []
                iterid_by_title = {it["title"]: it["id"] for it in iterations}
                field_iteration_ids_by_field_and_iteration_title[name] = iterid_by_title

        self.field_ids_by_field_name = field_ids_by_field_name
        self.field_select_option_ids_by_field_and_option_name = field_option_ids_by_field_and_option_name
        self.field_iteration_ids_by_field_and_iteration_title = field_iteration_ids_by_field_and_iteration_title

    def get_items(self, with_full_content=False):
        """
        Return a list of all items in this project
        This includes issues, pull requests and draft issues.
        Paginate as needed.
        """
        all_items = []

        has_next_page = True
        cursor = None

        full_content = """
                      content {
                        ... on DraftIssue {
                          title
                          body
                        }
                        ... on Issue {
                          id
                          number
                          title
                          url
                          updatedAt
                          assignees(first: 10) {
                            nodes {
                              login
                            }
                          }
                          labels(first: 10) {
                            nodes {
                              name
                            }
                          }
                        }
                        ... on PullRequest {
                          id
                          number
                          title
                          url
                          updatedAt
                          assignees(first: 10) {
                            nodes {
                              login
                            }
                          }
                          labels(first: 10) {
                            nodes {
                              name
                            }
                          }
                        }
                      }
        """

        mini_content = """
                      content {
                        ... on Issue {
                          id
                          number
                          url
                        }
                        ... on PullRequest {
                          id
                          number
                          url
                        }
                      }
        """

        content = full_content if with_full_content else mini_content

        while has_next_page:
            query = ("""
            query($project_node_id: ID!, $cursor: String) {
              node(id: $project_node_id) {
                ... on ProjectV2 {
                  items(first: 100, after: $cursor) {
                    pageInfo {
                      hasNextPage
                      endCursor
                    }
                    nodes {
                      id
                      project_id: fieldValueByName(name: "ProjectID") {
                        ... on ProjectV2ItemFieldTextValue {
                          text
                        }
                      }
                      issue_id: fieldValueByName(name: "IssueID") {
                        ... on ProjectV2ItemFieldTextValue {
                          text
                        }
                      }
                      estimate: fieldValueByName(name: "Estimate") {
                        ... on ProjectV2ItemFieldNumberValue {
                          number
                        }
                      }
                      status: fieldValueByName(name: "Status") {
                        ... on ProjectV2ItemFieldSingleSelectValue {
                          name
                        }
                      }
                      iteration: fieldValueByName(name: "Iteration") {
                        ... on ProjectV2ItemFieldIterationValue {
                          title
                        }
                      }

                      %s
                    }
                  }
                }
              }
            }
            """ % (
                content,
                )
            )
            variables = {"project_node_id": self.get_project_node_id(), "cursor": cursor}
            results = graphql_query(query=query, variables=variables)

            items = results["data"]["node"]["items"]

            all_items.extend(items["nodes"])

            page_info = items["pageInfo"]
            has_next_page = page_info["hasNextPage"]
            cursor = page_info["endCursor"]
        return all_items


def get_fields_update_query(with_status=False, with_iteration=False):

    status_vars = """
            $status_field_id:ID!
            $status_value:String!
            """ if with_status else ""

    status_update = """
            update_status_id: updateProjectV2ItemFieldValue(
                input: {
                    projectId: $project_node_id
                    itemId: $item_node_id
                    fieldId: $status_field_id
                    value: { singleSelectOptionId: $status_value }
                }
            )
            { projectV2Item { id } }
    """ if with_status else ""

    iteration_vars = """
            $iteration_field_id:ID!
            $iteration_value:String!
            """ if with_iteration else ""

    iteration_update = """
            update_iteration_id: updateProjectV2ItemFieldValue(
                input: {
                    projectId: $project_node_id
                    itemId: $item_node_id
                    fieldId: $iteration_field_id
                    value: { iterationId: $iteration_value }
                }
            )
            { projectV2Item { id } }
    """ if with_iteration else ""

    query = ("""
        mutation(
            $project_node_id:ID!
            $item_node_id:ID!

            $estimate_field_id:ID!
            $estimate_value:Float!

            $issue_id_field_id:ID!
            $issue_id_value:String!

            $project_id_field_id:ID!
            $project_id_value:String!

            %s

            %s
        ) {
            update_estimate: updateProjectV2ItemFieldValue(
                input: {
                    projectId: $project_node_id
                    itemId: $item_node_id
                    fieldId: $estimate_field_id
                    value: { number: $estimate_value }
                }
            )
            { projectV2Item { id } }

            update_issue_id: updateProjectV2ItemFieldValue(
                input: {
                    projectId: $project_node_id
                    itemId: $item_node_id
                    fieldId: $issue_id_field_id
                    value: { text: $issue_id_value }
                }
            )
            { projectV2Item { id } }

            update_project_id: updateProjectV2ItemFieldValue(
                input: {
                    projectId: $project_node_id
                    itemId: $item_node_id
                    fieldId: $project_id_field_id
                    value: { text: $project_id_value }
                }
            )
            { projectV2Item { id } }

            %s

            %s
       }
    """) % (
        status_vars,
        iteration_vars,
        status_update ,
        iteration_update ,
    )

    return query


def load_issues(location, max_load=0):
    """
    Load issues from the CSV file at ``location``.
    Return a list of Issue. Raise exception on errors.
    Limit loading up to ``max_load`` issues. Load all if ``max_load`` is 0.
    """
    issues = []
    issues_by_project_issue_id = {}
    subissues_by_parent_id = defaultdict(list)
    parents_by_subissue_id = defaultdict(list)

    with open(location) as issues_data:
        for i, issue_data in enumerate(csv.DictReader(issues_data), 1):

            issue = Issue.from_data(data=issue_data)
            issues.append(issue)
            project_issue_id = issue.project_issue_id

            if project_issue_id:
                assert project_issue_id not in issues_by_project_issue_id, f"Duplicated issue id: {issue!r}"
                issues_by_project_issue_id[project_issue_id] = issue

                project_parent_issue_id = issue.project_parent_issue_id
                if project_parent_issue_id:

                    if project_parent_issue_id == project_issue_id:
                        raise Exception(f"Subissue {project_issue_id} cannot be its ownparent")

                    # avoid dupes: subissue can only be in one parent, and cannot be twice in a parent
                    if project_parent_issue_id in parents_by_subissue_id[project_issue_id]:
                        raise Exception(
                            f"Subissue {project_issue_id} cannot have more than one parent: "
                            f"{subissues_by_parent_id[project_parent_issue_id]}")

                    parents_by_subissue_id[project_issue_id].append(project_parent_issue_id)

                    if project_issue_id in subissues_by_parent_id[project_parent_issue_id]:
                        raise Exception(
                            f"Subissue {project_issue_id} cannot be duplicated in parent: "
                            f"{project_parent_issue_id}")

                    subissues_by_parent_id[project_parent_issue_id].append(project_issue_id)

            if max_load and i >= max_load:
                break

    for parent_id, project_subissue_ids in subissues_by_parent_id.items():
        assert parent_id in issues_by_project_issue_id, (
            f"Orphaned parent_id: {parent_id!r} in sub issue ids: {project_subissue_ids!r}"
        )
        issue = issues_by_project_issue_id[parent_id]
        issue.project_subissue_ids = project_subissue_ids

    return issues


def dump_csv_sample(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    click.echo(open("issues.csv").read())
    ctx.exit()


@click.command()
@click.pass_context
@click.option(
    "-i",
    "--issues-file",
    type=click.Path(exists=True, readable=True, path_type=str, dir_okay=False),
    metavar="FILE",
    multiple=False,
    required=True,
    help="Path to a CSV file listing issues to create, one per line.",
)
@click.option(
    "-m",
    "--max-import",
    type=int,
    default=0,
    help="Maximum number of issues to import. Default to zero to import all issues in FILE.",
)
@click.option(
    "--csv-sample",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=dump_csv_sample,
    help='Dump a sample CSV on screen and exit. See also the "issues.csv" file',
)
@click.help_option("-h", "--help")
def import_issues_in_github(ctx, issues_file, max_import=0):
    """
    Import issues in GitHub as listed in the CSV FILE.

    You must set the GITHUB_TOKEN environment variable with a token for authentication with GitHub.
    The token must have the proper permissions to create issues and update projects.

    Use the "--csv-sample" option to print a CSV sample with all the supported columns.
    """

    if not GITHUB_TOKEN:
        click.echo("You must set the GITHUB_TOKEN environment variable to a Github token.")
        ctx.exit(1)

    issues = load_issues(location=issues_file, max_load=max_import)

    if max_import:
        click.echo(f"Importing up to {max_import} issues in GitHub from {len(issues)} total.")
    else:
        click.echo(f"Importing {len(issues)} issues in GitHub")

    for issue in issues:
        issue.create_issue_and_add_to_project()

    click.echo("Creating sub issues")
    # once all issues are created we can create subissues
    issue_by_project_issue_id = {
        issue.project_issue_id: issue for issue in issues if issue.project_issue_id
    }

    for issue in issues:
        for project_subissue_id in issue.project_subissue_ids:
            subissue = issue_by_project_issue_id[project_subissue_id]
            click.echo(f"  Create sub issue for parent issue: {issue.url}")
            click.echo(f"    Sub-issue: {subissue.url}")
            try:
                issue.add_subissue(subissue=subissue)
            except:
                click.echo(f"  Failed to create sub issue for parent issue: {issue!r}")
                click.echo(f"    Sub-issue: {subissue!r}")
                raise

    click.echo("Importing done.")


if __name__ == "__main__":
    import_issues_in_github()
