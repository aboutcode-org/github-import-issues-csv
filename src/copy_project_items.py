#
# Copyright (c) nexB Inc. and others. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# See http://www.apache.org/licenses/LICENSE-2.0 for the license text.
# See https://aboutcode.org for more information about nexB OSS projects.
#

"""
Copy all items of a GitHub project into another project, including fields.
"""

import json

import click

from import_issue import Item
from import_issue import Project
from import_issue import GITHUB_TOKEN


@click.command()
@click.pass_context
@click.option(
    "-s",
    "--source-project-number",
    type=int,
    multiple=False,
    required=True,
    help="Source GitHub project number to copy from.",
)

@click.option(
    "-t",
    "--target-project-number",
    type=int,
    multiple=False,
    required=True,
    help="Target GitHub project number to copy to.",
)

@click.option(
    "-n",
    "--account-name",
    type=str,
    required=True,
    help="GitHub account name, i.e, the user or organization name.",
)

@click.option(
    "-a",
    "--account-type",
    type=str,
    default="organization",
    help="GitHub account type: one of user or organization.",
)

@click.option(
    "-m",
    "--max-copy",
    type=int,
    default=0,
    help="Maximum number of items to copy. Default to zero to import all items from source.",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Print raw JSON results for source project. DO NOT COPY.",
)

@click.help_option("-h", "--help")
def copy_github_project_items(
    ctx,
    source_project_number: int,
    target_project_number: int,
    account_name:str,
    account_type: str="organization",
    max_copy=0,
    debug=False,
):
    """
    Copy GitHub project items from source to taregt project number..

    You must set the GITHUB_TOKEN environment variable with a token for authentication with GitHub.
    The token must have the proper permissions to create issues and update projects.

    """

    if not GITHUB_TOKEN:
        click.echo("You must set the GITHUB_TOKEN environment variable to a Github token.")
        ctx.exit(1)

    if debug:
        debug_project_items_from_source(
            source_project_number=source_project_number,
            account_name=account_name,
            account_type=account_type,
        )
    else:
        copy_github_project_items_from_source_to_target(
            source_project_number=source_project_number,
            target_project_number=target_project_number,
            account_name=account_name,
            account_type=account_type,
            max_copy=max_copy,
        )


def debug_project_items_from_source(
    source_project_number: int,
    account_name:str,
    account_type: str="organization",
):
    """
    Dump fetched project items raw data as JSON/
    """
    source_project = Project.get_or_create_project(
        number=source_project_number,
        account_type=account_type,
        account_name=account_name,
    )
    source_project.populate_field_ids_by_name()

    click.echo("-------------------------------------------------------")
    click.echo(json.dumps(source_project.field_ids_by_field_name, indent=2))
    click.echo("-------------------------------------------------------")
    click.echo(json.dumps(source_project.field_select_option_ids_by_field_and_option_name, indent=2))
    click.echo("-------------------------------------------------------")
    click.echo(json.dumps(source_project.field_iteration_ids_by_field_and_iteration_title, indent=2))
    click.echo("-------------------------------------------------------")
    items = source_project.get_items(with_full_content=True)
    click.echo(json.dumps(items, indent=2))


def copy_github_project_items_from_source_to_target(
    source_project_number: int,
    target_project_number: int,
    account_name:str,
    account_type: str="organization",
    max_copy=0,
):

    source_project = Project.get_or_create_project(
        number=source_project_number,
        account_type=account_type,
        account_name=account_name,
    )

    target_project = Project.get_or_create_project(
        number=target_project_number,
        account_type=account_type,
        account_name=account_name,
    )
    click.echo(f"Copying items from: {source_project.url} to {target_project.url} ")

    items = source_project.get_items()

    if max_copy:
        click.echo(f"Copying up to {max_copy} items.")
    else:
        click.echo(f"Copying all {len(items)} project items.")

    for i, item_data in enumerate(items):
        if max_copy and max_copy <= i:
            break

        if "content" not in item_data:
            click.echo(f"Skipping empty item.")
            continue
        content = item_data["content"]

        if "id" in content:

            # handle issues and PRs
            content_id = content["id"]
            new_item_id = target_project.create_item(content_id=content_id)

            item = Item.from_data(
                account_type=account_type,
                account_name=account_name,
                project_number=target_project_number,
                data=item_data
            )
            item.item_node_id = new_item_id

            click.echo(f"Created item with ID {content_id} in target project: {item.url}")

            target_project.set_fields(
                item_node_id=item.item_node_id,
                project_estimate=item.project_estimate,
                project_id=item.project_id,
                project_issue_id=item.project_issue_id,
                status=item.status,
                iteration=item.iteration,
                target_date=item.target_date,
            )

        else:
            # Handle draft issues
            draft_title = content["title"]
            draft_body = content["body"]
            new_item_id = target_project.create_draft_issue(title=draft_title, body=draft_body)
            click.echo(f"Created draft issue with title {draft_title!r} in target project.")

    click.echo("Project copy completed.")


if __name__ == "__main__":
    copy_github_project_items()
