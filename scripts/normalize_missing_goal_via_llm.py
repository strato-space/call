import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple, Optional

# Ensure local project package 'call' is importable when running as a script
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from call.lib import api as call_api


def list_missing() -> Tuple[List[Path], List[Path]]:
    """Reuse the listing logic by invoking the helper module (import as a lib)."""
    # We import and reuse the function by spawning the script to keep a single source of truth
    import subprocess, sys
    cmd = [sys.executable, str(ROOT / 'call' / 'scripts' / 'list_missing_goal.py'), '--repos', 'agent', 'prompt']
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"list_missing_goal.py failed: {p.stderr}")
    data = json.loads(p.stdout)
    missing = [Path(x) for x in data.get('missing_goal', [])]
    no_meta = [Path(x) for x in data.get('no_metadata', [])]
    return missing, no_meta


def normalize_file_via_llm(path: Path, *, project_name: str = 'AgentFab', agent_name: str = 'StratoFormater') -> Optional[str]:
    """Call StratoFormater on a given file path. Returns final_output (formatted text) or None on failure."""
    try:
        notes = (
            "Normalize to Strato Prompt Framework. Create METADATA/PROMPT blocks if missing. "
            "Extract text under '# Goal' from PROMPT and insert it as the first 'goal: |-' attribute in METADATA. "
            "Keep only control-flow/runtime parameters in METADATA (model, temperature, top_p, engine, provider, tg, io, memory, chain, mcp, workdir[s], prompts, id). "
            "Ensure PROMPT contains only the text intended for LLM (no control params). Preserve original content otherwise."
        )
        # Convert Windows absolute path to posix path for MCP FS (WORKDIR=.)
        posix_path: Optional[str] = None
        try:
            rel = path.resolve().relative_to(ROOT)
            posix_path = "./" + rel.as_posix()
        except Exception:
            posix_path = None

        payload_obj = {"path": (posix_path or str(path)), "notes": notes}
        payload = json.dumps(payload_obj, ensure_ascii=False)
        res = call_api.call(project=project_name, agent=agent_name, input=payload)
        if isinstance(res, dict) and res.get('ok') and res.get('final_output'):
            return res['final_output']
        # Fallback: try sending raw text content
        try:
            text = path.read_text(encoding='utf-8')
        except Exception:
            text = ''
        if text:
            # Send plain text (agent treats free text as fallback input)
            res2 = call_api.call(project=project_name, agent=agent_name, input=text)
            if isinstance(res2, dict) and res2.get('ok') and res2.get('final_output'):
                return res2['final_output']
        return None
    except Exception as e:
        try:
            import sys as _sys
            print(f"normalize_file_via_llm error for {path}: {e}", file=_sys.stderr)
        except Exception:
            pass
        return None


def main():
    ap = argparse.ArgumentParser(description='Normalize files missing goal via StratoFormater (LLM)')
    ap.add_argument('--limit', type=int, default=10, help='Limit number of files to process (missing-goal only)')
    ap.add_argument('--apply', action='store_true', help='Write changes back to files (in-place)')
    ap.add_argument('--include-no-metadata', action='store_true', help='Also process files without METADATA (LLM will create sections)')
    args = ap.parse_args()

    # Ensure repository indices are up-to-date
    try:
        call_api.reload(repos=['agent', 'prompt'])
    except Exception:
        pass

    missing, no_meta = list_missing()
    targets: List[Path] = missing[: args.limit]
    if args.include_no_metadata:
        targets += no_meta[: max(0, args.limit - len(targets))]

    report = {
        'ok': True,
        'planned': [str(p) for p in targets],
        'applied': [],
        'failed': [],
        'errors': {},
    }

    for p in targets:
        out = normalize_file_via_llm(p)
        if out:
            if args.apply:
                try:
                    p.write_text(out, encoding='utf-8')
                    report['applied'].append(str(p))
                except Exception:
                    report['failed'].append(str(p))
            else:
                report['applied'].append(str(p))
        else:
            report['failed'].append(str(p))
            # Try to fetch a structured error by calling once more to capture envelope (no write)
            try:
                payload_probe = json.dumps({"path": str(p)}, ensure_ascii=False)
                res_probe = call_api.call(project='AgentFab', agent='StratoFormater', input=payload_probe)
                if isinstance(res_probe, dict) and not res_probe.get('ok'):
                    report['errors'][str(p)] = {
                        'code': res_probe.get('code'),
                        'error_code': res_probe.get('error_code'),
                        'description': res_probe.get('description'),
                    }
            except Exception:
                pass

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
