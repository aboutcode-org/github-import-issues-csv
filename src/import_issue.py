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

You need to have pre-existing repositories and projects created in GitHub.


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
  This is used to populate an "Estimate" custom project field that needs to be created first as
  a "number" field in the project.

Optional Meta issues support:

We can import plain issues as well as "meta issues". A meta issue body contains a bulleted list of
checkboxes with links to all its "sub issues". GitHub recognizes these links as "tasks".

We use three columns for meta issues and their sub issues:

- "meta_issue_id": arbitrary meta issue id string, used to relate "sub issues" to a "meta issue".
- "sub_issue_id": arbitrary sub issue id string used to uniquely identify a sub issue within a meta issue.

With meta issues, a row can have:

- no "meta_issue_id": this is a plain issue, e.g., neither a meta nor a subissue.

- only a "meta_issue_id" value and no "sub_issue_id" value: this means this is a "meta issue" row.
  This meta_issue_id must be unique across all other meta issue rows.

- a "meta_issue_id" and a "sub_issue_id" value: this means this is a "sub issue" row for the meta
  issue of this "meta_issue_id". The combo of meta_issue_id and sub_issue_id must be
  unique across all issue rows.

For meta issues, the body will be extended with a bulleted list of links to sub issues.

If the project has an IssueID custom field:
- For meta issues, the IssueID is updated with "meta_issue_id" value
- For sub issues, the IssueID is updated with the combined values of "meta_issue_id-sub_issue_id"
"""

import csv
import dataclasses
import os
import time

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

# Rate limiter settings
# Maximum number of requests allowed within the time frame
RATE_LIMIT_MAX_REQUESTS = 60
# Time frame for rate limiting in seconds
RATE_LIMIT_TIME_FRAME = 60

DEBUG = False
VERBOSE = True


class RateLimiter:

    def __init__(self, max_requests, time_frame):
        self.max_requests = max_requests
        self.time_frame = time_frame
        self.requests = []

    def wait(self):
        time.sleep(0.5)
        now = time.time()
        if len(self.requests) >= self.max_requests:
            wait_time = self.time_frame - (now - self.requests[0])
            if wait_time > 0:
                if wait_time > 0.01:
                    click.echo(f"\n==> Rate limiter waiting: {wait_time:.2f} seconds")
                time.sleep(wait_time)
            self.requests = [r for r in self.requests if now - r <= self.time_frame]
        self.requests.append(now)


rate_limiter = RateLimiter(max_requests=RATE_LIMIT_MAX_REQUESTS, time_frame=RATE_LIMIT_TIME_FRAME)


def handle_rate_limit(response):
    """
    Wait according to the rate limit headers in ``response``.
    Return True if the rate limit was exceeded and the request was throttled, False otherwise.
    Riase Exceptions on errors.
    """
    if response.status_code in (403, 429):
        reset_time = int(response.headers.get('x-ratelimit-reset', 0))
        current_time = int(time.time())
        sleep_time = max(reset_time - current_time, 60)
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
    if not VERBOSE:
        return
    limit = response.headers.get('x-ratelimit-limit')
    remaining = response.headers.get('x-ratelimit-remaining')
    used = response.headers.get('x-ratelimit-used')
    reset = response.headers.get('x-ratelimit-reset')
    resource = response.headers.get('x-ratelimit-resource')

    if all([limit, remaining, used, reset, resource]):
        reset_time = datetime.fromtimestamp(int(reset)).strftime('%Y-%m-%d %H:%M:%S')
        click.echo(f"\nRate Limit Status:{used} of {limit}/{remaining} Reset Time: {reset_time} Resource: {resource}")
    else:
        click.echo("Rate limit information not available in the response headers.")


@dataclasses.dataclass(kw_only=True)
class Issue:
    """
    A GitHub issue with is ttitle and body.
    """

    # Do not set: the issue number, automatically set upon creation
    number: int = 0
    # Do not set: used in graphql, automatically set upon creation
    issue_id: str = ""

    # Required
    title: str = ""
    body: str = ""
    # one of user or organization
    account_type: str = "organization"
    account_name: str = ""
    repo_name: str = ""

    # Optional fields, if we add the issue to a GitHub project
    project_number: int = 0
    project_estimate: int = 0

    # Do not set: used in graphql, automatically set upon creation
    project_item_id: str = ""

    # Optional:
    # an arbitrary string used to identify and relate to a meta issue:
    # - if this is an Issue instance, we will add the issue to a MetaIssue with this identifier
    # - if this is a MetaIssue instance, that's the MetaIssue identifier
    meta_issue_id: str = ""

    # Optional: number to track related issues with structured issues number
    sub_issue_id: str = ""

    def __post_init__(self):
        assert self.title, f"Missing title: {self!r}"
        assert self.body, f"Missing body: {self!r}"
        assert self.account_type in ("user", "organization") , f"Invalid account type: {self!r}"
        assert self.account_name, f"Missing account name: {self!r}"
        assert self.repo_name, f"Missing repo name: {self!r}"

        if self.project_estimate:
            assert self.project_number

        if not self.meta_issue_id:
            assert not self.sub_issue_id

        if self.sub_issue_id:
            assert self.meta_issue_id

    @property
    def project_issue_id(self):
        if self.meta_issue_id:
            return f"{self.meta_issue_id}-{self.sub_issue_id}"
        else:
            return ""

    @property
    def is_sub_issue(self):
        return bool(self.meta_issue_id and self.sub_issue_id)

    @property
    def url(self):
        return f"https://github.com/{self.account_name}/{self.repo_name}/issues/{self.number}"

    def get_body(self):
        """Return the body. Subclasses can override"""
        return self.body

    def create(self, headers=auth_headers, retries=0):
        """
        Create issue at GitHub and update thyself.
        NB: this does not check if the same issue exists.
        """
        rate_limiter.wait()
        api_url = f"https://api.github.com/repos/{self.account_name}/{self.repo_name}/issues"
        request_data = {"title": self.title, "body": self.get_body()}

        response = requests.post(url=api_url, headers=headers, json=request_data)

        try:
            throttled = handle_rate_limit(response)
            if throttled and retries < 2:
                retries += 1
                self.create(headers=headers, retries=retries)

        except Exception as e:
            raise Exception(
                f"Failed to create issue: {self!r}\n\n"
                f"with request: {request_data}\n\n"
                f"and response: {response}"
            ) from e

        check_rate_limit_status(response)

        results = response.json()
        self.number = results["number"]
        self.issue_id = results["node_id"]

    def add_to_project(self, update_fields=True):
        """
        Add this issue to its project, if this issue has a "project_number".
        If ``update_fields`` is True, also sets custom project field values if present.
        This includes the estimate and task_number
        """
        assert self.number, f"Issue: {self.title} must be created first at GitHub"
        if project := self.get_project():
            project.add_issue(self)
            if update_fields:
                # make only a single grphql request to update both fields
                combined_update = True
                if combined_update:
                    update_project_issue_fields(
                        project=project,
                        item_id=self.project_item_id,
                        estimate=self.project_estimate or 0,
                        issueid=self.project_issue_id,
                    )

                else:
                    # Update estimate field, if present
                    if estimate := self.project_estimate:
                        project.update_number_field(item_id=self.project_item_id, field_name="Estimate", value=estimate)

                    # Update IssueID if we have meta/sub issues id fields
                    if issue_id := self.project_issue_id:
                        project.update_text_field(item_id=self.project_item_id, field_name="IssueID", value=issue_id)

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

    @classmethod
    def from_data(cls, data):
        """
        Create and return an Issue from a ``data`` mapping.
        """
        return cls(
            title=data["title"].strip(),
            body=data["body"].strip(),
            account_type=data["account_type"].strip(),
            account_name=data["account_name"].strip(),
            repo_name=data["repo_name"].strip(),
            # force int
            project_number=int(data.get("project_number", "").strip() or 0),
            # force int
            project_estimate=int(data.get("project_estimate", "").strip() or 0),
            meta_issue_id=data.get("meta_issue_id", "").strip() or "",
            sub_issue_id=data.get("sub_issue_id", "").strip() or "",
        )


@dataclasses.dataclass(kw_only=True)
class MetaIssue(Issue):
    """
    A meta issue is an issue with a body that contains a bulleted list of sub issues URLs, that
    GitHub interprets as "tasks".
    """

    issues: List[Issue] = dataclasses.field(default_factory=list)

    def get_body(self):
        sub_issues_lines = "\n".join([f"- [ ] {i.url}" for i in self.issues])
        body = f"{self.body}\n\n{sub_issues_lines}\n"
        return body

    @property
    def project_issue_id(self):
        return self.meta_issue_id


def graphql_query(query, variables=None, headers=auth_headers, retries=0):
    """
    Post ``request_data`` as GraphQL API query and return results. Raise Exceptions on errors.
    """
    rate_limiter.wait()

    api_url = "https://api.github.com/graphql"
    request_data = {"query":query, "variables":variables}

    if DEBUG:
        click.echo()
        click.echo(f"GraphQL query: {request_data}")
        click.echo()

    response = requests.post(url=api_url, headers=headers, json=request_data)

    try:
        throttled = handle_rate_limit(response)
        if throttled and retries < 2:
            retries += 1
            graphql_query(query=query, variables=variables, headers=headers, retries=retries)
            time.sleep(2)
    except Exception as e:
        raise Exception(
            f"Failed to post GraphQL query with request: {request_data}\n\n"
            f"and response: {response}"
        ) from e

    check_rate_limit_status(response)

    results = response.json()
    if 'errors' in results:
        raise Exception(
            f"GraphQL query error: {results['errors']}\n\n"
            f"query: {query}\n"
            f"variables: {variables}"
        )
    return results


@dataclasses.dataclass(kw_only=True)
class Project:
    """
    A GitHub project, identified by its project number  in a GitHub account.
    """
    # a cache of all projects, keyed by number
    projects_by_number = {}

    number: int = 0
    project_id: str = ""

    # one of user or organization
    account_type: str = "organization"
    account_name: str = ""

    # {name -> field_node_id} mapping for the project "plain" fields (text, date and numbers).
    fields: Dict[str, str] = dataclasses.field(default_factory=dict)

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
        Return a new or existing Project object.
        (Does NOT create anything at GitHub)
        """
        if existing := cls.projects_by_number.get(number):
            return existing

        project = Project(number=number, account_type=account_type, account_name=account_name)
        cls.projects_by_number[number] = project
        return project

    def add_issue(self, issue):
        """
        Add Issue ``issue`` to this project at GitHub.
        The issue must have been created first.
        """
        if not issue.number:
            raise Exception(f"Issue: {issue.title} has no number and must be created first at GitHub")

        query = """mutation($project_id:ID!, $issue_id:ID!) {
            addProjectV2ItemById(input: {projectId: $project_id, contentId: $issue_id })
            {
                item { id }
            }
        }
        """

        project_id = self.get_project_id()
        variables = {"project_id": project_id, "issue_id": issue.issue_id}

        results = graphql_query(query=query, variables=variables)
        issue.project_item_id = results['data']['addProjectV2ItemById']['item']['id']

    def get_project_id(self):
        self.populate_project_id()
        return self.project_id

    def populate_project_id(self):
        """
        Fetch, and cache this project id.
        """
        if self.project_id:
            return

        query = """query($account_name:String!, $number:Int!) {
            %s(login: $account_name) {
                projectV2(number: $number) {id}
            }
        }""" % (self.account_type)

        variables = {"account_name": self.account_name, "number": self.number}
        results = graphql_query(query=query, variables=variables)

        # sample: {"data":{"user":{"projectV2":{"id":"PVT_kwHOAApQnc4Au19y"}}}}
        self.project_id = results['data'][self.account_type]['projectV2']['id']

    def get_field_id(self, field_name):
        self.populate_field_names_by_id()
        return self.fields[field_name]

    def populate_field_names_by_id(self):
        """
        Fetch and cache this project field names and node ids. This is a {name -> field_node_id}
        mapping for the project "plain" fields (text, date and numbers). This ignores field
        typename, datatype, and skip special fields like iterations and select.
        """
        if self.fields:
            return

        query = """query($project_id:ID!) {
            node(id: $project_id) {
                ... on ProjectV2 {
                    fields(first: 20) {
                        nodes {
                            ... on ProjectV2Field { id name }
                        }
                    }
                }
            }
        }
        """

        variables = {"project_id":self.get_project_id()}
        results = graphql_query(query=query, variables=variables)

        # results data shape
        """
        {
          "data": {
            "node": {
              "fields": {
                "nodes": [
                  {
                    "id": "PVTF_lAHOAApQnc4Au19yzglXyEc",
                    "name": "Title"
                  },
                  ............
                ]
              }
            }
          }
        }
        """

        for field in results["data"]["node"]["fields"]["nodes"]:
            # some non-plain fields can be empty mappings
            # better be safe
            if field and (name := field.get("name")) and (field_id := field.get("id")):
                self.fields[name] = field_id

    def update_number_field(self, item_id, field_name, value):
        """
        Update a "number" field.
        """
        update_field(project=self, item_id=item_id, field_name=field_name, value=float(value), query=UPDATE_NUMBER_MUTATION_QUERY)

    def update_text_field(self, item_id, field_name, value):
        """
        Update a "string/text" field.
        """
        update_field(project=self, item_id=item_id, field_name=field_name, value=str(value), query=UPDATE_TEXT_MUTATION_QUERY)


UPDATE_ESTIMATE_AND_ISSUEID_MUTATION_QUERY = """
    mutation(
        $project_id:ID!,
        $item_id:ID!,

        $estimate_field_id:ID!,
        $estimate_value:Float!,

        $issueid_field_id:ID!,
        $issueid_value:String!
    ) {
        update_estimate: updateProjectV2ItemFieldValue(
            input: {
                projectId: $project_id
                itemId: $item_id
                fieldId: $estimate_field_id
                value: { number: $estimate_value }
            }
        )
        { projectV2Item { id } }

        update_issueid: updateProjectV2ItemFieldValue(
            input: {
                projectId: $project_id
                itemId: $item_id
                fieldId: $issueid_field_id
                value: { text: $issueid_value }
            }
        )
        { projectV2Item { id } }
    }
"""

UPDATE_ESTIMATE_MUTATION_QUERY = """
    mutation(
        $project_id:ID!,
        $item_id:ID!,

        $estimate_field_id:ID!,
        $estimate_value:Float!,

    ) {
        update_estimate: updateProjectV2ItemFieldValue(
            input: {
                projectId: $project_id
                itemId: $item_id
                fieldId: $estimate_field_id
                value: { number: $estimate_value }
            }
        )
        { projectV2Item { id } }

    }
"""

UPDATE_ISSUEID_MUTATION_QUERY = """
    mutation(
        $project_id:ID!,
        $item_id:ID!,

        $issueid_field_id:ID!,
        $issueid_value:String!
    ) {
        update_issueid: updateProjectV2ItemFieldValue(
            input: {
                projectId: $project_id
                itemId: $item_id
                fieldId: $issueid_field_id
                value: { text: $issueid_value }
            }
        )
        { projectV2Item { id } }
    }
"""


def update_project_issue_fields(project, item_id, estimate, issueid):
    """
    Update a the ``estimate`` and ``isssueid`` fields of the ``project`` item ``item_id``.
    """
    # note that this is rather ugly code, but updating multiple fields in GraphQL @ GH is ugly

    if not estimate and not issueid:
        return
    variables = {
        "project_id": project.get_project_id(),
        "item_id": item_id,
    }

    if estimate and issueid:
        query = UPDATE_ESTIMATE_AND_ISSUEID_MUTATION_QUERY
    elif estimate:
        query = UPDATE_ESTIMATE_MUTATION_QUERY
    elif issueid:
        query = UPDATE_ISSUEID_MUTATION_QUERY

    if estimate:
        variables.update(
            {
                "estimate_field_id": project.get_field_id("Estimate"),
                "estimate_value": estimate,
            }
        )
    if issueid:
        variables.update(
            {
                "issueid_field_id": project.get_field_id("IssueID"),
                "issueid_value": issueid,
            }
        )
    graphql_query(query=query, variables=variables)


UPDATE_NUMBER_MUTATION_QUERY = """mutation($project_id:ID!, $item_id:ID!, $field_id:ID!, $value:Float!) {
    updateProjectV2ItemFieldValue(input: {
        projectId: $project_id
        itemId: $item_id
        fieldId: $field_id
        value: { number: $value }
    })
    { projectV2Item { id } }
}
"""

UPDATE_TEXT_MUTATION_QUERY = """mutation($project_id:ID!, $item_id:ID!, $field_id:ID!, $value:String!) {
    updateProjectV2ItemFieldValue(input: {
        projectId: $project_id
        itemId: $item_id
        fieldId: $field_id
        value: { text: $value }
    })
    { projectV2Item { id } }
}
"""


def update_field(project, item_id, field_name, value, query):
    """
    Update a field with ``field_name`` to ``value`` for the project item ``item_id`` using the
    ``query`` GraphQL mutation.
    """
    variables = {
        "project_id": project.get_project_id(),
        "item_id": item_id,
        "field_id": project.get_field_id(field_name),
        "value": value
    }

    graphql_query(query=query, variables=variables)


def load_issues(location, max_load=0):
    """
    Load issues from the CSV file at ``location``.
    Return a tuple of ([list of Issue], [list of MetaIssue])
    Raise exception on errors.
    Limit loading up to "max_import" issues. Load all if max_load is zero.
    """
    issues = []
    meta_issues_by_id = {}

    with open(location) as issues_data:
        for i, issue_data in enumerate(csv.DictReader(issues_data), 1):

            meta_issue_id = issue_data.get("meta_issue_id", "").strip() or ""
            sub_issue_id = issue_data.get("sub_issue_id", "").strip() or ""

            is_meta_issue = bool(meta_issue_id and not sub_issue_id)

            cls = MetaIssue if is_meta_issue else Issue
            issue = cls.from_data(data=issue_data)

            if is_meta_issue:
                if existing_meta := meta_issues_by_id.get(meta_issue_id):
                    raise Exception(
                        f"Duplicated meta issue identifier: {meta_issue_id}: "
                        f"existing: {existing_meta.title!r} "
                        f"new: {issue.title!r} "
                    )
                meta_issues_by_id[meta_issue_id] = issue
            else:
                issues.append(issue)
            if max_load and i >= max_load:
                break

    for issue in issues:
        if meta_id := issue.meta_issue_id:
            meta_issue = meta_issues_by_id[meta_id]
            meta_issue.issues.append(issue)

    return issues, list(meta_issues_by_id.values())


def dump_csv_sample(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    click.echo('''meta_issue_id,sub_issue_id,account_type,account_name,repo_name,project_number,title,body,project_estimate
gizmo,,organization,allthelibraries,test-repo-issues2,1,Create gizmo application,"The goal of this main issue is to create a new gizmo app.
There are multiple items we need to  complete for that:",0
gizmo,a,organization,allthelibraries,test-repo-issues1,1,Design Gizmo data model,"A gizmo has two fields as follow:
- [ ] foo
- [ ] bar
",2
gizmo,b,organization,allthelibraries,test-repo-issues2,1,Create Gizmo back-end,We need to create a backend with a rest API,3
gizmo,c,organization,allthelibraries,test-repo-issues2,1,Create Gizmo front-end UI,We need to create a frontend with a web UI,4
bidule,,organization,allthelibraries,test-repo-issues1,1,Create bidule application,"The goal of this main issue is to create a new bidule app.
There are multiple items we need to  complete for that:",0
bidule,d,organization,allthelibraries,test-repo-issues1,1,Design bidule data model,"A bidule has two fields as follow:
- [ ] foo
- [ ] bar
",5
bidule,d,organization,allthelibraries,test-repo-issues2,1,Create bidule back-end,We need to create a backend with a rest API,6
bidule,f,organization,allthelibraries,test-repo-issues2,1,Create bidule front-end UI,We need to create a frontend with a web UI,7
,,organization,allthelibraries,test-repo-issues2,,"Plain issue, no project",Plain,
,,organization,allthelibraries,test-repo-issues2,2,"Plain issue, in project 2",Plain 2,9
meta,,organization,allthelibraries,test-repo-issues2,2,"Meta issue, in project 2, no sub",Plain 3,10
metasub,,organization,allthelibraries,test-repo-issues2,,"Meta issue, NO project with sub",Meta plain,
metasub,subissue,organization,allthelibraries,test-repo-issues2,,"Sub issue, NO project with sub",Sub plain,
truc,,organization,allthelibraries,test-repo-issues1,,Create truc application NO project ,"The goal of this main issue is to create a new truc app.
There are multiple items we need to  complete for that:",
truc,d,organization,allthelibraries,test-repo-issues1,,Design truc data model NO project ,"A truc has two fields as follow:
- [ ] foo
- [ ] bar
",
truc,d,organization,allthelibraries,test-repo-issues2,,Create truc back-end NO project ,We need to create a backend with a rest API,
truc,f,organization,allthelibraries,test-repo-issues2,,Create truc front-end UI NO project ,We need to create a frontend with a web UI,
''')
    ctx.exit()


def create_issue_and_add_to_project(issue):
    issue.create()
    click.echo(f"Created Issue: URL: {issue.url} - {issue.title} ", nl="")
    project = issue.get_project()
    if project:
        issue.add_to_project()
        click.echo(f"... and added to Project: {project.url}")
    else:
        click.echo("")


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
    help='Dump a sample CSV on screen and exit.',
)
@click.help_option("-h", "--help")
def import_issues_in_github(ctx, issues_file, max_import=0):
    """
    Import issues in GitHub as listed in the CSV FILE.

    You must set the GITHUB_TOKEN environment variable with a token for authentication with GitHub.
    The token must have the proper permissions to create issues and update projects.

    Use the "--csv-sample" option to print a CSV sample.
    """

    if not GITHUB_TOKEN:
        click.echo("You must set the GITHUB_TOKEN environment variable to a Github token.")
        ctx.exit(1)

    issues, meta_issues = load_issues(location=issues_file, max_load=max_import)

    if max_import:
        click.echo(f"Importing up to {max_import} issues in GitHub")
    else:
        click.echo("Importing issues in GitHub")

    for issue in issues:
        create_issue_and_add_to_project(issue)

    click.echo("\nImporting meta issues in GitHub")
    for issue in meta_issues:
        create_issue_and_add_to_project(issue)

    click.echo("Importing done.")


if __name__ == "__main__":
    import_issues_in_github()
