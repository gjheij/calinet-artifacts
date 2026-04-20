from importlib.resources import files
import subprocess
import sys

def main():
    script_path = files("calinet_artifacts") / "scripts" / "calinet_artifacts_batch.ps1"

    result = subprocess.run(
        [
            "powershell",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script_path),
            *sys.argv[1:],
        ]
    )
    raise SystemExit(result.returncode)