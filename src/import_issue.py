import csv
import requests
import os
import time
import signal
import sys
import logging
from requests.exceptions import RequestException
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Rate limiter settings
# Maximum number of requests allowed within the time frame
RATE_LIMIT_MAX_REQUESTS = 60
# Time frame for rate limiting in seconds
RATE_LIMIT_TIME_FRAME = 60

# GitHub API settings
GITHUB_TOKEN = os.getenv("GITHUB_PAT_HASHLENS")
REPO_NAME = "HashLens/HashLens"
API_BASE_URL = "https://api.github.com"
PROJECT_NUMBER = 1

# CSV file path
CSV_FILE_PATH = "tasks.csv"

# Create logs directory if it doesn't exist
if not os.path.exists('logs'):
    os.makedirs('logs')

# Set up logging
log_file = os.path.join('logs', f'github_project_uploader_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Enable debug logging if DEBUG is set to 'ON' in .env file
if os.getenv('DEBUG', 'OFF').upper() == 'ON':
    logger.setLevel(logging.DEBUG)

auth_headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

class RateLimiter:
    def __init__(self, max_requests, time_frame):
        self.max_requests = max_requests
        self.time_frame = time_frame
        self.requests = []

    def wait(self):
        now = time.time()
        if len(self.requests) >= self.max_requests:
            wait_time = self.time_frame - (now - self.requests[0])
            if wait_time > 0:
                logger.debug(f"Rate limiter waiting for {wait_time:.2f} seconds")
                time.sleep(wait_time)
            self.requests = [r for r in self.requests if now - r <= self.time_frame]
        self.requests.append(now)

# Initialize rate limiter with the settings defined at the top of the script
rate_limiter = RateLimiter(max_requests=RATE_LIMIT_MAX_REQUESTS, time_frame=RATE_LIMIT_TIME_FRAME)

def signal_handler(sig, frame):
    logger.info("Script interrupted. Exiting gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def make_request(method, url, **kwargs):
    logger.debug(f"Starting API call to {url}")
    start_wait = time.time()
    rate_limiter.wait()
    end_wait = time.time()
    logger.debug(f"Rate limiter wait time: {end_wait - start_wait:.2f} seconds")
    
    start_time = time.time()
    response = requests.request(method, url, headers=auth_headers, **kwargs)
    end_time = time.time()
    
    logger.debug(f"API call to {url} took {end_time - start_time:.2f} seconds")
    
    handle_rate_limit(response)
    check_rate_limit_status(response)
    return response

def create_github_issue(title, body, assignee, labels, row):
    logger.debug(f"Starting to create issue: {title}")
    start_time = time.time()
    api_url = f"{API_BASE_URL}/repos/{REPO_NAME}/issues"
    issue_data = {
        "title": title,
        "body": body,
        "assignee": assignee,
        "labels": labels
    }
    try:
        response = make_request("POST", api_url, json=issue_data)
        response.raise_for_status()
        issue = response.json()
        logger.info(f"Issue '{title}' created successfully.")
        add_issue_to_project(issue['node_id'], row)
        end_time = time.time()
        logger.debug(f"Issue creation took {end_time - start_time:.2f} seconds")
        return True
    except RequestException as e:
        logger.error(f"Failed to create issue '{title}': {str(e)}")
        return False

def add_issue_to_project(issue_node_id, row):
    logger.debug(f"Starting to add issue to project")
    start_time = time.time()
    project_id = get_project_id()
    if not project_id:
        logger.error("Failed to add issue to project: Could not retrieve project ID")
        return

    add_item_mutation = """
    mutation($project:ID!, $issue:ID!) {
      addProjectV2ItemById(input: {projectId: $project, contentId: $issue}) {
        item {
          id
        }
      }
    }
    """
    add_variables = {
        "project": project_id,
        "issue": issue_node_id
    }
    
    try:
        response = make_request("POST", f"{API_BASE_URL}/graphql", json={"query": add_item_mutation, "variables": add_variables})
        response.raise_for_status()
        data = response.json()
        if 'errors' in data:
            logger.error(f"Failed to add issue to project: {data['errors']}")
            return
        item_id = data['data']['addProjectV2ItemById']['item']['id']
        logger.info("Issue added to project successfully.")
        
        # Update item fields
        update_fields(item_id, row)
        end_time = time.time()
        logger.debug(f"Adding issue to project took {end_time - start_time:.2f} seconds")
    except RequestException as e:
        logger.error(f"Failed to add issue to project: {str(e)}")
        logger.debug(f"Response content: {response.text}")

def update_fields(item_id, row):
    logger.debug(f"Starting to update fields for item {item_id}")
    start_time = time.time()
    for field_name, value in row.items():
        if field_name in project_fields:
            field = project_fields[field_name]
            if field['__typename'] == 'ProjectV2SingleSelectField':
                option = next((opt for opt in field['options'] if opt['name'].lower() == value.lower()), None)
                if option:
                    update_single_select_field(item_id, field['id'], option['id'])
            elif field['__typename'] == 'ProjectV2Field':
                if field['dataType'] == 'NUMBER':
                    update_number_field(item_id, field['id'], value)
                else:
                    update_text_field(item_id, field['id'], value)
    end_time = time.time()
    logger.debug(f"Updating fields took {end_time - start_time:.2f} seconds")

def update_single_select_field(item_id, field_id, option_id):
    mutation = """
    mutation($project:ID!, $item:ID!, $field:ID!, $value:String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $project
        itemId: $item
        fieldId: $field
        value: {
          singleSelectOptionId: $value
        }
      }) {
        projectV2Item {
          id
        }
      }
    }
    """
    variables = {
        "project": get_project_id(),
        "item": item_id,
        "field": field_id,
        "value": option_id
    }
    execute_field_update(mutation, variables)

def update_text_field(item_id, field_id, value):
    mutation = """
    mutation($project:ID!, $item:ID!, $field:ID!, $value:String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $project
        itemId: $item
        fieldId: $field
        value: {
          text: $value
        }
      }) {
        projectV2Item {
          id
        }
      }
    }
    """
    variables = {
        "project": get_project_id(),
        "item": item_id,
        "field": field_id,
        "value": value
    }
    execute_field_update(mutation, variables)

def execute_field_update(mutation, variables):
    try:
        response = make_request("POST", f"{API_BASE_URL}/graphql", json={"query": mutation, "variables": variables})
        response.raise_for_status()
        data = response.json()
        if 'errors' in data:
            logger.error(f"Failed to update field: {data['errors']}")
        else:
            logger.info(f"Field updated successfully.")
    except RequestException as e:
        logger.error(f"Failed to update field: {str(e)}")

def get_project_id():
    query = """
    query($owner:String!, $number:Int!) {
      organization(login: $owner) {
        projectV2(number: $number) {
          id
        }
      }
    }
    """
    variables = {
        "owner": REPO_NAME.split('/')[0],
        "number": PROJECT_NUMBER
    }
    try:
        response = make_request("POST", f"{API_BASE_URL}/graphql", json={"query": query, "variables": variables})
        response.raise_for_status()
        data = response.json()
        if 'errors' in data:
            logger.error(f"GraphQL Error: {data['errors']}")
            return None
        return data['data']['organization']['projectV2']['id']
    except (RequestException, KeyError) as e:
        logger.error(f"Failed to get project ID: {str(e)}")
        logger.debug(f"Response content: {response.text}")
        return None

def check_existing_issue(title):
    api_url = f"{API_BASE_URL}/repos/{REPO_NAME}/issues"
    params = {"state": "all"}
    try:
        response = make_request("GET", api_url, params=params)
        response.raise_for_status()
        issues = response.json()
        return any(issue['title'] == title for issue in issues)
    except RequestException as e:
        logger.error(f"Failed to check for existing issue: {str(e)}")
        return None

def handle_rate_limit(response):
    if response.status_code == 403 or response.status_code == 429:
        reset_time = int(response.headers.get('x-ratelimit-reset', 0))
        current_time = int(time.time())
        sleep_time = max(reset_time - current_time, 60)
        logger.info(f"Rate limit exceeded. Waiting for {sleep_time} seconds before retrying.")
        time.sleep(sleep_time)
    elif 400 <= response.status_code < 600:
        logger.error(f"Error: {response.status_code} - {response.text}")
        raise RequestException(f"HTTP error {response.status_code}")

def check_rate_limit_status(response):
    limit = response.headers.get('x-ratelimit-limit')
    remaining = response.headers.get('x-ratelimit-remaining')
    used = response.headers.get('x-ratelimit-used')
    reset = response.headers.get('x-ratelimit-reset')
    resource = response.headers.get('x-ratelimit-resource')

    if all([limit, remaining, used, reset, resource]):
        reset_time = datetime.fromtimestamp(int(reset)).strftime('%Y-%m-%d %H:%M:%S')
        logger.info("\nRate Limit Status:")
        logger.info(f"  Limit: {limit}")
        logger.info(f"  Remaining: {remaining}")
        logger.info(f"  Used: {used}")
        logger.info(f"  Reset Time: {reset_time}")
        logger.info(f"  Resource: {resource}")
    else:
        logger.info("Rate limit information not available in the response headers.")

def load_tasks_from_csv(csv_file_path):
    logger.debug(f"Starting to load tasks from CSV: {csv_file_path}")
    global project_fields
    project_fields = get_project_fields()
    if not project_fields:
        logger.error("Failed to retrieve project fields. Exiting.")
        logger.debug(f"Project fields: {project_fields}")
        return

    with open(csv_file_path, mode='r') as file:
        csv_reader = csv.DictReader(file)
        for row in csv_reader:
            title = row["Title"]
            if check_existing_issue(title) is True:
                logger.info(f"Issue '{title}' already exists. Skipping.")
                continue
            elif check_existing_issue(title) is None:
                logger.info("Failed to check for existing issue. Retrying.")
                time.sleep(5)  # Wait 5 seconds before retrying
                continue

            assignee = row["Assignee"] if row["Assignee"].lower() != "self" else None
            status = row["Status"]
            priority = row["Priority"]
            estimate = row["Estimate"]
            size = row["Size"]
            iteration = row["Iteration"]
            
            body = (
                f"**Status**: {status}\n"
                f"**Priority**: {priority}\n"
                f"**Estimate**: {estimate} hours\n"
                f"**Size**: {size}\n"
                f"**Iteration**: {iteration}\n"
            )
            labels = [status, priority]
            
            if not create_github_issue(title, body, assignee, labels, row):
                logger.info(f"Failed to create issue '{title}'. Retrying in 60 seconds.")
                time.sleep(60)
    end_time = time.time()
    logger.debug(f"Loading tasks from CSV took {end_time - start_time:.2f} seconds")

def validate_repo():
    api_url = f"{API_BASE_URL}/repos/{REPO_NAME}"
    try:
        response = make_request("GET", api_url)
        response.raise_for_status()
        repo_data = response.json()
        logger.info(f"Connected to repository: {repo_data['full_name']}")
        logger.info(f"Owner: {repo_data['owner']['login']}")
        logger.info(f"Repository type: {'Organization' if repo_data['owner']['type'] == 'Organization' else 'User'}")
        return True
    except RequestException as e:
        logger.error(f"Error validating repository: {str(e)}")
        return False

def get_project_fields():
    query = """
    query($owner:String!, $number:Int!) {
      organization(login: $owner) {
        projectV2(number: $number) {
          fields(first:20) {
            nodes {
              ... on ProjectV2Field {
                id
                name
                __typename
                dataType
              }
              ... on ProjectV2SingleSelectField {
                id
                name
                options {
                  id
                  name
                }
                __typename
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "owner": REPO_NAME.split('/')[0],
        "number": PROJECT_NUMBER
    }
    try:
        response = make_request("POST", f"{API_BASE_URL}/graphql", json={"query": query, "variables": variables})
        response.raise_for_status()
        data = response.json()
        if 'errors' in data:
            logger.error(f"GraphQL Error: {data['errors']}")
            return None
        fields = data['data']['organization']['projectV2']['fields']['nodes']
        
        logger.debug("Debug: All fields:")
        for field in fields:
            logger.debug(f"Field: {field}")
        
        parsed_fields = {}
        for field in fields:
            if 'name' in field and 'id' in field:
                parsed_fields[field['name']] = field
            else:
                logger.warning(f"Skipping field due to missing 'name' or 'id': {field}")
        
        return parsed_fields
    except (RequestException, KeyError) as e:
        logger.error(f"Failed to get project fields: {str(e)}")
        logger.debug(f"Response content: {response.text}")
        return None

def update_number_field(item_id, field_id, value):
    mutation = """
    mutation($project:ID!, $item:ID!, $field:ID!, $value:Float!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $project
        itemId: $item
        fieldId: $field
        value: {
          number: $value
        }
      }) {
        projectV2Item {
          id
        }
      }
    }
    """
    variables = {
        "project": get_project_id(),
        "item": item_id,
        "field": field_id,
        "value": float(value)
    }
    execute_field_update(mutation, variables)

if __name__ == "__main__":
    logger.debug("Script started")
    start_time = time.time()
    if not GITHUB_TOKEN:
        logger.error("GitHub token not set. Please set the GITHUB_PAT_HASHLENS environment variable.")
    elif not validate_repo():
        logger.error("Unable to validate repository. Please check your repository name and permissions.")
    else:
        try:
            load_tasks_from_csv(CSV_FILE_PATH)
        except KeyboardInterrupt:
            logger.info("Script interrupted. Exiting gracefully...")
            sys.exit(0)
    end_time = time.time()
    logger.debug(f"Script completed in {end_time - start_time:.2f} seconds")
