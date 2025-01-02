# GitHub Issues and Project importer

### Description

This script is a simple tool designed to create new issues from a CSV file into GitHub repositories
and optionally, to add these issues to Projects.

The script reads issues, one per row, from a CSV file and creates corresponding GitHub issues, 

The goal is to make it easier to manage and track many issues and tasks using GitHub's project
management system.

This is forked from @goldhaxx https://github.com/goldhaxx/github-projects-task-uploader and
heavily modified

### License

MIT License

Copyright (c) 2024 goldhaxx, nexB and others


### Features

- Bulk import from CSV to GitHub as issues.
- Optional addition of these issues to a Project.
- Support for "meta" issue that reference multiple "sub" issues in their body. 
- Support for two custom fields in the CSV: Estimate and IssueID
  - Estimate is imported from the "project_estimate" column with a rough estimate to complete in number of days
  - IssueID is computed from the concatenation of "meta_issue_id-sub_issue_id" columns and is used to track these original ids
  
Not supported: Nothing not listed above.
- Labels, priority, iterations, size and status.
- Assignees.

### Getting started

#### Prerequisites

- Python 3.x installed on your system.
- A GitHub personal access token with `repo` permissions exported as a GITHUB_TOKEN variable
- A CSV file containing task information to upload.

This script read a CSV and creates GitHub issues, and add these to GitHub projects.
You need first to install these dependencies in your virtualenv::

    pip install click requests

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

##### Optional Meta issues support:

We can import plain issues as well as "meta issues". A meta issue body contains a bulleted list of
checkboxes with links to all its "sub issues". GitHub recognizes these links as "tasks".

We use two columns for meta issues and their sub issues:

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

