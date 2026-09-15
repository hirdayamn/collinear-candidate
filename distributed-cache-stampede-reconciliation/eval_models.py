#!/usr/bin/env python3
"""
Direct Harbor SDK evaluation script for cache stampede task.
Runs model trials against Opus 4.7 and GPT-5.5-high via OpenRouter.
Bypasses Harbor CLI limitations.
"""

import asyncio
import os
import json
import subprocess
import tempfile
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

# Harbor SDK imports
try:
    from harbor import Harbor
    from harbor.agent import MiniSWEAgent
    from harbor.environment import DockerEnvironment
    from harbor.verifier import ScriptVerifier
except ImportError:
    print("Harbor SDK not fully available, using LiteLLM direct approach")
    Harbor = None

# LiteLLM for model calls
try:
    from litellm import acompletion
except ImportError:
    print("Installing litellm...")
    subprocess.run(["pip", "install", "litellm"], check=True)
    from litellm import acompletion


# ============================================================
# CONFIGURATION
# ============================================================

TASK_DIR = Path(__file__).parent
ENVIRONMENT_DIR = TASK_DIR / "environment"
INSTRUCTION_FILE = TASK_DIR / "instruction.md"
VERIFIER_SCRIPT = TASK_DIR / "tests/test.sh"

MODELS = [
    {
        "name": "opus-4.7",
        "model": "openrouter/anthropic/claude-opus-4.7",
        "temperature": 0.0,
        "max_tokens": 8192,
    },
    {
        "name": "gpt-5.5-high",
        "model": "openrouter/openai/gpt-5.5",
        "temperature": 0.0,
        "max_tokens": 8192,
    },
]

ATTEMPTS_PER_MODEL = 3


# ============================================================
# TASK INSTRUCTION LOADER
# ============================================================

def load_instruction() -> str:
    """Load the task instruction for the model."""
    with open(INSTRUCTION_FILE) as f:
        return f.read()


# ============================================================
# CLEANUP
# ============================================================

def cleanup_old_jobs(force: bool = False):
    """Remove old evaluation containers and images."""
    print("Cleaning up old evaluation containers and images...")

    # Find and remove eval containers
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=eval-", "--format", "{{.Names}}"],
        capture_output=True, text=True
    )
    for container in result.stdout.strip().split('\n'):
        if container:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            print(f"  Removed container: {container}")

    # Find and remove eval images
    result = subprocess.run(
        ["docker", "images", "--filter", "reference=cache-stampede-eval-*", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True, text=True
    )
    for image in result.stdout.strip().split('\n'):
        if image and image != "<none>:<none>":
            subprocess.run(["docker", "rmi", "-f", image], capture_output=True)
            print(f"  Removed image: {image}")

    # Remove dangling images
    if force:
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True)
        print("  Pruned dangling images")


# ============================================================
# DOCKER ENVIRONMENT MANAGEMENT
# ============================================================

def build_docker_image(tag: str) -> bool:
    """Build the Docker image for evaluation."""
    print(f"  Building Docker image: {tag}")
    result = subprocess.run(
        ["docker", "build", "-t", tag, str(ENVIRONMENT_DIR)],
        capture_output=True,
        text=True,
        timeout=300
    )
    if result.returncode != 0:
        print(f"  Build failed: {result.stderr[:500]}")
        return False
    print(f"  Build successful")
    return True


def run_container(tag: str, container_name: str) -> bool:
    """Run a container from the image."""
    # Clean up any existing container with same name
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    print(f"  Starting container: {container_name}")
    result = subprocess.run(
        ["docker", "run", "-d", "--name", container_name, tag],
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode != 0:
        print(f"  Container start failed: {result.stderr}")
        return False
    # Wait for Redis to be ready
    import time
    time.sleep(2)
    return True


def cleanup_container(container_name: str):
    """Stop and remove container."""
    subprocess.run(["docker", "stop", container_name], capture_output=True)
    subprocess.run(["docker", "rm", container_name], capture_output=True)


def copy_to_container(container_name: str, src: str, dst: str):
    """Copy file to container."""
    subprocess.run(["docker", "cp", src, f"{container_name}:{dst}"], check=True)


def exec_in_container(container_name: str, cmd: List[str]) -> subprocess.CompletedProcess:
    """Execute command in container."""
    full_cmd = ["docker", "exec", container_name] + cmd
    return subprocess.run(full_cmd, capture_output=True, text=True, timeout=120)


# ============================================================
# MODEL INTERACTION
# ============================================================

async def get_model_response(model_config: Dict, instruction: str, context: str = "") -> str:
    """Get response from model via LiteLLM/OpenRouter."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in environment")

    messages = [
        {"role": "system", "content": "You are an expert distributed systems engineer. Fix the cache stampede incident by modifying the provided Python files. Output the complete fixed file contents in markdown code blocks with file paths. Format each fix as:\n\n```python\n# /app/analytics_service/cache_manager.py\nimport ...\n```\n\n```python\n# /app/analytics_service/audit_reconciler.py\nimport ...\n```\n\n```python\n# /app/scripts/reconcile_data.py\nimport ...\n```"},
        {"role": "user", "content": f"{instruction}\n\n{context}"}
    ]

    print(f"  Calling {model_config['model']} via OpenRouter...")
    response = await acompletion(
        model=model_config["model"],
        messages=messages,
        temperature=model_config["temperature"],
        max_tokens=model_config["max_tokens"],
        api_base="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    content = response.choices[0].message.content
    print(f"  Response received: {len(content)} chars")
    return content


def parse_model_fixes(response: str) -> Dict[str, str]:
    """Parse model response to extract file fixes."""
    fixes = {}

    # DEBUG: Print raw response
    print(f"  DEBUG: Raw response length: {len(response)}")
    print(f"  DEBUG: First 500 chars:\n{response[:500]}")
    print(f"  DEBUG: Last 500 chars:\n{response[-500:]}")

    # Strategy 1: Look for markdown code blocks with file paths
    import re

    # Pattern: ```python\n# file.py\ncode\n```
    code_block_pattern = r'```(?:python|py)?\s*\n?(?:#\s*)?([/\w\-.]+\.py)\s*\n(.*?)\n```'
    matches = re.findall(code_block_pattern, response, re.DOTALL)
    for file_path, content in matches:
        file_path = file_path.strip()
        if not file_path.startswith('/'):
            file_path = '/app/' + file_path.lstrip('./')
        fixes[file_path] = content.strip()
        print(f"  Parsed via code block: {file_path}")

    # Strategy 2: Look for explicit file headers
    if not fixes:
        # Pattern: ### file.py or File: file.py
        header_pattern = r'(?:###|####|File:|file:)\s*([/\w\-.]+\.py)\s*\n(.*?)(?=\n(?:###|####|File:|file:)|$)'
        matches = re.findall(header_pattern, response, re.DOTALL)
        for file_path, content in matches:
            file_path = file_path.strip()
            if not file_path.startswith('/'):
                file_path = '/app/' + file_path.lstrip('./')
            # Extract code from content (might have code blocks)
            code_match = re.search(r'```(?:python|py)?\s*\n(.*?)\n```', content, re.DOTALL)
            if code_match:
                content = code_match.group(1)
            fixes[file_path] = content.strip()
            print(f"  Parsed via header: {file_path}")

    # Strategy 3: If still nothing, try to extract any python code blocks and map to known files
    if not fixes:
        known_files = [
            '/app/analytics_service/cache_manager.py',
            '/app/analytics_service/audit_reconciler.py',
            '/app/scripts/reconcile_data.py',
        ]
        code_blocks = re.findall(r'```(?:python|py)?\s*\n(.*?)\n```', response, re.DOTALL)
        for i, block in enumerate(code_blocks):
            if i < len(known_files):
                fixes[known_files[i]] = block.strip()
                print(f"  Parsed via fallback (block {i}): {known_files[i]}")

    print(f"  Total fixes parsed: {len(fixes)}")
    return fixes


# ============================================================
# APPLY FIXES TO CONTAINER
# ============================================================

def apply_fixes(container_name: str, fixes: Dict[str, str]) -> bool:
    """Apply model's fixes to container files."""
    for file_path, content in fixes.items():
        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            # Copy to container
            copy_to_container(container_name, temp_path, file_path)
            print(f"  Applied fix to {file_path}")
        except Exception as e:
            print(f"  Failed to apply {file_path}: {e}")
            return False
        finally:
            os.unlink(temp_path)

    return True


# ============================================================
# RUN VERIFIER
# ============================================================

def run_verifier(container_name: str) -> Dict[str, Any]:
    """Run the test verifier in container."""
    print(f"  Running verifier...")
    result = exec_in_container(container_name, ["bash", "/app/tests/test.sh"])

    # Read reward file
    reward_result = exec_in_container(container_name, ["cat", "/logs/verifier/reward.txt"])
    reward = 1 if reward_result.stdout.strip() == "1" else 0

    return {
        "passed": reward == 1,
        "reward": reward,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    }


# ============================================================
# RUN SINGLE TRIAL
# ============================================================

async def run_trial(model_config: Dict, attempt: int) -> Dict[str, Any]:
    """Run a single evaluation trial for a model."""
    model_name = model_config["name"]
    container_name = f"eval-{model_name}-{attempt}"
    image_tag = f"cache-stampede-eval-{model_name}-{attempt}"

    print(f"\n{'='*60}")
    print(f"TRIAL: {model_name} attempt {attempt+1}/{ATTEMPTS_PER_MODEL}")
    print(f"{'='*60}")

    trial_result = {
        "model": model_name,
        "attempt": attempt + 1,
        "timestamp": datetime.now().isoformat(),
        "reward": 0,
        "passed": False,
        "error": None,
        "fixes_applied": [],
    }

    try:
        # Build image
        if not build_docker_image(image_tag):
            trial_result["error"] = "Docker build failed"
            return trial_result

        # Start container
        if not run_container(image_tag, container_name):
            trial_result["error"] = "Container start failed"
            return trial_result

        # Load instruction
        instruction = load_instruction()

        # Get model response
        print(f"  Calling {model_config['model']} via OpenRouter...")
        response = await get_model_response(model_config, instruction)

        # Parse fixes
        fixes = parse_model_fixes(response)
        if not fixes:
            print(f"  No valid fixes parsed from model response")
            trial_result["error"] = "No valid fixes parsed"
            return trial_result

        trial_result["fixes_applied"] = list(fixes.keys())
        print(f"  Parsed {len(fixes)} file fixes")

        # Apply fixes
        if not apply_fixes(container_name, fixes):
            trial_result["error"] = "Failed to apply fixes"
            return trial_result

        # Run verifier
        verifier_result = run_verifier(container_name)
        trial_result.update(verifier_result)

        print(f"  Result: {'PASSED' if verifier_result['passed'] else 'FAILED'} (reward={verifier_result['reward']})")

    except Exception as e:
        trial_result["error"] = str(e)
        print(f"  Trial error: {e}")

    finally:
        cleanup_container(container_name)
        # Clean up image
        subprocess.run(["docker", "rmi", image_tag], capture_output=True)

    return trial_result


# ============================================================
# MAIN EVALUATION LOOP
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="Run Harbor model evaluation for cache stampede task")
    parser.add_argument("--cleanup", action="store_true", help="Clean up old containers/images before running")
    parser.add_argument("--force-cleanup", action="store_true", help="Force cleanup including dangling images")
    parser.add_argument("--attempts", type=int, default=ATTEMPTS_PER_MODEL, help="Attempts per model")
    parser.add_argument("--model", action="append", choices=["opus-4.7", "gpt-5.5-high"], help="Specific model(s) to run")
    args = parser.parse_args()

    # Check API key
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
    if not OPENROUTER_API_KEY:
        print("ERROR: Set OPENROUTER_API_KEY environment variable")
        return

    # Cleanup if requested
    if args.cleanup or args.force_cleanup:
        cleanup_old_jobs(force=args.force_cleanup)

    # Filter models if specified
    models_to_run = MODELS
    if args.model:
        models_to_run = [m for m in MODELS if m["name"] in args.model]
        if not models_to_run:
            print(f"ERROR: No valid models selected from {args.model}")
            return

    attempts = args.attempts

    print("=" * 60)
    print("HARBOR MODEL EVALUATION: Cache Stampede Reconciliation")
    print("=" * 60)
    print(f"Models: {[m['name'] for m in models_to_run]}")
    print(f"Attempts per model: {attempts}")
    print(f"Total trials: {len(models_to_run) * attempts}")
    print(f"Task: {TASK_DIR}")

    all_results = []

    for model_config in models_to_run:
        for attempt in range(attempts):
            result = await run_trial(model_config, attempt)
            all_results.append(result)

            # Small delay between trials
            await asyncio.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    for model_config in models_to_run:
        model_name = model_config["name"]
        model_results = [r for r in all_results if r["model"] == model_name]
        passed = sum(1 for r in model_results if r["passed"])
        total = len(model_results)
        print(f"\n{model_name}: {passed}/{total} passed")
        for r in model_results:
            status = "✓" if r["passed"] else "✗"
            error = f" - {r['error']}" if r["error"] else ""
            print(f"  Attempt {r['attempt']}: {status}{error}")

    # Save results with detailed failure info
    results_file = TASK_DIR / f"eval_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_file, 'w') as f:
        json.dump({
            "task": "distributed-cache-stampede-reconciliation",
            "timestamp": datetime.now().isoformat(),
            "models": [m["name"] for m in models_to_run],
            "attempts_per_model": attempts,
            "results": all_results
        }, f, indent=2)

    print(f"\nResults saved to: {results_file}")

    # Overall pass rate
    total_passed = sum(1 for r in all_results if r["passed"])
    total_trials = len(all_results)
    print(f"\nOverall: {total_passed}/{total_trials} trials passed ({100*total_passed/total_trials:.1f}%)")

    # Print failure details
    failed = [r for r in all_results if not r["passed"]]
    if failed:
        print("\n" + "=" * 60)
        print("FAILURE DETAILS")
        print("=" * 60)
        for r in failed:
            print(f"\n{r['model']} attempt {r['attempt']}:")
            print(f"  Error: {r.get('error', 'Test failed')}")
            print(f"  Fixes applied: {r.get('fixes_applied', 'none')}")
            if r.get('stderr'):
                print(f"  Stderr: {r['stderr'][:300]}")
            if r.get('stdout'):
                print(f"  Stdout: {r['stdout'][:300]}")


if __name__ == "__main__":
    asyncio.run(main())