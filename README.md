# GitHub Projects Task Uploader

### Description

GitHub Projects Task Uploader is a simple automation tool designed to load tasks from a CSV file into a GitHub repository. This script reads project tasks from a CSV file and creates corresponding GitHub issues, making it easier to manage and track tasks directly in GitHub's project management system. Ideal for project teams using GitHub for task management, it streamlines the process of transferring task details to GitHub.

### Features

- Bulk upload tasks from CSV to GitHub as issues.
- Automatically assign labels such as priority and status.
- Set assignees for each task.
- Customize issue details such as body, title, and labels.

### Prerequisites

- Python 3.x installed on your system.
- A GitHub personal access token with `repo` permissions.
- A CSV file containing task information to upload.

### CSV File Format

The CSV file should contain the following columns:

- `Title`: The title of the task to be used as the GitHub issue title.
- `Assignee`: The assignee for the GitHub issue (use "Self" to assign to no one).
- `Status`: Status of the task (e.g., "Todo", "In Progress").
- `Priority`: Priority of the task (e.g., "P0", "P1").
- `Estimate`: Estimated time for the task in hours.
- `Size`: Size of the task (e.g., "S", "M", "L").
- `Iteration`: The iteration period for the task.

### Installation and Setup

1. Clone the repository:
   ```sh
   git clone https://github.com/HashLens/HashLens.git
