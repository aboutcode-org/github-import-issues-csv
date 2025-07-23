# GitHub Issues and Project issues importer

### Description

This script creates new GitHub issues in repositories from a CSV file
and optionally, to add these issues to Projects.

The script reads issues, one per row, from a CSV file and creates corresponding GitHub issues.

The goal is to make it easier to manage and track many issues and tasks using GitHub's project
management system.

This is forked from @goldhaxx https://github.com/goldhaxx/github-projects-task-uploader and
heavily modified

### License

MIT License

Copyright (c) 2024 goldhaxx, nexB and others


### Features

- Bulk import from CSV to GitHub as issues.
- Optionally add these issues to a Project.
- Labels are also imported. Use a list sperated by coma 
- Issues can have a single parent issue. They will be also added to the parent as GitHub sub-issues.
- Support for custom fields in the CSV, thta are added to the Project items as custom fields:
  - Estimate is imported from the "project_estimate" column with a rough estimate to complete in
    number of days
  - IssueID is imported from the "project_issue_id" column and is used to track these external ids
    we assign to an issue.
  - ParentIssueID is imported from the "project_parent_issue_id" column and is used to track
    external ids we assign to an issue. Use to create subissues.
  
Not supported: Nothing not listed above including
- Priority, iterations, size and status.
- Assignees.

### Getting started

#### Prerequisites

- Python 3.x installed on your system.
- A GitHub personal access token with `repo` permissions exported as a GITHUB_TOKEN variable
- A CSV file containing task information to upload.

This script read a CSV and creates GitHub issues, and add these to GitHub projects.
You need first to install these dependencies in your virtualenv::

    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

Then run the script this way::

    python src/import_issues.py --help

You need to have pre-existing repositories and projects created in GitHub.


#### CSV File Format

See the issues.csv for an example.

The CSV has these columns:

Core fields:
- account_type: required for projects, GitHub account type: either a "user" or an "organization"
- account_name: required, GitHub account name that owns the repo_name where to create the issues
  (and who owns the optional "project_number" to append issues to.)
- repo_name: required, GitHub repo name where to create the issues

- title: required, GitHub issue title.
- body: required, GitHub issue body.


##### Optional Project support:

- project_number: optional for projects, GitHub project number in the "account_name". The issue
  will be added to this project.

Optional Project fields support:

- project_estimate: a rough estimate to complete this issue as a number of days.
  This is used to populate an "Estimate" custom project field that needs to be created first as
  a "number" field in the Project.

##### Optional issue ids and subissues support:

We can related issues to parent issues using "subissues". We use two columns for issues id and subissues:

- "project_issue_id": arbitrary issue id string. Must be unique across all rows in a project import.
  Used to populate the IssueID project field.

- "project_parent_issue_id": arbitrary parent issue id string used to uniquely identify a parent issue.
  Must exist also as a project_issue_id. An issue can only have a sinple unique parent.
  Used to populate the ParentIssueID project field.
