#!/usr/bin/env python
import sys
import os
from pathlib import Path
# Add parent directory to path so 'call' module is importable
_repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(_repo_root))

# Set up environment for repos
os.environ.setdefault("PROMPT_REPO", str(_repo_root / "prompt"))
os.environ.setdefault("AGENT_REPO", str(_repo_root / "agent"))

from call.lib import repo_fs, repo_db

# Reload the database
print("Reloading repo...")
result = repo_fs.reload()
print(f"Reload result: {result}")

# Query for UxFab project card - should have 2 entries (type=project and type=prompt)
print("\n=== Checking UxFab project card ===")
conn = repo_db._ensure_db()
cur = conn.cursor()
cur.execute("SELECT target, project, agent, prompt, type, state FROM repo WHERE target = 'UxFab' ORDER BY type, prompt")
rows = cur.fetchall()
print(f"Found {len(rows)} rows:")
for row in rows:
    print(f"  {row}")
cur.close()
conn.close()

# Query for AgentFab - should have 1 entry (type=project only)
print("\n=== Checking AgentFab entries ===")
conn = repo_db._ensure_db()
cur = conn.cursor()
cur.execute("SELECT target, project, agent, prompt, type, state, rel_path FROM repo WHERE target = 'AgentFab' OR project = 'AgentFab' ORDER BY type, target")
rows = cur.fetchall()
print(f"Found {len(rows)} rows:")
for row in rows:
    print(f"  {row}")
cur.close()
conn.close()

# Query for StratoProject
print("\n=== Checking StratoProject entries ===")
conn = repo_db._ensure_db()
cur = conn.cursor()
cur.execute("SELECT target, project, agent, prompt, type, rel_path FROM repo WHERE project = 'StratoProject' ORDER BY type, target")
rows = cur.fetchall()
for row in rows:
    print(f"  {row}")
cur.close()
conn.close()

# Query for 50-DiscoveryAgent (duplicate check)
print("\n=== Checking 50-DiscoveryAgent ===")
conn = repo_db._ensure_db()
cur = conn.cursor()
cur.execute("SELECT target, project, agent, prompt, type, state, rel_path FROM repo WHERE target = '50-DiscoveryAgent' ORDER BY type")
rows = cur.fetchall()
print(f"Found {len(rows)} rows:")
for row in rows:
    print(f"  {row}")

# Check cascading lookup
print("\n=== Testing cascading lookup for 50-DiscoveryAgent ===")
cur.execute("SELECT target, project, agent, prompt, type FROM repo WHERE project = 'AgentFab' AND target = '50-DiscoveryAgent' AND type = 'project'")
print(f"  project match: {cur.fetchall()}")
cur.execute("SELECT target, project, agent, prompt, type FROM repo WHERE project = 'AgentFab' AND target = '50-DiscoveryAgent' AND type = 'agent'")
print(f"  agent match: {cur.fetchall()}")
cur.execute("SELECT target, project, agent, prompt, type FROM repo WHERE project = 'AgentFab' AND target = '50-DiscoveryAgent' AND type = 'prompt'")
print(f"  prompt match: {cur.fetchall()}")

# Test kind="agent" filter (prompt = '' AND agent != '')
print("\n=== Testing kind=agent filter ===")
cur.execute("SELECT target, project, agent, prompt, type FROM repo WHERE 1=1 AND prompt = '' AND agent != '' AND project = 'AgentFab' AND target = '50-DiscoveryAgent'")
rows = cur.fetchall()
print(f"  Found {len(rows)} rows with kind='agent' filters: {rows}")

# Test kind="prompt" filter (prompt != '')
print("\n=== Testing kind=prompt filter ===")
cur.execute("SELECT target, project, agent, prompt, type FROM repo WHERE 1=1 AND prompt != '' AND project = 'AgentFab' AND target = '50-DiscoveryAgent'")
rows = cur.fetchall()
print(f"  Found {len(rows)} rows with kind='prompt' filters: {rows}")

cur.close()
conn.close()

# Test actual API call
print("\n=== Testing actual API call ===")
try:
    from call.lib import api
    row = api.interpret_target(project="AgentFab", agent=None, prompt=None, target="50-DiscoveryAgent")
    print(f"  SUCCESS: {row.target}, {row.type}, {row.agent}, {row.prompt}")
except Exception as e:
    print(f"  ERROR: {e}")

# Test StratoProject resolution
print("\n=== Testing StratoProject resolution ===")
try:
    row = api.interpret_target(project=None, agent=None, prompt=None, target="StratoProject")
    print(f"  Type: {row.type}")
    print(f"  Agent: {row.agent}")
    print(f"  Prompt: {row.prompt}")
    print(f"  Card length: {len(row.card or '')}")
    print(f"  Path: {row.path}")
except Exception as e:
    print(f"  ERROR: {e}")
