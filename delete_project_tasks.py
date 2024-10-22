import os
import time
import signal
import sys
import logging
from requests.exceptions import RequestException
from datetime import datetime
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()

# Rate limiter settings
RATE_LIMIT_MAX_REQUESTS = 80
RATE_LIMIT_TIME_FRAME = 60

# GitHub API settings
GITHUB_TOKEN = os.getenv("GITHUB_PAT_HASHLENS")
REPO_NAME = "HashLens/HashLens"
API_BASE_URL = "https://api.github.com"
PROJECT_NUMBER = 1

# Set up logging
log_file = os.path.join('logs', f'delete_project_tasks_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
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

def get_project_items(project_id):
    query = """
    query($project_id:ID!, $cursor:String) {
      node(id: $project_id) {
        ... on ProjectV2 {
          items(first: 100, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              content {
                ... on Issue {
                  title
                }
              }
            }
          }
        }
      }
    }
    """
    items = []
    cursor = None
    while True:
        variables = {
            "project_id": project_id,
            "cursor": cursor
        }
        try:
            response = make_request("POST", f"{API_BASE_URL}/graphql", json={"query": query, "variables": variables})
            response.raise_for_status()
            data = response.json()
            if 'errors' in data:
                logger.error(f"GraphQL Error: {data['errors']}")
                return None
            
            new_items = data['data']['node']['items']['nodes']
            items.extend(new_items)
            
            page_info = data['data']['node']['items']['pageInfo']
            if not page_info['hasNextPage']:
                break
            cursor = page_info['endCursor']
        except (RequestException, KeyError) as e:
            logger.error(f"Failed to get project items: {str(e)}")
            logger.debug(f"Response content: {response.text}")
            return None
    return items

def delete_project_item(project_id, item_id):
    mutation = """
    mutation($project_id:ID!, $item_id:ID!) {
      deleteProjectV2Item(input: {projectId: $project_id, itemId: $item_id}) {
        deletedItemId
      }
    }
    """
    variables = {
        "project_id": project_id,
        "item_id": item_id
    }
    try:
        response = make_request("POST", f"{API_BASE_URL}/graphql", json={"query": mutation, "variables": variables})
        response.raise_for_status()
        data = response.json()
        if 'errors' in data:
            logger.error(f"GraphQL Error: {data['errors']}")
            return False
        return True
    except RequestException as e:
        logger.error(f"Failed to delete project item: {str(e)}")
        logger.debug(f"Response content: {response.text}")
        return False

def get_all_repo_issues():
    logger.info("Fetching all issues from the repository")
    issues = []
    page = 1
    per_page = 100
    while True:
        url = f"{API_BASE_URL}/repos/{REPO_NAME}/issues"
        params = {
            "state": "all",
            "per_page": per_page,
            "page": page
        }
        try:
            response = make_request("GET", url, params=params)
            response.raise_for_status()
            new_issues = response.json()
            if not new_issues:
                break
            issues.extend(new_issues)
            page += 1
        except RequestException as e:
            logger.error(f"Failed to fetch issues: {str(e)}")
            return None
    return issues

def delete_issue(issue_number):
    url = f"{API_BASE_URL}/repos/{REPO_NAME}/issues/{issue_number}"
    try:
        response = make_request("PATCH", url, json={"state": "closed"})
        response.raise_for_status()
        return True
    except RequestException as e:
        logger.error(f"Failed to delete issue {issue_number}: {str(e)}")
        return False

def delete_all_issues():
    logger.info("Starting to delete all issues from the repository")
    issues = get_all_repo_issues()
    if issues is None:
        logger.error("Failed to get repository issues. Exiting.")
        return

    total_issues = len(issues)
    logger.info(f"Found {total_issues} issues in the repository")

    for index, issue in enumerate(issues, start=1):
        issue_number = issue['number']
        issue_title = issue['title']
        logger.info(f"Deleting issue {index}/{total_issues}: {issue_title} (#{issue_number})")
        if delete_issue(issue_number):
            logger.info(f"Successfully deleted issue: {issue_title} (#{issue_number})")
        else:
            logger.error(f"Failed to delete issue: {issue_title} (#{issue_number})")

    logger.info("Finished deleting all repository issues")

def delete_all_project_items_and_issues():
    logger.info("Starting to delete all project items and repository issues")
    
    # Delete project items
    project_id = get_project_id()
    if project_id:
        items = get_project_items(project_id)
        if items:
            total_items = len(items)
            logger.info(f"Found {total_items} items in the project")
            for index, item in enumerate(items, start=1):
                item_id = item['id']
                item_title = item['content']['title'] if item['content'] else "Unknown"
                logger.info(f"Deleting project item {index}/{total_items}: {item_title}")
                if delete_project_item(project_id, item_id):
                    logger.info(f"Successfully deleted project item: {item_title}")
                else:
                    logger.error(f"Failed to delete project item: {item_title}")
        else:
            logger.info("No items found in the project")
    else:
        logger.error("Failed to get project ID. Skipping project item deletion.")

    # Delete all repository issues
    delete_all_issues()

    logger.info("Finished deleting all project items and repository issues")

if __name__ == "__main__":
    logger.debug("Script started")
    start_time = time.time()
    if not GITHUB_TOKEN:
        logger.error("GitHub token not set. Please set the GITHUB_PAT_HASHLENS environment variable.")
    else:
        try:
            delete_all_project_items_and_issues()
        except KeyboardInterrupt:
            logger.info("Script interrupted. Exiting gracefully...")
            sys.exit(0)
    end_time = time.time()
    logger.debug(f"Script completed in {end_time - start_time:.2f} seconds")
