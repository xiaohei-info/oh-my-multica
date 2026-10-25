"""Remove integration-test stacks left behind by killed runs: python tests/it/cleanup.py [max_age_hours=2]."""
import json
import subprocess
import sys
import time

max_age = float(sys.argv[1]) * 3600 if len(sys.argv) > 1 else 2 * 3600


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True).stdout.split()


projects = {}
for cid in docker("ps", "-aq", "--filter", "label=omac.it=1"):
    info = json.loads(subprocess.run(["docker", "inspect", cid], capture_output=True, text=True).stdout)[0]
    project = info["Config"]["Labels"].get("com.docker.compose.project", "")
    created = time.mktime(time.strptime(info["Created"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    if project.startswith("omac-it-") and time.time() - created > max_age:
        projects[project] = True
for project in projects:
    subprocess.run(["docker", "compose", "-p", project, "down", "-v", "--remove-orphans"], capture_output=True)
    print("removed", project)
